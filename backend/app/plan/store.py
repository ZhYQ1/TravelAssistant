"""方案存储：Key 含用户/会话/计划/版本，拒绝覆盖同版本内容。"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from app.plan.models import ItineraryPlan, PlanReference, PlanSummary


class PlanStoreError(RuntimeError):
    """方案存储层错误的统一基类，携带稳定错误码。"""

    error_code = "PLAN_ERROR"


class PlanNotFound(PlanStoreError):
    """方案缺失或已过期；对应 DATA_INCOMPLETE。"""

    error_code = "DATA_INCOMPLETE"


class PlanVersionConflict(PlanStoreError):
    """请求版本不是最新版本，或试图覆盖同版本内容。"""

    error_code = "VERSION_CONFLICT"


class PlanIntegrityError(PlanStoreError):
    """存储内容与请求的归属/版本不一致。"""

    error_code = "PLAN_INTEGRITY_ERROR"


class PlanStore(Protocol):
    async def save(self, plan: ItineraryPlan) -> None:
        """写入一个不可变版本；相同内容重复写入视为幂等。"""

    async def load(
        self,
        *,
        user_id: str,
        session_id: str,
        plan_id: str,
        version: int,
        require_latest: bool = True,
    ) -> ItineraryPlan:
        """按归属与版本读取完整方案。"""

    async def latest_version(self, *, user_id: str, session_id: str, plan_id: str) -> int | None:
        """返回当前最新版本；不存在时返回 None。"""

    async def list_ids(self, *, user_id: str, session_id: str) -> list[str]:
        """按最近更新时间倒序列出该会话下的方案编号，供行程列表使用。"""


def _as_str(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _dump(plan: ItineraryPlan) -> str:
    return json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


# 仅在新版本不小于当前 latest 时更新指针，避免旧版本回写覆盖新版本。
_UPDATE_LATEST_LUA = """
local current = redis.call('GET', KEYS[1])
if (not current) or (tonumber(current) < tonumber(ARGV[1])) then
  redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
