"""对真实服务运行 60 条固定集两轮对照回归：

    python scripts/run_route_regression.py --rounds 2 --min-accuracy 0.8
    python scripts/run_route_regression.py --output .runtime/route-report.json

基线是仅使用 LLM 的参考路由，不代表历史版本。Token 只统计服务返回并记录的 usage；
Jev 或其他未返回 usage 的提供方不会被估算。运行需要 Redis / Embedding / LLM 配置。
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.main import build_chain  # noqa: E402
from app.config import ConfigError, Settings  # noqa: E402
from app.llm.client import HttpChatClient  # noqa: E402
from app.models import DecisionLayer, RouteDecision, clarify  # noqa: E402
from app.observability.metrics import MetricsRegistry  # noqa: E402
from app.routing.llm_router import LLMRouter  # noqa: E402
from app.routing.regression import compare  # noqa: E402


def load_cases() -> list[dict[str, str]]:
    path = ROOT / "tests" / "fixtures" / "route_regression_60.jsonl"
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


class LLMOnlyBaseline:
    def __init__(self, settings: Settings, metrics: MetricsRegistry) -> None:
        self.metrics = metrics
        self._router = LLMRouter(HttpChatClient(
            settings.llm_base_url,
            settings.llm_api_key,
            settings.llm_model,
            temperature=settings.llm_temperature,
            timeout_seconds=settings.llm_timeout_seconds,
            usage_sink=metrics,
        ))

    async def route(self, utterance: str) -> RouteDecision:
        result = await self._router.decide(utterance)
        if result.status == "ok" and result.intent is not None and result.confidence > 0:
            return RouteDecision(
                trace_id="llm-only-baseline",
                route_type=result.intent,
                confidence=result.confidence,
                decision_layer=DecisionLayer.LLM,
                candidates=list(result.candidates),
            )
        return clarify(
            "llm-only-baseline",
            "LLM-only baseline could not decide",
            "请补充差旅需求。",
        )


async def main(min_accuracy: float, rounds: int, output: str | None) -> int:
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"配置缺失：{exc}", file=sys.stderr)
        return 2

    baseline_metrics = MetricsRegistry()
    candidate_metrics = MetricsRegistry()
    baseline = LLMOnlyBaseline(settings, baseline_metrics)
    candidate = build_chain(settings, metrics=candidate_metrics)
    comparison = await compare(
        baseline,
        candidate,
        load_cases(),
        repeats=rounds,
        baseline_name="LLM-only reference",
        candidate_name="L1→L2→Jev→LLM",
    )
    print(comparison.summary())
    if output:
        Path(output).write_text(
            json.dumps(asdict(comparison), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"报告已写入：{output}")
    return 0 if comparison.candidate.accuracy >= min_accuracy else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-accuracy", type=float,
                        default=float(os.environ.get("REGRESSION_MIN_ACCURACY", "0.8")))
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--output", help="可选：保存对比报告 JSON 到指定路径")
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be at least 1")
    raise SystemExit(asyncio.run(main(args.min_accuracy, args.rounds, args.output)))
