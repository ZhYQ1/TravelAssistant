"""真实 Redis 集成测试：只需要 REDIS_URL，不依赖模型服务。"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

pytestmark = pytest.mark.integration

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")


async def _connect():
    """建立异步连接；不可达时跳过整条用例。"""
    import redis.asyncio as redis_async

    client = redis_async.from_url(REDIS_URL, socket_connect_timeout=1)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        await client.aclose()
        pytest.skip(f"Redis 不可用（{REDIS_URL}）：{exc}")
    return client


def test_redis_intent_vector_store_roundtrip():
    from app.routing.vector import RedisIntentVectorStore

    async def scenario():
        client = await _connect()
        store = RedisIntentVectorStore(client, "ta:itest", 60)
        namespace = uuid.uuid4().hex
        key = f"ta:itest:intent_vectors:{namespace}"
        try:
            await store.save(namespace, {
                "task_coordination|0": [0.1, 0.2, 0.3],
                "knowledge_qa|0": [0.3, 0.2, 0.1],
            })
            loaded = await store.load(namespace)
            ttl = await client.ttl(key)
            return loaded, ttl
        finally:
            await client.delete(key)
            await client.aclose()

    loaded, ttl = asyncio.run(scenario())
    assert loaded == {
        "task_coordination|0": [0.1, 0.2, 0.3],
        "knowledge_qa|0": [0.3, 0.2, 0.1],
    }
    assert 0 < ttl <= 60


def test_redis_audit_sink_writes_ttl_record():
    from app.routing.audit import RedisAuditSink

    async def scenario():
        client = await _connect()
        sink = RedisAuditSink(client, "ta:itest", 60)
        trace_id = uuid.uuid4().hex
        key = f"ta:itest:audit:{trace_id}"
        try:
            await sink.record({"trace_id": trace_id, "decision_layer": "l1_rule"})
            return await client.exists(key), await client.ttl(key)
        finally:
            await client.delete(key)
            await client.aclose()

    exists, ttl = asyncio.run(scenario())
    assert exists == 1
    assert 0 < ttl <= 60
