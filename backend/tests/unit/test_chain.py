import asyncio

from app.models import Candidate, DecisionLayer, RouteType
from app.routing.audit import FailingAuditSink, InMemoryAuditSink
from app.routing.chain import RouteChain
from app.routing.jev import JevResult
from app.routing.llm_router import L3Result
from app.routing.rules import RuleMatcher
from app.routing.vector import L2Result

TASK = RouteType.TASK_COORDINATION
KB = RouteType.KNOWLEDGE_QA
WF = RouteType.WORKFLOW


def run(coro):
    return asyncio.run(coro)


class FakeVector:
    def __init__(self, result=None, error=None):
        self.result = result or L2Result("undecided", reason="fake-undecided")
        self.error = error
        self.calls = 0

    async def recall(self, text):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class FakeJev:
    def __init__(self, result=None, error=None, threshold=0.75):
        self.result = result or JevResult("invalid", reason="fake")
        self.error = error
        self.threshold = threshold

    async def decide_route(self, text):
        if self.error:
            raise self.error
        return self.result

    def is_confident(self, result):
        return result.status == "ok" and result.confidence >= self.threshold


class FakeLLM:
    def __init__(self, result=None, error=None):
        self.result = result or L3Result("invalid", reason="fake")
        self.error = error
        self.received_candidates = None

    async def decide(self, text, candidates=(), options=tuple(RouteType)):
        self.received_candidates = tuple(candidates)
        if self.error:
            raise self.error
        return self.result


def build(vector=None, jev=None, llm=None, audit=None):
    return RouteChain(
        matcher=RuleMatcher(),
        vector_router=vector or FakeVector(),
        jev=jev or FakeJev(),
        llm_router=llm or FakeLLM(),
        audit=audit or InMemoryAuditSink(),
    )


def test_l1_decision_skips_all_model_layers():
    vector, llm, audit = FakeVector(), FakeLLM(), InMemoryAuditSink()
    decision = run(build(vector, llm=llm, audit=audit).route("帮我规划下周出差"))
    assert decision.decision_layer is DecisionLayer.L1_RULE
    assert decision.route_type is TASK
    assert decision.confidence == 1.0
    assert decision.fallback is False
    assert vector.calls == 0
    assert audit.records[0]["decision_layer"] == "l1_rule"


def test_conflict_falls_through_to_vector_decision():
    vector = FakeVector(L2Result(
        "decided",
        intent=WF,
        confidence=0.88,
        candidates=(Candidate(route_type=WF, score=0.88),),
        reason="区分度 0.2 满足条件",
    ))
    decision = run(build(vector).route("帮我规划行程，顺便说下报销标准"))
    assert decision.decision_layer is DecisionLayer.L2_VECTOR
    assert decision.route_type is WF
    assert decision.fallback is True
    assert "l1:conflict" in decision.fallback_reason


def test_jev_decides_after_vector_undecided():
    jev = FakeJev(JevResult(
        "ok", value=KB, confidence=0.91,
        candidates=(Candidate(route_type=KB, score=0.91),),
    ))
    decision = run(build(jev=jev).route("这个舱位能报吗"))
    assert decision.decision_layer is DecisionLayer.JEV
    assert decision.route_type is KB
    assert "l2:undecided" in decision.fallback_reason


def test_low_jev_confidence_falls_back_to_llm_with_candidates():
    l2_candidates = (
        Candidate(route_type=KB, score=0.66),
        Candidate(route_type=TASK, score=0.61),
    )
    jev = FakeJev(JevResult("ok", value=KB, confidence=0.4))
    llm = FakeLLM(L3Result("ok", intent=KB, confidence=0.83,
                           candidates=(Candidate(route_type=KB, score=0.83),)))
    decision = run(build(
        FakeVector(L2Result("undecided", candidates=l2_candidates, reason="区分度不足")),
        jev,
        llm,
    ).route("这个能报吗"))
    assert decision.decision_layer is DecisionLayer.LLM
    assert llm.received_candidates == l2_candidates


def test_jev_adapter_error_falls_back_to_llm():
    llm = FakeLLM(L3Result(
        "ok",
        intent=KB,
        confidence=0.83,
        candidates=(Candidate(route_type=KB, score=0.83),),
    ))
    decision = run(build(jev=FakeJev(error=RuntimeError("adapter unavailable")), llm=llm)
                   .route("这个舱位能报吗"))

    assert decision.decision_layer is DecisionLayer.LLM
    assert decision.route_type is KB
    assert "jev:error:RuntimeError" in decision.fallback_reason


def test_all_layers_fail_returns_clarification():
    jev = FakeJev(error=RuntimeError("jev down"))
    llm = FakeLLM(L3Result("error", reason="timeout"))
    decision = run(build(jev=jev, llm=llm).route("嗯"))
    assert decision.decision_layer is DecisionLayer.CLARIFY
    assert decision.route_type is None
    assert decision.clarification
    assert "jev:error" in decision.fallback_reason
    assert "llm:error" in decision.fallback_reason


def test_vector_exception_does_not_break_chain():
    jev = FakeJev(JevResult("ok", value=TASK, confidence=0.9))
    decision = run(build(FakeVector(error=RuntimeError("redis down")), jev).route("嗯"))
    assert decision.route_type is TASK
    assert "l2:error:RuntimeError" in decision.fallback_reason


def test_audit_failure_does_not_change_decision():
    audit = FailingAuditSink()
    decision = run(build(audit=audit).route("帮我规划出差"))
    assert decision.route_type is TASK
    assert decision.decision_layer is DecisionLayer.L1_RULE


def test_empty_input_clarifies_without_model_calls():
    vector, llm = FakeVector(), FakeLLM()
    audit = InMemoryAuditSink()
    decision = run(build(vector, llm=llm, audit=audit).route("   "))
    assert decision.decision_layer is DecisionLayer.CLARIFY
    assert decision.fallback_reason == "empty_input"
    assert vector.calls == 0
    assert len(audit.records) == 1
    assert decision.target_agent == "clarification"


def test_trace_id_is_preserved_and_generated():
    chain = build()
    given = run(chain.route("帮我改行程", trace_id="trace-abc"))
    assert given.trace_id == "trace-abc"
    generated = run(chain.route("帮我改行程"))
    assert generated.trace_id and generated.trace_id != "trace-abc"
