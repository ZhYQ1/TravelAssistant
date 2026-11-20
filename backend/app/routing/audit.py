"""路由审计：失败不影响路由结果。"""

from __future__ import annotations

import json
from typing import Any, Protocol


class AuditSink(Protocol):
    async def record(self, payload: dict[str, Any]) -> None:
        """写入一条路由审计记录。"""


class RedisAuditSink:
    def __init__(self, redis, prefix: str, ttl_seconds: int) -> None:
        self._redis = redis
        self._prefix = prefix
        self._ttl = ttl_seconds

    async def record(self, payload: dict[str, Any]) -> None:
        key = f"{self._prefix}:audit:{payload['trace_id']}"
        await self._redis.set(key, json.dumps(payload, ensure_ascii=False), ex=self._ttl)


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, payload: dict[str, Any]) -> None:
        self.records.append(payload)


class FailingAuditSink:
    """测试用：模拟审计后端不可用。"""

    async def record(self, payload: dict[str, Any]) -> None:
        raise RuntimeError("audit down")
