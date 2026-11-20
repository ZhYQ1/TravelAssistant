"""多方案审核编排：读取同版本完整方案，生成可解释的整改优先级。"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Sequence

from app.plan.models import PlanReference
from app.plan.store import (
    PlanStore,
    PlanStoreError,
)
from app.observability.metrics import MetricsRegistry
from app.review.models import Dimension, ReviewIssue, ReviewOutcome, ReviewReport
from app.review.rules import IssueDraft, ReviewRuleEngine
from app.review.state import (
    ReviewCancelled,
    ReviewState,
    ReviewStateError,
    ReviewStateMachine,
    ReviewStateStore,
    ReviewTask,
    roll_forward_task,
)
from app.review.store import ReviewReportStore

# 优先级权重为确定性默认值，评审后可调整；分数越高越优先整改。
_SEVERITY_SCORE = {"low": 10, "medium": 20, "high": 30, "critical": 40}
_DIMENSION_SCORE = {
    Dimension.RESILIENCE.value: 3,
    Dimension.EXPERIENCE.value: 2,
    Dimension.PREFERENCE.value: 1,
}
_REPEAT_BONUS_CAP = 5


class ReviewVersionConflict(ReviewStateError):
    error_code = "VERSION_CONFLICT"


class ReviewBusy(ReviewStateError):
    error_code = "REVIEW_BUSY"


def _prioritize(
    drafts: Sequence[IssueDraft], repeat_counts: Counter[str]
) -> list[ReviewIssue]:
    """严重度 + 维度 + 历史重复构成确定性排序键，并列时按规则码稳定排序。"""
    issues: list[ReviewIssue] = []
    for draft in drafts:
        repeat = int(repeat_counts.get(draft.code, 0))
        score = (
            _SEVERITY_SCORE[draft.severity.value]
            + _DIMENSION_SCORE[draft.dimension.value]
            + min(repeat, _REPEAT_BONUS_CAP) * 2
        )
        issues.append(ReviewIssue(
            issue_id=draft.issue_id,
            code=draft.code,
            dimension=draft.dimension,
            severity=draft.severity,
            message=draft.message,
            evidence=draft.evidence,
            repeat_count=repeat,
            priority_score=score,
        ))
    issues.sort(
        key=lambda item: (-item.priority_score, -item.severity.rank, item.code, item.issue_id)
    )
    for index, issue in enumerate(issues, start=1):
        issue.priority = index
    return issues


class ReviewOrchestrator:
    def __init__(
        self,
        plan_store: PlanStore,
        report_store: ReviewReportStore,
        state_store: ReviewStateStore,
        *,
        engine: ReviewRuleEngine | None = None,
        state_machine: ReviewStateMachine | None = None,
        history_limit: int = 20,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        self._plans = plan_store
        self._reports = report_store
        self._states = state_store
        self._engine = engine or ReviewRuleEngine()
        self._machine = state_machine or ReviewStateMachine()
        self._history_limit = history_limit
        self._metrics = metrics

    @property
    def rules_version(self) -> str:
        return self._engine.rules_version

    async def review(
        self,
        *,
        user_id: str,
        session_id: str,
        plan_id: str,
        version: int,
        trace_id: str | None = None,
    ) -> ReviewReport:
        trace_id = trace_id or uuid.uuid4().hex
        latest = await self._plans.latest_version(
            user_id=user_id, session_id=session_id, plan_id=plan_id
        )
        if latest is not None and version < latest:
            raise ReviewVersionConflict(
                f"方案 {plan_id} 最新版本为 {latest}，拒绝审核旧版本 {version}"
            )
        task = await self._states.get(
            user_id=user_id, session_id=session_id, plan_id=plan_id
        )
        if task is not None:
            if task.state is ReviewState.CANCELLED:
                raise ReviewCancelled("该计划的审核已取消，不再发布新结论")
            if version < task.plan_version:
                raise ReviewVersionConflict(
                    f"当前审核针对版本 {task.plan_version}，拒绝处理旧版本 {version}"
                )
            if version > task.plan_version:
                task = roll_forward_task(task, version)
                await self._states.save(task)
        else:
            task = ReviewTask(
                user_id=user_id, session_id=session_id,
                plan_id=plan_id, plan_version=version,
            )
            await self._states.save(task)

        cached = await self._reports.get(
            user_id=user_id, session_id=session_id, plan_id=plan_id,
            plan_version=version, rules_version=self._engine.rules_version,
        )
        if cached is not None:
            return cached

        self._machine.begin_review(task)
        await self._states.save(task)

        try:
            plan = await self._plans.load(
                user_id=user_id, session_id=session_id, plan_id=plan_id,
                version=version, require_latest=True,
            )
        except PlanStoreError as exc:
            return await self._failed(task, trace_id, exc)

        missing = plan.missing_sections()
        if missing:
            report = await self._failed(
                task, trace_id,
                PlanStoreError("方案缺少必需部分：" + "、".join(missing)),
                error_code="DATA_INCOMPLETE",
                message="方案缺少必需部分：" + "、".join(missing),
            )
            return report

        history = await self._reports.list_for_plan(
            user_id=user_id, session_id=session_id, plan_id=plan_id,
            limit=self._history_limit,
        )
        repeat_counts: Counter[str] = Counter(
            issue.code for report in history for issue in report.issues
        )
        issues = _prioritize(self._engine.evaluate(plan), repeat_counts)

        self._machine.complete_review(task, has_issues=bool(issues))
        report = ReviewReport(
            review_id=uuid.uuid4().hex,
            trace_id=trace_id,
            user_id=user_id,
            session_id=session_id,
            plan_id=plan_id,
            plan_version=version,
            rules_version=self._engine.rules_version,
            outcome=(
                ReviewOutcome.CHANGES_REQUIRED if issues else ReviewOutcome.APPROVED
            ),
            issues=issues,
            requires_user_decision=task.requires_user_decision,
            review_round=max(task.review_round, 1),
            created_at=datetime.now(timezone.utc),
        )
        await self._reports.save(report)
        await self._states.save(task)
        self._observe(report)
        return report

    def _observe(self, report: ReviewReport) -> None:
        if self._metrics is not None:
            try:
                self._metrics.observe_review(report)
            except Exception:  # noqa: BLE001 - 指标失败不影响审核结论
                pass

    async def review_many(
        self,
        refs: Sequence[PlanReference],
        *,
        user_id: str,
        session_id: str,
        trace_id: str | None = None,
    ) -> list[ReviewReport]:
        """多方案统一审核；每个方案独立绑定版本与结论。"""
        reports: list[ReviewReport] = []
        for ref in refs:
            reports.append(await self.review(
                user_id=user_id, session_id=session_id,
                plan_id=ref.plan_id, version=ref.version, trace_id=trace_id,
            ))
        return reports

    async def begin_replan(
        self, *, user_id: str, session_id: str, plan_id: str, version: int
    ) -> ReviewTask:
        task = await self._states.get(
            user_id=user_id, session_id=session_id, plan_id=plan_id
        )
        if task is None:
            raise ReviewVersionConflict("没有可重规划的审核任务")
        if task.plan_version != version:
            raise ReviewVersionConflict(
                f"当前任务版本 {task.plan_version} 与请求版本 {version} 不一致"
            )
        self._machine.begin_replan(task)
        await self._states.save(task)
        return task

    async def cancel(
        self, *, user_id: str, session_id: str, plan_id: str, version: int
    ) -> ReviewTask:
        task = await self._states.get(
            user_id=user_id, session_id=session_id, plan_id=plan_id
        )
        if task is None:
            raise ReviewVersionConflict("没有可取消的审核任务")
        if task.plan_version != version:
            raise ReviewVersionConflict(
                f"当前任务版本 {task.plan_version} 与请求版本 {version} 不一致"
            )
        self._machine.cancel(task)
        await self._states.save(task)
        return task

    async def _failed(
        self,
        task: ReviewTask,
        trace_id: str,
        exc: Exception,
        *,
        error_code: str | None = None,
        message: str | None = None,
    ) -> ReviewReport:
        """失败结论不落幂等缓存，允许 REVIEW_FAILED -> REVIEWING 重试。"""
        code = error_code or getattr(exc, "error_code", "REVIEW_FAILED")
        self._machine.fail_review(task, str(exc))
        await self._states.save(task)
        report = ReviewReport(
            review_id=uuid.uuid4().hex,
            trace_id=trace_id,
            user_id=task.user_id,
            session_id=task.session_id,
            plan_id=task.plan_id,
            plan_version=task.plan_version,
            rules_version=self._engine.rules_version,
            outcome=ReviewOutcome.REVIEW_FAILED,
            error_code=code,
            error_message=message or str(exc),
            review_round=max(task.review_round, 1),
            created_at=datetime.now(timezone.utc),
        )
        self._observe(report)
        return report
