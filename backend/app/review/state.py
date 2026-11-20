"""审核状态机：状态流转受控，最大轮次超限后不再静默循环。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol


class ReviewState(str, Enum):
    PLANNED = "planned"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    CHANGES_REQUIRED = "changes_required"
    REPLANNING = "replanning"
    REVIEW_FAILED = "review_failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = {ReviewState.APPROVED, ReviewState.CANCELLED}


class ReviewStateError(RuntimeError):
    error_code = "REVIEW_STATE_ERROR"


class IllegalTransition(ReviewStateError):
    error_code = "ILLEGAL_TRANSITION"


class ReviewCancelled(ReviewStateError):
    error_code = "CANCELLED"


class RequiresUserDecision(ReviewStateError):
    error_code = "REQUIRES_USER_DECISION"


class ReviewStateNotFound(ReviewStateError):
    error_code = "STATE_NOT_FOUND"


ALLOWED_TRANSITIONS: dict[ReviewState, set[ReviewState]] = {
    ReviewState.PLANNED: {ReviewState.REVIEWING, ReviewState.CANCELLED},
    ReviewState.REVIEWING: {
        ReviewState.APPROVED,
        ReviewState.CHANGES_REQUIRED,
        ReviewState.REVIEW_FAILED,
        ReviewState.CANCELLED,
    },
    ReviewState.CHANGES_REQUIRED: {ReviewState.REPLANNING, ReviewState.CANCELLED},
    ReviewState.REPLANNING: {ReviewState.PLANNED, ReviewState.CANCELLED},
    ReviewState.REVIEW_FAILED: {
        ReviewState.REVIEWING,
        ReviewState.REPLANNING,
        ReviewState.CANCELLED,
    },
    ReviewState.APPROVED: set(),
    ReviewState.CANCELLED: set(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ReviewTask:
    user_id: str
    session_id: str
    plan_id: str
    plan_version: int
    state: ReviewState = ReviewState.PLANNED
    review_round: int = 0
    replan_round: int = 0
    requires_user_decision: bool = False
    updated_at: datetime = field(default_factory=_now)
    transitions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "state": self.state.value,
            "review_round": self.review_round,
            "replan_round": self.replan_round,
            "requires_user_decision": self.requires_user_decision,
            "updated_at": self.updated_at.isoformat(),
            "transitions": list(self.transitions),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ReviewTask":
        return cls(
            user_id=raw["user_id"],
            session_id=raw["session_id"],
            plan_id=raw["plan_id"],
            plan_version=int(raw["plan_version"]),
            state=ReviewState(raw["state"]),
            review_round=int(raw.get("review_round", 0)),
            replan_round=int(raw.get("replan_round", 0)),
            requires_user_decision=bool(raw.get("requires_user_decision", False)),
            updated_at=datetime.fromisoformat(raw["updated_at"]),
            transitions=list(raw.get("transitions", [])),
        )


class ReviewStateMachine:
    """最大复审轮次由配置决定；超限后必须请求用户决策。"""

    def __init__(self, max_review_rounds: int = 3) -> None:
        if max_review_rounds < 1:
            raise ValueError("max_review_rounds 必须大于 0")
        self._max_rounds = max_review_rounds

    @property
    def max_review_rounds(self) -> int:
        return self._max_rounds

    def _transition(self, task: ReviewTask, target: ReviewState, reason: str) -> None:
        if target not in ALLOWED_TRANSITIONS[task.state]:
            raise IllegalTransition(f"不允许从 {task.state.value} 流转到 {target.value}")
        task.transitions.append(f"{task.state.value}->{target.value}:{reason}")
        task.state = target
        task.updated_at = _now()

    def begin_review(self, task: ReviewTask) -> None:
        if task.state is ReviewState.CANCELLED:
            raise ReviewCancelled("审核已取消")
        if task.state is ReviewState.APPROVED:
            raise IllegalTransition("已通过的方案不再重复审核")
        if task.state is ReviewState.REVIEWING:
            return  # 并发重试按幂等处理，不重复流转
        self._transition(task, ReviewState.REVIEWING, "发起审核")

    def complete_review(self, task: ReviewTask, *, has_issues: bool) -> None:
        if task.state is not ReviewState.REVIEWING:
            raise IllegalTransition(f"{task.state.value} 状态下不能提交审核结论")
        task.review_round += 1
        if has_issues:
            self._transition(task, ReviewState.CHANGES_REQUIRED, "存在需整改问题")
            if task.review_round >= self._max_rounds:
                task.requires_user_decision = True
        else:
            self._transition(task, ReviewState.APPROVED, "全部必检项通过")

    def fail_review(self, task: ReviewTask, reason: str) -> None:
        if task.state is ReviewState.REVIEWING:
            self._transition(task, ReviewState.REVIEW_FAILED, reason)
        task.requires_user_decision = False

    def begin_replan(self, task: ReviewTask) -> None:
        if task.requires_user_decision:
            raise RequiresUserDecision("已达到最大整改轮次，需用户决策")
        if task.state is not ReviewState.CHANGES_REQUIRED:
            raise IllegalTransition(f"{task.state.value} 状态下不能进入重规划")
        self._transition(task, ReviewState.REPLANNING, "生成整改优先级")

    def cancel(self, task: ReviewTask) -> None:
        if task.state is ReviewState.CANCELLED:
            return
        if task.state is ReviewState.APPROVED:
            raise IllegalTransition("已完成的审核不可取消")
        self._transition(task, ReviewState.CANCELLED, "用户取消")


def roll_forward_task(previous: ReviewTask, new_version: int) -> ReviewTask:
    """重规划产生新版本后重新进入 PLANNED，但保留轮次计数。"""
    if new_version <= previous.plan_version:
        raise IllegalTransition(
            f"新版本 {new_version} 必须大于当前版本 {previous.plan_version}"
        )
    task = ReviewTask(
        user_id=previous.user_id,
        session_id=previous.session_id,
        plan_id=previous.plan_id,
        plan_version=new_version,
        review_round=previous.review_round,
        replan_round=previous.replan_round + 1,
    )
    task.transitions.append(
        f"version:{previous.plan_version}->{new_version}:产生新版本方案"
    )
    return task


class ReviewStateStore(Protocol):
    async def get(self, *, user_id: str, session_id: str, plan_id: str) -> ReviewTask | None:
        """读取审核任务状态。"""

    async def save(self, task: ReviewTask) -> None:
        """保存审核任务状态。"""


class InMemoryReviewStateStore:
    def __init__(self) -> None:
        self._tasks: dict[str, ReviewTask] = {}

    def _key(self, user_id: str, session_id: str, plan_id: str) -> str:
        return f"{user_id}:{session_id}:{plan_id}"

    async def get(self, *, user_id: str, session_id: str, plan_id: str) -> ReviewTask | None:
        task = self._tasks.get(self._key(user_id, session_id, plan_id))
        return ReviewTask.from_dict(task.to_dict()) if task else None

    async def save(self, task: ReviewTask) -> None:
        self._tasks[self._key(task.user_id, task.session_id, task.plan_id)] = (
            ReviewTask.from_dict(task.to_dict())
        )


class RedisReviewStateStore:
    def __init__(self, redis, prefix: str, ttl_seconds: int) -> None:
        self._redis = redis
        self._prefix = prefix
        self._ttl = ttl_seconds

    def _key(self, user_id: str, session_id: str, plan_id: str) -> str:
        return f"{self._prefix}:review_state:{user_id}:{session_id}:{plan_id}"

    async def get(self, *, user_id: str, session_id: str, plan_id: str) -> ReviewTask | None:
        raw = await self._redis.get(self._key(user_id, session_id, plan_id))
        if raw is None:
            return None
        text = raw.decode() if isinstance(raw, bytes) else raw
        return ReviewTask.from_dict(json.loads(text))

    async def save(self, task: ReviewTask) -> None:
        await self._redis.set(
            self._key(task.user_id, task.session_id, task.plan_id),
            json.dumps(task.to_dict(), ensure_ascii=False),
            ex=self._ttl,
        )
