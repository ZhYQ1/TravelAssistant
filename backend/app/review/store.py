"""审核报告存储：幂等键为 计划版本 + 规则版本，并提供历史查询。"""

from __future__ import annotations

import json
from typing import Any, Protocol

from app.review.models import ReviewReport


class ReviewReportStore(Protocol):
    async def get(
        self, *, user_id: str, session_id: str, plan_id: str,
        plan_version: int, rules_version: str,
    ) -> ReviewReport | None:
        """按幂等键读取已有报告。"""

    async def save(self, report: ReviewReport) -> None:
        """保存报告并登记到计划历史。"""

    async def list_for_plan(
        self, *, user_id: str, session_id: str, plan_id: str, limit: int = 20,
    ) -> list[ReviewReport]:
        """按时间倒序返回同一计划的历史报告。"""


def _report_key(prefix: str, report: ReviewReport) -> str:
    return (
        f"{prefix}:review:{report.user_id}:{report.session_id}:{report.plan_id}:"
        f"v{report.plan_version}:{report.rules_version}"
    )


def _history_key(prefix: str, user_id: str, session_id: str, plan_id: str) -> str:
    return f"{prefix}:review_history:{user_id}:{session_id}:{plan_id}"


def _as_str(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


class InMemoryReviewReportStore:
    def __init__(self) -> None:
        self._reports: dict[str, str] = {}
        self._history: dict[str, list[str]] = {}

    async def get(self, *, user_id: str, session_id: str, plan_id: str,
                  plan_version: int, rules_version: str) -> ReviewReport | None:
        key = f"{user_id}:{session_id}:{plan_id}:v{plan_version}:{rules_version}"
        payload = self._reports.get(key)
        return ReviewReport.model_validate_json(payload) if payload else None

    async def save(self, report: ReviewReport) -> None:
        key = f"{report.user_id}:{report.session_id}:{report.plan_id}:v{report.plan_version}:{report.rules_version}"
        if key in self._reports:
            return
        self._reports[key] = report.model_dump_json()
        self._history.setdefault(
            f"{report.user_id}:{report.session_id}:{report.plan_id}", []
        ).insert(0, key)

    async def list_for_plan(self, *, user_id: str, session_id: str, plan_id: str,
                            limit: int = 20) -> list[ReviewReport]:
        keys = self._history.get(f"{user_id}:{session_id}:{plan_id}", [])[:limit]
        return [ReviewReport.model_validate_json(self._reports[key]) for key in keys]


class RedisReviewReportStore:
    def __init__(self, redis, prefix: str, ttl_seconds: int) -> None:
        self._redis = redis
        self._prefix = prefix
        self._ttl = ttl_seconds

    async def get(self, *, user_id: str, session_id: str, plan_id: str,
                  plan_version: int, rules_version: str) -> ReviewReport | None:
        key = (
            f"{self._prefix}:review:{user_id}:{session_id}:{plan_id}:"
            f"v{plan_version}:{rules_version}"
        )
        raw = await self._redis.get(key)
        return None if raw is None else ReviewReport.model_validate_json(_as_str(raw))

    async def save(self, report: ReviewReport) -> None:
        key = _report_key(self._prefix, report)
        created = await self._redis.set(key, report.model_dump_json(), ex=self._ttl, nx=True)
        if not created:
            return  # 同幂等键重复提交，保留首次结论
        history = _history_key(
            self._prefix, report.user_id, report.session_id, report.plan_id
        )
        await self._redis.lpush(history, key)
        await self._redis.ltrim(history, 0, 49)
        await self._redis.expire(history, self._ttl)

    async def list_for_plan(self, *, user_id: str, session_id: str, plan_id: str,
                            limit: int = 20) -> list[ReviewReport]:
        history = _history_key(self._prefix, user_id, session_id, plan_id)
        keys = await self._redis.lrange(history, 0, max(limit - 1, 0))
        reports: list[ReviewReport] = []
        for raw_key in keys:
            raw = await self._redis.get(_as_str(raw_key))
            if raw is not None:
                reports.append(ReviewReport.model_validate_json(_as_str(raw)))
        return reports
