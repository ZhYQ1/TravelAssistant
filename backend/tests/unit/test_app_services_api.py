import asyncio
from datetime import date, datetime, timedelta, timezone

from agentscope.message import Msg, TextBlock
from fastapi.testclient import TestClient

from app.agents.orchestrator import AgentExecutionCoordinator
from app.api.main import AppServices, create_app
from app.config import ConfigError
from app.context.folding import InMemorySkillContentStore, SkillContentCollapseHook
from app.loop.continuation import (
    ActiveAgentSession,
    ContinuationService,
    InMemoryContinuationStore,
)
from app.models import DecisionLayer, RouteDecision, RouteType
from app.observability.metrics import MetricsRegistry
from app.plan.models import DayPlan, HotelOption, ItineraryPlan, TransportMode, TransportOption
from app.plan.store import InMemoryPlanStore, PlanService
from app.review.orchestrator import ReviewOrchestrator
from app.review.state import InMemoryReviewStateStore
from app.review.store import InMemoryReviewReportStore
from app.skill.adapter import InMemoryCredentialResolver, InMemorySkillStateStore, SkillAdapter


class StubChain:
    async def route(self, text, *, trace_id=None):
        return RouteDecision(
            trace_id=trace_id or "t-1", route_type=RouteType.TASK_COORDINATION,
            confidence=1.0, decision_layer=DecisionLayer.L1_RULE,
        )


class StubHandler:
    async def handle(self, *, session, action, instruction=None):
        return {"phase": "approved" if action.value == "confirm" else "changed"}


class StubAgent:
    async def __call__(self, message):
        return Msg("task_coordination_agent", [
            TextBlock(type="text", text="无实时库存的草案")
        ], "assistant")


def clean_plan(version=1):
    base = datetime(2026, 12, 1, 9, 0)
    return ItineraryPlan(
        plan_id="p-api", user_id="u-1", session_id="s-1", version=version,
        created_at=datetime(2026, 11, 19, 10, 0),
        transport_options=[
            TransportOption(option_id="T1", mode=TransportMode.TRAIN, carrier="G1",
                            depart_at=base, arrive_at=base + timedelta(hours=3),
                            price_cny=500),
            TransportOption(option_id="T2", mode=TransportMode.TRAIN, carrier="G2",
                            depart_at=base + timedelta(hours=1),
                            arrive_at=base + timedelta(hours=4), price_cny=600),
        ],
        hotel_options=[
            HotelOption(option_id="H1", name="测试酒店", check_in=date(2026, 12, 1),
                        check_out=date(2026, 12, 3), nightly_price_cny=400,
                        distance_km=2),
            HotelOption(option_id="H2", name="测试酒店二", check_in=date(2026, 12, 1),
                        check_out=date(2026, 12, 3), nightly_price_cny=450,
                        distance_km=3),
        ],
        days=[DayPlan(day=date(2026, 12, 1), transport_option_id="T1",
                      hotel_option_id="H1")],
        preferences={"max_budget_cny": "5000"},
    )


def make_services(tmp_path):
    plans = InMemoryPlanStore()
    continuation_store = InMemoryContinuationStore()
    metrics = MetricsRegistry()
    return AppServices(
        chain=StubChain(),
        plan_store=plans,
        plan_service=PlanService(plans),
        review_orchestrator=ReviewOrchestrator(
            plans, InMemoryReviewReportStore(), InMemoryReviewStateStore(), metrics=metrics
        ),
        continuation_store=continuation_store,
        continuation_service=ContinuationService(
            continuation_store, {"task_coordination_agent": StubHandler()}
        ),
        skill_adapter=SkillAdapter(
            InMemoryCredentialResolver(), InMemorySkillStateStore(), base_dir=tmp_path
        ),
        content_hook=SkillContentCollapseHook(InMemorySkillContentStore()),
        metrics=metrics,
        agent_coordinator=AgentExecutionCoordinator(
            StubChain(),
            lambda route_type: StubAgent(),
        ),
    )