end
return 1
"""


class RedisPlanStore:
    """Redis 实现：方案正文与 latest 指针分开保存。"""

    def __init__(self, redis, prefix: str, ttl_seconds: int) -> None:
        self._redis = redis
        self._prefix = prefix
        self._ttl = ttl_seconds

    def _plan_key(self, user_id: str, session_id: str, plan_id: str, version: int) -> str:
        return f"{self._prefix}:plan:{user_id}:{session_id}:{plan_id}:v{version}"

    def _latest_key(self, user_id: str, session_id: str, plan_id: str) -> str:
        return f"{self._prefix}:plan:{user_id}:{session_id}:{plan_id}:latest"

    def _index_key(self, user_id: str, session_id: str) -> str:
        return f"{self._prefix}:plans:{user_id}:{session_id}"

    async def save(self, plan: ItineraryPlan) -> None:
        key = self._plan_key(plan.user_id, plan.session_id, plan.plan_id, plan.version)
        payload = _dump(plan)
        created = await self._redis.set(key, payload, ex=self._ttl, nx=True)
        if not created:
            existing = await self._redis.get(key)
            if existing is not None and json.loads(_as_str(existing)) != json.loads(payload):
                raise PlanVersionConflict(
                    f"方案 {plan.plan_id} 版本 {plan.version} 已存在且内容不同，必须写入新版本"
                )
            if existing is None:
                # 与过期竞争：补写一次，仍失败则明确报错。
                if not await self._redis.set(key, payload, ex=self._ttl, nx=True):
                    raise PlanVersionConflict(
                        f"方案 {plan.plan_id} 版本 {plan.version} 写入竞争失败"
                    )
        latest_key = self._latest_key(plan.user_id, plan.session_id, plan.plan_id)
        await self._redis.eval(_UPDATE_LATEST_LUA, 1, latest_key, plan.version, self._ttl)
        index_key = self._index_key(plan.user_id, plan.session_id)
        await self._redis.zadd(index_key, {plan.plan_id: time.time()})
        await self._redis.expire(index_key, self._ttl)

    async def load(
        self,
        *,
        user_id: str,
        session_id: str,
        plan_id: str,
        version: int,
        require_latest: bool = True,
    ) -> ItineraryPlan:
        key = self._plan_key(user_id, session_id, plan_id, version)
        raw = await self._redis.get(key)
        if raw is None:
            raise PlanNotFound(f"方案 {plan_id} 版本 {version} 不存在或已过期")
        plan = ItineraryPlan.model_validate_json(_as_str(raw))
        if (plan.user_id, plan.session_id, plan.plan_id, plan.version) != (
            user_id,
            session_id,
            plan_id,
            version,
        ):
            raise PlanIntegrityError("方案归属或版本与存储 Key 不一致")
        if require_latest:
            latest = await self.latest_version(
                user_id=user_id, session_id=session_id, plan_id=plan_id
            )
            if latest is not None and latest != version:
                raise PlanVersionConflict(
                    f"方案 {plan_id} 最新版本为 {latest}，请求版本 {version} 已失效"
                )
        return plan

    async def latest_version(self, *, user_id: str, session_id: str, plan_id: str) -> int | None:
        raw = await self._redis.get(self._latest_key(user_id, session_id, plan_id))
        return None if raw is None else int(_as_str(raw))

    async def list_ids(self, *, user_id: str, session_id: str) -> list[str]:
        raw = await self._redis.zrevrange(self._index_key(user_id, session_id), 0, -1)
        return [_as_str(item) for item in raw]


class InMemoryPlanStore:
    """与 Redis 行为一致的本地实现，用于单测。"""

    def __init__(self) -> None:
        self._plans: dict[str, str] = {}
        self._latest: dict[str, int] = {}
        self._index: dict[str, dict[str, float]] = {}

    def _plan_key(self, user_id: str, session_id: str, plan_id: str, version: int) -> str:
        return f"{user_id}:{session_id}:{plan_id}:v{version}"

    def _latest_key(self, user_id: str, session_id: str, plan_id: str) -> str:
        return f"{user_id}:{session_id}:{plan_id}"

    async def save(self, plan: ItineraryPlan) -> None:
        key = self._plan_key(plan.user_id, plan.session_id, plan.plan_id, plan.version)
        payload = _dump(plan)
        existing = self._plans.get(key)
        if existing is not None and existing != payload:
            raise PlanVersionConflict(
                f"方案 {plan.plan_id} 版本 {plan.version} 已存在且内容不同，必须写入新版本"
            )
        self._plans[key] = payload
        latest_key = self._latest_key(plan.user_id, plan.session_id, plan.plan_id)
        current = self._latest.get(latest_key)
        if current is None or current < plan.version:
            self._latest[latest_key] = plan.version
        self._index.setdefault(f"{plan.user_id}:{plan.session_id}", {})[
            plan.plan_id
        ] = time.time()

    async def load(
        self,
        *,
        user_id: str,
        session_id: str,
        plan_id: str,
        version: int,
        require_latest: bool = True,
    ) -> ItineraryPlan:
        payload = self._plans.get(self._plan_key(user_id, session_id, plan_id, version))
        if payload is None:
            raise PlanNotFound(f"方案 {plan_id} 版本 {version} 不存在或已过期")
        plan = ItineraryPlan.model_validate_json(payload)
        if require_latest:
            latest = await self.latest_version(
                user_id=user_id, session_id=session_id, plan_id=plan_id
            )
            if latest is not None and latest != version:
                raise PlanVersionConflict(
                    f"方案 {plan_id} 最新版本为 {latest}，请求版本 {version} 已失效"
                )
        return plan

    async def latest_version(self, *, user_id: str, session_id: str, plan_id: str) -> int | None:
        return self._latest.get(self._latest_key(user_id, session_id, plan_id))

    async def list_ids(self, *, user_id: str, session_id: str) -> list[str]:
        entries = self._index.get(f"{user_id}:{session_id}", {})
        return [
            plan_id
            for plan_id, _ in sorted(entries.items(), key=lambda item: item[1], reverse=True)
        ]


class PlanService:
    """先持久化、回读确认后再发布引用（FR-P01）。"""

    def __init__(self, store: PlanStore) -> None:
        self._store = store

    async def publish(self, plan: ItineraryPlan) -> PlanReference:
        await self._store.save(plan)
        try:
            await self._store.load(
                user_id=plan.user_id,
                session_id=plan.session_id,
                plan_id=plan.plan_id,
                version=plan.version,
                require_latest=True,
            )
        except PlanStoreError as exc:
            raise PlanIntegrityError(f"方案写入后回读失败：{exc}") from exc
        return PlanReference(plan_id=plan.plan_id, version=plan.version)

    async def get(
        self,
        *,
        user_id: str,
        session_id: str,
        plan_id: str,
        version: int,
        require_latest: bool = True,
    ) -> ItineraryPlan:
        return await self._store.load(
            user_id=user_id,
            session_id=session_id,
            plan_id=plan_id,
            version=version,
            require_latest=require_latest,
        )

    async def list_summaries(
        self, *, user_id: str, session_id: str, limit: int = 20
    ) -> list[PlanSummary]:
        """按最近更新时间倒序返回会话内方案摘要；过期成员跳过而非报错。"""
        plan_ids = await self._store.list_ids(user_id=user_id, session_id=session_id)
        summaries: list[PlanSummary] = []
        for plan_id in plan_ids[:limit]:
            version = await self._store.latest_version(
                user_id=user_id, session_id=session_id, plan_id=plan_id
            )
            if version is None:
                continue
            try:
                plan = await self._store.load(
                    user_id=user_id,
                    session_id=session_id,
                    plan_id=plan_id,
                    version=version,
                    require_latest=False,
                )
            except PlanStoreError:
                continue
            summaries.append(
                PlanSummary(
                    plan_id=plan.plan_id,
                    version=plan.version,
                    title=plan.preferences.get("title", ""),
                    created_at=plan.created_at,
                )
            )
        return summaries
