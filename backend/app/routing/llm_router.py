"""L3-b：LLM 精判，接收上层候选并输出同构结果。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from app.llm.client import JsonLLM
from app.models import Candidate, RouteType

_SYSTEM = (
    "你是差旅助手的最终路由判定器。用户输入可能口语化、省略或存在歧义。"
    "必须在给定路由类型中选择最符合用户真实目的的一个，并给出0到1的置信度。"
    '只输出 JSON：{"route_type": "<枚举值>", "confidence": <0到1的小数>, '
    '"reason": "<不超过30字的判定依据>"}。'
)


@dataclass(frozen=True)
class L3Result:
    status: Literal["ok", "invalid", "error"]
    intent: RouteType | None = None
    confidence: float = 0.0
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)
    reason: str = ""


class LLMRouter:
    """只做一次精判；调用失败不重试，避免链路出现回退环。"""

    def __init__(self, llm: JsonLLM) -> None:
        self._llm = llm

    async def decide(
        self,
        text: str,
        candidates: Sequence[Candidate] = (),
        options: Sequence[RouteType] = tuple(RouteType),
    ) -> L3Result:
        allowed = [option.value for option in options]
        hint = (
            "上一级候选："
            + json.dumps(
                [{"route_type": item.route_type.value, "score": item.score}
                 for item in candidates],
                ensure_ascii=False,
            )
            if candidates
            else "上一级候选：无"
        )
        prompt = (
            f"可选类型：{allowed}\n{hint}\n"
            f"用户输入：{json.dumps(text, ensure_ascii=False)}"
        )
        try:
            raw: dict[str, Any] = await self._llm.complete_json(
                _SYSTEM, prompt, name="llm_route"
            )
        except Exception as exc:  # noqa: BLE001 - 交由链路澄清
            return L3Result("error", reason=f"{type(exc).__name__}: {exc}")

        route_value = raw.get("route_type")
        confidence = raw.get("confidence")
        if route_value not in allowed:
            return L3Result("invalid", reason=f"route_type 不在枚举内：{route_value!r}")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            return L3Result("invalid", reason="confidence 缺失或不是数字")
        confidence = float(confidence)
        if not 0.0 <= confidence <= 1.0:
            return L3Result("invalid", reason="confidence 超出 [0,1]")

        intent = RouteType(route_value)
        return L3Result(
            "ok",
            intent=intent,
            confidence=confidence,
            candidates=(Candidate(route_type=intent, score=confidence),),
            reason=str(raw.get("reason") or ""),
        )
