"""三级路由链路编排：L1 → L2 → Jev → LLM，任一环异常都不改变降级方向。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.models import DecisionLayer, RouteDecision, RouteType, clarify
from app.observability.metrics import MetricsRegistry
from app.routing.audit import AuditSink
from app.routing.jev import JevRouter
from app.routing.llm_router import LLMRouter
from app.routing.rules import RuleMatcher
from app.routing.vector import VectorRouter


@dataclass
class RouteChain:
    matcher: RuleMatcher
    vector_router: VectorRouter
    jev: JevRouter
    llm_router: LLMRouter
    audit: AuditSink
    audit_utterance_limit: int = 200
    metrics: MetricsRegistry | None = None

    async def route(self, text: str, *, trace_id: str | None = None) -> RouteDecision:
        trace_id = trace_id or uuid.uuid4().hex
        started = time.perf_counter()
        notes: list[str] = []
        utterance = (text or "").strip()

        try:
            decision = await self._decide(utterance, trace_id, notes)
        except Exception as exc:  # noqa: BLE001 - 兜底不得把异常抛给调用方
            notes.append(f"unhandled:{type(exc).__name__}")
            decision = clarify(
                trace_id,
                "内部异常",
                "抱歉，这次没能理解你的需求，可以换个说法再说一次吗？",
                fallback_reason="; ".join(notes),
            )

        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        decision = decision.model_copy(update={"latency_ms": latency_ms})
        await self._audit(decision, utterance, notes, started)
        if self.metrics is not None:
            try:
                self.metrics.observe_route(decision, latency_ms)
            except Exception:  # noqa: BLE001 - 指标失败不得影响路由
                pass
        return decision

    async def _decide(self, text: str, trace_id: str, notes: list[str]) -> RouteDecision:
        if not text:
            return clarify(
                trace_id,
                "空输入",
                "请说明你想处理的差旅事项，例如「帮我规划下周去上海的行程」。",
                fallback=False,
                fallback_reason="empty_input",
            )

        l1 = self.matcher.match(text)
        if l1.status == "decided" and l1.intent is not None:
            return RouteDecision(
                trace_id=trace_id,
                route_type=l1.intent,
                confidence=1.0,
                decision_layer=DecisionLayer.L1_RULE,
                rule_id=l1.rule_id,
            )
        notes.append(f"l1:{l1.status}:{l1.reason}")

        try:
            l2 = await self.vector_router.recall(text)
        except Exception as exc:  # noqa: BLE001 - 单层故障必须下沉
            l2 = None
            notes.append(f"l2:error:{type(exc).__name__}")
        if l2 is not None:
            if l2.status == "decided" and l2.intent is not None:
                return RouteDecision(
                    trace_id=trace_id,
                    route_type=l2.intent,
                    confidence=l2.confidence,
                    decision_layer=DecisionLayer.L2_VECTOR,
                    candidates=list(l2.candidates),
                    fallback=True,
                    fallback_reason="; ".join(notes + [l2.reason]),
                )
            notes.append(f"l2:{l2.status}:{l2.reason}")

        try:
            jev = await self.jev.decide_route(text)
        except Exception as exc:  # noqa: BLE001
            jev = None
            notes.append(f"jev:error:{type(exc).__name__}")
        if (
            jev is not None
            and jev.status == "ok"
            and isinstance(jev.value, RouteType)
            and self.jev.is_confident(jev)
        ):
            return RouteDecision(
                trace_id=trace_id,
                route_type=jev.value,
                confidence=jev.confidence,
                decision_layer=DecisionLayer.JEV,
                candidates=list(jev.candidates),
                fallback=True,
                fallback_reason="; ".join(notes),
            )
        if jev is not None:
            notes.append(f"jev:{jev.status}:{jev.reason or 'low_confidence'}")

        hints = list(l2.candidates) if l2 else []
        if jev is not None:
            hints = hints or list(jev.candidates)
        try:
            llm = await self.llm_router.decide(text, candidates=hints)
        except Exception as exc:  # noqa: BLE001
            llm = None
            notes.append(f"llm:error:{type(exc).__name__}")
        if llm is not None:
            if llm.status == "ok" and llm.intent is not None:
                return RouteDecision(
                    trace_id=trace_id,
                    route_type=llm.intent,
                    confidence=llm.confidence,
                    decision_layer=DecisionLayer.LLM,
                    candidates=list(llm.candidates),
                    fallback=True,
                    fallback_reason="; ".join(notes),
                )
            notes.append(f"llm:{llm.status}:{llm.reason}")
        return clarify(
            trace_id,
            "无法判定",
            "我还不确定你想做哪件事，可以说明是要规划/修改行程、咨询差旅政策，还是提交审批吗？",
            fallback_reason="; ".join(notes),
        )

    async def _audit(
        self,
        decision: RouteDecision,
        text: str,
        notes: list[str],
        started: float,
    ) -> None:
        payload: dict[str, Any] = {
            "trace_id": decision.trace_id,
            "utterance": text[: self.audit_utterance_limit],
            "route_type": decision.route_type.value if decision.route_type else None,
            "decision_layer": decision.decision_layer.value,
            "confidence": round(decision.confidence, 6),
            "fallback": decision.fallback,
            "fallback_reason": decision.fallback_reason,
            "rule_id": decision.rule_id,
            "candidates": [item.model_dump() for item in decision.candidates],
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "stage_log": notes,
            "created_at": time.time(),
        }
        try:
            await self.audit.record(payload)
        except Exception:  # noqa: BLE001 - 审计失败不得影响路由
            pass