import asyncio
from types import SimpleNamespace

from agentscope.message import Msg, TextBlock

from app.agents.orchestrator import (
    AgentExecutionCoordinator,
    AgentMessageRequest,
)
from app.models import DecisionLayer, RouteDecision, RouteType


class StubChain:
    def __init__(self, route_type):
        self.route_type = route_type
        self.calls = 0
        # 有活跃子 Agent 时，L1 兜底用 matcher 判断是否携带“明确新意图信号”；
        # 测试桩统一返回 decided，使后续请求回到主路由链路。
        self.matcher = SimpleNamespace(
            match=lambda text: SimpleNamespace(status="decided")
        )

    async def route(self, text, *, trace_id=None):
        self.calls += 1
        return RouteDecision(
            trace_id=trace_id or "trace-1",
            route_type=self.route_type,
            confidence=0.9,
            decision_layer=DecisionLayer.L1_RULE,
        )


class StubAgent:
    def __init__(self, name):
        self.name = name
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)
        return Msg(
            self.name,
            [TextBlock(type="text", text=f"答复：{message.content}")],
            "assistant",
        )


class StubNoulClient:
        def __init__(self, probabilities):
            self.probabilities = iter(probabilities)
            self.states = []

        async def decide_noul(self, **request):
            import json

            self.states.append(json.loads(request["state"]))
            return next(self.probabilities)


def request(user="user-1", session="session-1"):
    return AgentMessageRequest(
        user_id=user,
        session_id=session,
        utterance="下周去深圳出差",
    )


def test_task_agent_returns_explicitly_non_bookable_draft_and_isolates_sessions():
    created = []

    def factory(route_type):
        agent = StubAgent(route_type.value)
        created.append(agent)
        return agent

    coordinator = AgentExecutionCoordinator(StubChain(RouteType.TASK_COORDINATION), factory)
    first = asyncio.run(coordinator.handle(request()))
    second = asyncio.run(coordinator.handle(request()))
    other_user = asyncio.run(coordinator.handle(request(user="user-2")))

    assert first.mode == "draft"
    assert first.bookable is False
    assert first.live_inventory_checked is False
    assert first.source_label == "模型生成草案 · 无实时库存"
    assert second.content.startswith("答复：")
    assert len(created) == 2
    assert created[0].messages[0].content == "下周去深圳出差"
    assert other_user.agent_id == "task_coordination_agent"


def test_workflow_is_an_explicit_mock_and_does_not_invoke_an_agent():
    created = []
    coordinator = AgentExecutionCoordinator(
        StubChain(RouteType.WORKFLOW),
        lambda route_type: created.append(route_type),
    )

    response = asyncio.run(coordinator.handle(request()))

    assert response.mode == "mock"
    assert response.simulation is True
    assert "没有提交到真实审批" in response.content
    assert created == []


def test_knowledge_response_is_marked_unverified():
    coordinator = AgentExecutionCoordinator(
        StubChain(RouteType.KNOWLEDGE_QA),
        lambda route_type: StubAgent(route_type.value),
    )

    response = asyncio.run(coordinator.handle(request()))

    assert response.mode == "general_generated"
    assert response.policy_verified is False
    assert response.source_label == "Mock 大模型自动生成"


def test_high_probability_noul_resumes_the_active_agent_without_main_routing():
    chain = StubChain(RouteType.TASK_COORDINATION)
    created = []
    noul = StubNoulClient([0.96])
    coordinator = AgentExecutionCoordinator(
        chain,
        lambda route_type: created.append(StubAgent(route_type.value)) or created[-1],
        noul_client=noul,
        confidence_threshold=0.75,
    )

    async def exercise():
        await coordinator.handle(request())
        continued = await coordinator.handle(AgentMessageRequest(
            user_id="user-1",
            session_id="session-1",
            utterance="预算改成三千",
        ))
        return continued

    continued = asyncio.run(exercise())

    assert chain.calls == 1
    assert len(created) == 1
    assert continued.decision.decision_layer is DecisionLayer.JEV
    assert continued.decision.confidence == 0.96
    assert continued.decision.fallback_reason == "active_agent_continuation"
    assert noul.states[0]["current_message"] == "预算改成三千"


def test_low_probability_noul_returns_to_main_route():
    chain = StubChain(RouteType.TASK_COORDINATION)
    coordinator = AgentExecutionCoordinator(
        chain,
        lambda route_type: StubAgent(route_type.value),
        noul_client=StubNoulClient([0.2]),
        confidence_threshold=0.75,
    )

    async def exercise():
        await coordinator.handle(request())
        await coordinator.handle(AgentMessageRequest(
            user_id="user-1",
            session_id="session-1",
            utterance="公司报销政策是什么",
        ))

    asyncio.run(exercise())
    assert chain.calls == 2