"""固定集回归运行器：真实链路逐条比对并汇总延迟与判定层分布。"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from app.models import RouteDecision


@dataclass(frozen=True)
class RegressionReport:
    total: int
    correct: int
    accuracy: float
    avg_latency_ms: float
    p95_latency_ms: float
    rounds: int = 1
    total_tokens: int | None = None
    token_usage_scope: str = (
        "仅统计写入 MetricsRegistry 的 usage；不含未返回 usage 的提供方用量"
    )
    layer_counts: dict[str, int] = field(default_factory=dict)
    failures: tuple[str, ...] = ()

    def summary(self) -> str:
        layers = ", ".join(f"{name}={count}" for name, count in sorted(self.layer_counts.items()))
        tokens = (
            f"｜已观测 Token {self.total_tokens}"
            if self.total_tokens is not None
            else "｜Token 未观测（服务未返回 usage）"
        )
        head = (
            f"样本 {self.total} 条｜正确 {self.correct}｜准确率 {self.accuracy:.1%}"
            f"｜轮次 {self.rounds}｜平均延迟 {self.avg_latency_ms:.1f}ms"
            f"｜P95 {self.p95_latency_ms:.1f}ms{tokens}｜判定层分布 {layers}"
        )
        return head if not self.failures else head + "\n" + "\n".join(self.failures)


@dataclass(frozen=True)
class RegressionComparison:
    baseline_name: str
    baseline: RegressionReport
    candidate_name: str
    candidate: RegressionReport
    latency_improvement_pct: float | None
    p95_improvement_pct: float | None
    token_reduction_pct: float | None

    def summary(self) -> str:
        latency = (
            f"{self.latency_improvement_pct:.1f}%"
            if self.latency_improvement_pct is not None
            else "未计算"
        )
        p95 = (
            f"{self.p95_improvement_pct:.1f}%"
            if self.p95_improvement_pct is not None
            else "未计算"
        )
        tokens = (
            f"{self.token_reduction_pct:.1f}%"
            if self.token_reduction_pct is not None
            else "未计算"
        )
        return (
            f"基线：{self.baseline_name}\n{self.baseline.summary()}\n"
            f"候选：{self.candidate_name}\n{self.candidate.summary()}\n"
            f"平均延迟改善：{latency}｜P95 改善：{p95}｜Token 减少：{tokens}"
        )


def _actual_label(decision: RouteDecision) -> str:
    return decision.route_type.value if decision.route_type else "clarify"


def _token_total(snapshot: object) -> tuple[bool, int]:
    if not isinstance(snapshot, dict):
        return False, 0
    token_usage = snapshot.get("token_usage")
    if not isinstance(token_usage, dict) or not token_usage:
        return False, 0
    observed = False
    total = 0
    for usage in token_usage.values():
        if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
            observed = True
            total += usage["total_tokens"]
    return observed, total


async def evaluate(
    chain,
    cases: Iterable[Mapping[str, str]],
    *,
    repeats: int = 1,
) -> RegressionReport:
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    fixed_cases = list(cases)
    metrics = getattr(chain, "metrics", None)
    before_observed, before_tokens = _token_total(
        metrics.snapshot() if metrics is not None else None
    )
    total = correct = 0
    latencies: list[float] = []
    layers: dict[str, int] = {}
    failures: list[str] = []

    for round_number in range(1, repeats + 1):
        for case in fixed_cases:
            total += 1
            started = time.perf_counter()
            decision = await chain.route(case["utterance"])
            latencies.append((time.perf_counter() - started) * 1000)

            layer = decision.decision_layer.value
            layers[layer] = layers.get(layer, 0) + 1
            actual = _actual_label(decision)
            if actual == case["expected"]:
                correct += 1
            else:
                failures.append(
                    f"第 {round_number} 轮 {case['id']} 期望 {case['expected']} 实际 {actual}（{layer}）"
                    f"｜{case['utterance']}｜{decision.fallback_reason or ''}"
                )

    after_observed, after_tokens = _token_total(
        metrics.snapshot() if metrics is not None else None
    )
    ordered = sorted(latencies)
    p95_index = max(math.ceil(len(ordered) * 0.95) - 1, 0)
    accuracy = correct / total if total else 0.0
    return RegressionReport(
        total=total,
        correct=correct,
        accuracy=accuracy,
        avg_latency_ms=sum(latencies) / total if total else 0.0,
        p95_latency_ms=ordered[p95_index] if ordered else 0.0,
        rounds=repeats,
        total_tokens=(after_tokens - before_tokens)
        if after_observed or before_observed
        else None,
        layer_counts=layers,
        failures=tuple(failures),
    )


async def compare(
    baseline,
    candidate,
    cases: Iterable[Mapping[str, str]],
    *,
    repeats: int = 2,
    baseline_name: str = "baseline",
    candidate_name: str = "candidate",
) -> RegressionComparison:
    fixed_cases = list(cases)
    baseline_report = await evaluate(baseline, fixed_cases, repeats=repeats)
    candidate_report = await evaluate(candidate, fixed_cases, repeats=repeats)
    latency_improvement = (
        (baseline_report.avg_latency_ms - candidate_report.avg_latency_ms)
        / baseline_report.avg_latency_ms * 100
        if baseline_report.avg_latency_ms > 0
        else None
    )
    p95_improvement = (
        (baseline_report.p95_latency_ms - candidate_report.p95_latency_ms)
        / baseline_report.p95_latency_ms * 100
        if baseline_report.p95_latency_ms > 0
        else None
    )
    token_reduction = (
        (baseline_report.total_tokens - candidate_report.total_tokens)
        / baseline_report.total_tokens * 100
        if baseline_report.total_tokens not in (None, 0)
        and candidate_report.total_tokens is not None
        else None
    )
    return RegressionComparison(
        baseline_name=baseline_name,
        baseline=baseline_report,
        candidate_name=candidate_name,
        candidate=candidate_report,
        latency_improvement_pct=latency_improvement,
        p95_improvement_pct=p95_improvement,
        token_reduction_pct=token_reduction,
    )