def test_plan_review_and_metrics_endpoints(tmp_path):
    services = make_services(tmp_path)
    client = TestClient(create_app(services_factory=lambda: services))
    plan = clean_plan()

    created = client.post("/v1/plans", json=plan.model_dump(mode="json"))
    assert created.status_code == 201
    assert created.json() == {"plan_id": "p-api", "version": 1}

    changed = plan.model_copy(update={"preferences": {"max_budget_cny": "1"}})
    conflict = client.post("/v1/plans", json=changed.model_dump(mode="json"))
    assert conflict.status_code == 409

    loaded = client.get("/v1/plans/p-api", params={"user_id": "u-1", "session_id": "s-1"})
    assert loaded.status_code == 200
    assert len(loaded.json()["transport_options"]) == 2

    reviewed = client.post("/v1/plans/p-api/reviews", json={
        "user_id": "u-1", "session_id": "s-1", "version": 1, "trace_id": "trace-review"
    })
    assert reviewed.status_code == 200
    assert reviewed.json()["outcome"] == "approved"
    assert reviewed.json()["trace_id"] == "trace-review"

    repeated = client.post("/v1/plans/p-api/reviews", json={
        "user_id": "u-1", "session_id": "s-1", "version": 1
    })
    assert repeated.json()["review_id"] == reviewed.json()["review_id"]

    metrics = client.get("/metrics").json()
    assert metrics["counters"]["review_total"] == 1  # 幂等命中不重复计数
    assert metrics["counters"]["review_first_pass"] == 1


def test_assistant_endpoint_returns_agent_reply_with_non_bookable_contract(tmp_path):
    client = TestClient(create_app(
        services_factory=lambda: make_services(tmp_path)
    ))

    response = client.post("/v1/assistant/message", json={
        "user_id": "u-1",
        "session_id": "s-1",
        "utterance": "帮我规划深圳差旅",
    })

    assert response.status_code == 200
    assert response.json()["agent_id"] == "task_coordination_agent"
    assert response.json()["mode"] == "draft"
    assert response.json()["bookable"] is False
    assert response.json()["live_inventory_checked"] is False
    assert response.json()["content"] == "无实时库存的草案"


def test_continuation_actions_resume_idempotency_and_metrics(tmp_path):
    services = make_services(tmp_path)
    session = ActiveAgentSession.for_phase(
        session_id="s-1", user_id="u-1", agent_id="task_coordination_agent",
        task_id="task-1", phase="awaiting_confirmation", plan_id="p-api",
        plan_version=1, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    asyncio.run(services.continuation_store.save_session(session))
    client = TestClient(create_app(services_factory=lambda: services))

    actions = client.get("/v1/continuation/actions",
                         params={"session_id": "s-1", "user_id": "u-1"})
    assert actions.status_code == 200
    assert [item["action"] for item in actions.json()] == ["confirm", "modify", "cancel"]

    payload = {
        "session_id": "s-1", "user_id": "u-1", "agent_id": "task_coordination_agent",
        "task_id": "task-1", "action": "confirm", "click_id": "click-api-1",
        "plan_id": "p-api", "plan_version": 1,
    }
    resumed = client.post("/v1/continuation/resume", json=payload)
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "resumed"
    assert resumed.json()["agent_id"] == "task_coordination_agent"

    duplicate = client.post("/v1/continuation/resume", json=payload)
    assert duplicate.json()["duplicate"] is True

    stale = client.post("/v1/continuation/resume", json=payload | {
        "click_id": "click-api-2", "plan_version": 0
    })
    assert stale.status_code == 422  # 版本必须 >= 1，请求不会进入业务处理

    stale = client.post("/v1/continuation/resume", json=payload | {
        "click_id": "click-api-3", "plan_version": 2
    })
    assert stale.json()["error_code"] == "VERSION_CONFLICT"

    unknown = client.post("/v1/continuation/resume", json=payload | {"action": "自由执行"})
    assert unknown.status_code == 422

    metrics = client.get("/metrics").json()
    assert metrics["counters"]["continuation_total"] == 2  # 重复点击不重复计数
    assert metrics["counters"]["continuation_resumed"] == 1
    assert metrics["counters"]["continuation_fallback"] == 1


def test_new_endpoints_return_503_when_real_config_is_missing(tmp_path):
    def broken_factory():
        raise ConfigError("缺少必需环境变量：REDIS_URL")

    client = TestClient(create_app(services_factory=broken_factory))
    response = client.post("/v1/plans", json=clean_plan().model_dump(mode="json"))
    assert response.status_code == 503
    assert "REDIS_URL" in response.json()["detail"]
