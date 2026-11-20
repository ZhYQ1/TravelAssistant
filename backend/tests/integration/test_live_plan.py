"""真实 Redis 的方案存储集成测试：只需要 REDIS_URL。"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime, timedelta

import pytest

from app.plan.models import DayPlan, HotelOption, ItineraryPlan, TransportMode, TransportOption
from app.plan.store import PlanVersionConflict, RedisPlanStore

pytestmark = pytest.mark.integration

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")


async def _connect():
    import redis.asyncio as redis_async

    client = redis_async.from_url(REDIS_URL, socket_connect_timeout=1)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        await client.aclose()
        pytest.skip(f"Redis 不可用（{REDIS_URL}）：{exc}")
    return client


def _plan(plan_id: str, version: int) -> ItineraryPlan:
    base = datetime(2026, 12, 1, 8, 0)
    return ItineraryPlan(
        plan_id=plan_id,
        user_id="u-live",
        session_id="s-live",
        version=version,
        created_at=datetime(2026, 11, 19, 10, 0),
        transport_options=[
            TransportOption(option_id="T1", mode=TransportMode.TRAIN, carrier="G1",
                            depart_at=base, arrive_at=base + timedelta(hours=3), price_cny=553)
        ],
        hotel_options=[
            HotelOption(option_id="H1", name="测试酒店", check_in=date(2026, 12, 1),
                        check_out=date(2026, 12, 3), nightly_price_cny=400, distance_km=1.2)
        ],
        days=[DayPlan(day=date(2026, 12, 1), activities=["会议"],
                      transport_option_id="T1", hotel_option_id="H1")],
        preferences={"max_budget_cny": "2000"},
    )


def test_redis_plan_store_versioning_and_immutability():
    async def scenario():
        client = await _connect()
        store = RedisPlanStore(client, "ta:itest", 60)
        plan_id = f"p-{uuid.uuid4().hex[:8]}"
        latest_key = f"ta:itest:plan:u-live:s-live:{plan_id}:latest"
        try:
            await store.save(_plan(plan_id, 1))
            await store.save(_plan(plan_id, 2))
            loaded = await store.load(
                user_id="u-live", session_id="s-live", plan_id=plan_id, version=2
            )
            latest = await store.latest_version(
                user_id="u-live", session_id="s-live", plan_id=plan_id
            )
            ttl = await client.ttl(latest_key)
            with pytest.raises(PlanVersionConflict):
                await store.load(
                    user_id="u-live", session_id="s-live", plan_id=plan_id, version=1
                )
            return loaded, latest, ttl
        finally:
            keys = await client.keys(f"ta:itest:plan:u-live:s-live:{plan_id}:*")
            if keys:
                await client.delete(*keys)
            await client.aclose()

    loaded, latest, ttl = asyncio.run(scenario())
    assert loaded.version == 2
    assert loaded.transport_options[0].option_id == "T1"
    assert latest == 2
    assert 0 < ttl <= 60
