from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import ConfigError
from app.models import DecisionLayer, RouteDecision, RouteType


class StubChain:
    def __init__(self, decision: RouteDecision):
        self.decision = decision
        self.calls = []

    async def route(self, text, *, trace_id=None):
        self.calls.append((text, trace_id))
        return self.decision.model_copy(update={"trace_id": trace_id or self.decision.trace_id})


def make_client(decision):
    chain = StubChain(decision)
    client = TestClient(create_app(chain_factory=lambda: chain))
    return client, chain


def test_route_endpoint_returns_decision_with_target_agent():
    decision = RouteDecision(
        trace_id="t-1",
        route_type=RouteType.KNOWLEDGE_QA,
        confidence=0.88,
        decision_layer=DecisionLayer.JEV,
        fallback=True,
    )
    client, chain = make_client(decision)
    response = client.post("/v1/route", json={"utterance": "报销标准是多少", "trace_id": "abc"})
    assert response.status_code == 200
    body = response.json()
    assert body["route_type"] == "knowledge_qa"
    assert body["target_agent"] == "knowledge_qa_agent"
    assert body["decision_layer"] == "jev"
    assert chain.calls == [("报销标准是多少", "abc")]


def test_route_endpoint_rejects_blank_utterance():
    client, _ = make_client(
        RouteDecision(trace_id="t", route_type=RouteType.WORKFLOW, confidence=1.0,
                      decision_layer=DecisionLayer.L1_RULE)
    )
    assert client.post("/v1/route", json={"utterance": ""}).status_code == 422
    assert client.post("/v1/route", json={}).status_code == 422


def test_route_endpoint_returns_clarification_as_200():
    from app.models import clarify

    client, _ = make_client(clarify("t-2", "无法判定", "你想做什么？"))
    body = client.post("/v1/route", json={"utterance": "嗯"}).json()
    assert body["decision_layer"] == "clarify"
    assert body["route_type"] is None
    assert body["target_agent"] == "clarification"
    assert body["clarification"]


def test_missing_config_returns_503():
    def broken_factory():
        raise ConfigError("缺少必需环境变量：LLM_API_KEY")

    client = TestClient(create_app(chain_factory=broken_factory))
    response = client.post("/v1/route", json={"utterance": "帮我改行程"})
    assert response.status_code == 503
    assert "LLM_API_KEY" in response.json()["detail"]


def test_healthz_is_independent_of_services():
    client = TestClient(create_app(chain_factory=lambda: None))
    assert client.get("/healthz").json() == {"status": "ok"}


def test_chain_is_created_once():
    calls = {"count": 0}

    decision = RouteDecision(trace_id="t", route_type=RouteType.WORKFLOW,
                             confidence=1.0, decision_layer=DecisionLayer.L1_RULE)

    def factory():
        calls["count"] += 1
        return StubChain(decision)

    client = TestClient(create_app(chain_factory=factory))
    client.post("/v1/route", json={"utterance": "a"})
    client.post("/v1/route", json={"utterance": "b"})
    assert calls["count"] == 1


def test_endpoint_is_async_safe_under_concurrency():
    decision = RouteDecision(trace_id="t", route_type=RouteType.WORKFLOW,
                             confidence=1.0, decision_layer=DecisionLayer.L1_RULE)
    client, _ = make_client(decision)
    results = []
    for index in range(5):
        results.append(client.post("/v1/route", json={"utterance": f"走流程{index}"}))
    assert all(item.status_code == 200 for item in results)
