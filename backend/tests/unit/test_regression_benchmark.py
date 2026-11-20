import asyncio

from app.models import DecisionLayer, RouteDecision, RouteType
from app.observability.metrics import MetricsRegistry, TokenUsage
from app.routing.regression import compare, evaluate


class BenchChain:
    def __init__(self, total_tokens):
        self.metrics = MetricsRegistry()
        self.total_tokens = total_tokens

    async def route(self, utterance):
        self.metrics.record_token_usage(TokenUsage(
            name="route",
            model="test",
            prompt_tokens=self.total_tokens,
            completion_tokens=0,
            total_tokens=self.total_tokens,
        ))
        return RouteDecision(
            trace_id="bench",
            route_type=RouteType.TASK_COORDINATION,
            confidence=0.9,
            decision_layer=DecisionLayer.L1_RULE,
        )


def test_evaluate_repeats_cases_and_reports_p95_and_observed_tokens():
    cases = [
        {"id": "1", "utterance": "规划行程", "expected": "task_coordination"},
        {"id": "2", "utterance": "调整行程", "expected": "task_coordination"},
    ]
    report = asyncio.run(evaluate(BenchChain(7), cases, repeats=2))

    assert report.total == 4
    assert report.correct == 4
    assert report.rounds == 2
    assert report.p95_latency_ms > 0
    assert report.total_tokens == 28
    assert "P95" in report.summary()
    assert "Token 28" in report.summary()


def test_comparison_computes_token_delta_and_identifies_baseline():
    cases = [{"id": "1", "utterance": "规划行程", "expected": "task_coordination"}]
    comparison = asyncio.run(compare(
        BenchChain(10),
        BenchChain(5),
        cases,
        repeats=2,
        baseline_name="LLM-only",
        candidate_name="multi-stage",
    ))

    assert comparison.baseline.total_tokens == 20
    assert comparison.candidate.total_tokens == 10
    assert comparison.token_reduction_pct == 50
    assert comparison.p95_improvement_pct is not None
    assert comparison.baseline_name == "LLM-only"
    assert comparison.candidate.rounds == 2
