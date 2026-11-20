"""真实 Redis 闭环集成测试：方案审核、直达续跑与 Skill 内容恢复。"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.context.folding import RedisSkillContentStore, SkillContent, SkillContentCollapseHook
from app.loop.continuation import (
    ActiveAgentSession,
    ContinuationAction,
    ContinuationRequest,
    ContinuationService,
    RedisContinuationStore,
    TaskContinuationHandler,
)
from app.plan.models import DayPlan, HotelOption, ItineraryPlan, TransportMode, TransportOption
from app.plan.store import RedisPlanStore
from app.review.models import ReviewOutcome
from app.review.orchestrator import ReviewOrchestrator
from app.review.state import RedisReviewStateStore, ReviewState
from app.review.store import RedisReviewReportStore

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


def _problem_plan(namespace: str) -> ItineraryPlan:
    base = datetime(2026, 12, 1, 5, 0)
    return ItineraryPlan(
        plan_id=f"plan-{namespace}",
        user_id=f"user-{namespace}",
        session_id=f"session-{namespace}",
        version=1,
        created_at=datetime(2026, 11, 19, 10, 0),
        transport_options=[TransportOption(
            option_id="T1", mode=TransportMode.TRAIN, carrier="G9",
            depart_at=base, arrive_at=base + timedelta(hours=18, minutes=30),
            price_cny=1500, transfer_count=2,
        )],
        hotel_options=[HotelOption(
            option_id="H1", name="远酒店", check_in=date(2026, 12, 1),
            check_out=date(2026, 12, 3), nightly_price_cny=800,
            distance_km=12, refundable=False,
        )],
        days=[
            DayPlan(day=date(2026, 12, 1), activities=["拜访"], transport_option_id="T1"),
            DayPlan(day=date(2026, 12, 2), activities=["复盘"], hotel_option_id="H1"),
        ],
        preferences={"max_budget_cny": "1000", "hotel_brand": "近", "avoid_red_eye": "1"},
    )


def test_redis_closed_loop_review_continuation_and_content_recovery():
    async def scenario():
        client = await _connect()
        namespace = uuid.uuid4().hex
        prefix = f"ta:closed-loop:{namespace}"
        plan = _problem_plan(namespace)
        try:
            plans = RedisPlanStore(client, prefix, 300)
            reports = RedisReviewReportStore(client, prefix, 300)
            states = RedisReviewStateStore(client, prefix, 300)
            reviewer = ReviewOrchestrator(plans, reports, states)
            await plans.save(plan)

            first = await reviewer.review(
                user_id=plan.user_id, session_id=plan.session_id,
                plan_id=plan.plan_id, version=1,
            )
            second = await reviewer.review(
                user_id=plan.user_id, session_id=plan.session_id,
                plan_id=plan.plan_id, version=1,
            )
            assert first.outcome is ReviewOutcome.CHANGES_REQUIRED
            assert second.review_id == first.review_id
            task = await states.get(
                user_id=plan.user_id, session_id=plan.session_id, plan_id=plan.plan_id
            )
            assert task.state is ReviewState.CHANGES_REQUIRED

            continuations = RedisContinuationStore(client, prefix, 300)
            session = ActiveAgentSession.for_phase(
                session_id=plan.session_id, user_id=plan.user_id,
                agent_id="task_coordination_agent", task_id=f"task-{namespace}",
                phase="changes_required", plan_id=plan.plan_id, plan_version=1,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            await continuations.save_session(session)
            service = ContinuationService(continuations, {
                "task_coordination_agent": TaskContinuationHandler(continuations, reviewer),
            })
            request = ContinuationRequest(
                session_id=plan.session_id, user_id=plan.user_id,
                agent_id=session.agent_id, task_id=session.task_id,
                action=ContinuationAction.REPLAN, click_id=f"click-{namespace}",
                plan_id=plan.plan_id, plan_version=1,
            )
            resumed = await service.resume(request)
            duplicate = await service.resume(request)
            assert resumed.status == "resumed"
            assert resumed.output["phase"] == "replanning"
            assert duplicate.duplicate is True
            assert duplicate.output == resumed.output
            task = await states.get(
                user_id=plan.user_id, session_id=plan.session_id, plan_id=plan.plan_id
            )
            assert task.state is ReviewState.REPLANNING
            active = await continuations.get_session(plan.session_id)
            assert active.phase == "replanning"

            contents = RedisSkillContentStore(client, prefix, 300)
            hook = SkillContentCollapseHook(contents, collapse_after_turns=2)
            original = SkillContent(
                content_id=f"content-{namespace}", skill_id="weather", loaded_turn=1,
                text="真实 Redis 中保存的完整 Skill 输出", summary="天气查询摘要",
            )
            await hook.register(original)
            collapsed = await hook.collapse([original], current_turn=4)
            assert collapsed.folded_ids == (original.content_id,)
            restored = await hook.restore(collapsed.blocks[0].reference)
            assert restored == original
        finally:
            keys = [key async for key in client.scan_iter(match=f"{prefix}:*")]
            if keys:
                await client.delete(*keys)
            await client.aclose()

    asyncio.run(scenario())
