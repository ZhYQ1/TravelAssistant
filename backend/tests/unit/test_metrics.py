import asyncio

from app.llm.client import HttpChatClient
from app.loop.continuation import ContinuationResult
from app.models import DecisionLayer, RouteDecision, RouteType
from app.observability.metrics import MetricsRegistry, TokenUsage
from app.review.models import ReviewOutcome, ReviewReport
from app.routing.chain import RouteChain
from app.routing.audit import InMemoryAuditSink
from app.routing.rules import RuleMatcher
from datetime import datetime, timezone


class UnusedLayer:
    async def recall(self, text):
        raise AssertionError("L1 命中不应调用 L2")

    async def decide_route(self, text):
        raise AssertionError("L1 命中不应调用 Jev")

    async def decide(self, text, candidates=(), options=tuple(RouteType)):
        raise AssertionError("L1 命中不应调用 LLM")


def decision(layer=DecisionLayer.L1_RULE, fallback=False):
    return RouteDecision(
        trace_id="t-1", route_type=RouteType.TASK_COORDINATION,
        confidence=1.0, decision_layer=layer, fallback=fallback,
    )


def report(outcome=ReviewOutcome.APPROVED, error_code=None):
    return ReviewReport(
        review_id="r-1", trace_id="t-1", user_id="u-1", session_id="s-1",
        plan_id="p-1", plan_version=1, rules_version="review-rules-v1",
        outcome=outcome, error_code=error_code, created_at=datetime.now(timezone.utc),
    )


def test_chain_records_route_layer_latency_and_fallback_metrics():
    metrics = MetricsRegistry()
    chain = RouteChain(
        matcher=RuleMatcher(), vector_router=UnusedLayer(), jev=UnusedLayer(),
        llm_router=UnusedLayer(), audit=InMemoryAuditSink(), metrics=metrics,
    )
    result = asyncio.run(chain.route("帮我改行程"))
    assert result.decision_layer is DecisionLayer.L1_RULE

    snapshot = metrics.snapshot()
    assert snapshot["counters"]["route_total"] == 1
    assert snapshot["counters"]["route_l1_rule"] == 1
    assert snapshot["latency_ms"]["route"]["count"] == 1
    assert snapshot["ratios"]["route_llm_fallback_rate"] == 0.0


def test_snapshot_aggregates_tokens_reviews_and_continuation_rates():
    metrics = MetricsRegistry()
    metrics.observe_route(decision(DecisionLayer.LLM, fallback=True), 120.0)
    metrics.record_token_usage(TokenUsage("jev_option", "model-a", 100, 20, 120))
    metrics.record_token_usage(TokenUsage("jev_option", "model-a", 50, 10, 60))
    metrics.observe_review(report())
    metrics.observe_review(report(ReviewOutcome.REVIEW_FAILED, "DATA_INCOMPLETE"))
    metrics.observe_continuation(ContinuationResult(
        status="resumed", session_id="s-1", action="confirm"
    ))
    metrics.observe_continuation(ContinuationResult(
        status="fallback", session_id="s-1", action="confirm",
        error_code="VERSION_CONFLICT",
    ))

    snapshot = metrics.snapshot()
    assert snapshot["token_usage"]["jev_option|model-a"] == {
        "calls": 2, "prompt_tokens": 150, "completion_tokens": 30, "total_tokens": 180
    }
    assert snapshot["counters"]["review_first_pass"] == 1
    assert snapshot["ratios"]["review_first_pass_rate"] == 0.5
    assert snapshot["ratios"]["review_read_failure_rate"] == 0.5
    assert snapshot["ratios"]["continuation_direct_rate"] == 0.5
    assert snapshot["ratios"]["route_llm_fallback_rate"] == 1.0


def test_http_client_side_channel_records_usage_without_changing_return_contract():
    metrics = MetricsRegistry()
    client = HttpChatClient("http://unused", "secret", "model-a", usage_sink=metrics)
    client._record_usage("llm_route", {
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
    })
    client._record_usage("llm_route", {"choices": []})
    assert metrics.snapshot()["token_usage"]["llm_route|model-a"]["total_tokens"] == 15