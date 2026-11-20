"""进程内指标注册表：延迟、Token、审核与续跑口径统一快照。"""

from __future__ import annotations

import math
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Protocol

from app.loop.continuation import ContinuationResult
from app.models import RouteDecision
from app.review.models import ReviewOutcome, ReviewReport


@dataclass(frozen=True)
class TokenUsage:
    name: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class TokenUsageSink(Protocol):
    def record_token_usage(self, usage: TokenUsage) -> None: ...


class MetricsRegistry:
    """同步记录、异步链路可直接调用；指标失败由调用方隔离。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[str] = Counter()
        self._latencies: dict[str, list[float]] = defaultdict(list)
        self._tokens: dict[str, dict[str, int]] = defaultdict(
            lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        )

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def observe_latency(self, name: str, latency_ms: float) -> None:
        with self._lock:
            self._latencies[name].append(float(latency_ms))

    def record_token_usage(self, usage: TokenUsage) -> None:
        key = f"{usage.name}|{usage.model}"
        with self._lock:
            bucket = self._tokens[key]
            bucket["calls"] += 1
            bucket["prompt_tokens"] += usage.prompt_tokens
            bucket["completion_tokens"] += usage.completion_tokens
            bucket["total_tokens"] += usage.total_tokens

    def observe_route(self, decision: RouteDecision, latency_ms: float) -> None:
        layer = decision.decision_layer.value
        with self._lock:
            self._counters["route_total"] += 1
            self._counters[f"route_{layer}"] += 1
            if decision.fallback:
                self._counters["route_fallback"] += 1
            if layer == "llm":
                self._counters["route_llm_fallback"] += 1
            self._latencies["route"].append(float(latency_ms))
            self._latencies[f"route_{layer}"].append(float(latency_ms))

    def observe_review(self, report: ReviewReport) -> None:
        with self._lock:
            self._counters["review_total"] += 1
            self._counters[f"review_outcome_{report.outcome.value}"] += 1
            if report.outcome is ReviewOutcome.APPROVED and report.review_round == 1:
                self._counters["review_first_pass"] += 1
            if report.outcome is ReviewOutcome.REVIEW_FAILED:
                self._counters[f"review_error_{report.error_code or 'unknown'}"] += 1

    def observe_continuation(self, result: ContinuationResult) -> None:
        with self._lock:
            self._counters["continuation_total"] += 1
            self._counters[f"continuation_{result.status}"] += 1
            if result.error_code:
                self._counters[f"continuation_error_{result.error_code}"] += 1

    @staticmethod
    def _latency_stats(values: list[float]) -> dict[str, float | int]:
        ordered = sorted(values)
        p95_index = max(math.ceil(len(ordered) * 0.95) - 1, 0)
        return {
            "count": len(ordered),
            "mean": round(sum(ordered) / len(ordered), 3),
            "p95": round(ordered[p95_index], 3),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(sorted(self._counters.items()))
            latencies = {
                name: self._latency_stats(values)
                for name, values in sorted(self._latencies.items()) if values
            }
            tokens = {name: dict(values) for name, values in sorted(self._tokens.items())}
        route_total = counters.get("route_total", 0)
        review_total = counters.get("review_total", 0)
        continuation_total = counters.get("continuation_total", 0)
        ratios = {
            "route_llm_fallback_rate": _ratio(
                counters.get("route_llm_fallback", 0), route_total
            ),
            "review_first_pass_rate": _ratio(
                counters.get("review_first_pass", 0), review_total
            ),
            "review_read_failure_rate": _ratio(
                sum(value for key, value in counters.items()
                    if key.startswith("review_error_DATA_INCOMPLETE")
                    or key.startswith("review_error_VERSION_CONFLICT")),
                review_total,
            ),
            "continuation_direct_rate": _ratio(
                counters.get("continuation_resumed", 0), continuation_total
            ),
        }
        return {
            "counters": counters,
            "latency_ms": latencies,
            "token_usage": tokens,
            "ratios": ratios,
        }


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0