"""Jev route client backed by the official TypeSafe Python SDK."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, Sequence

from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Score,
    ScoreAnswer,
    TypeSafeError,
)

from app.models import Candidate, RouteType


@dataclass(frozen=True)
class JevResult:
    """Validated route-choice result from the official Jev service."""

    status: Literal["ok", "invalid", "error"]
    value: RouteType | None = None
    confidence: float = 0.0
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)
    reason: str = ""


class JevChoiceClient(Protocol):
    async def decide_route(
        self, text: str, options: dict[str, str]
    ) -> dict[str, Any]:
        """Return the official Jev Choice answer through the TypeSafe SDK."""


class JevAdapterError(RuntimeError):
    """The TypeSafe Jev service is unavailable or returned invalid data."""


class JevAdapterClient:
    """Jev client backed by the official TypeSafe Python SDK.

    Replaces the former local Java adapter sidecar: the SDK calls the hosted
    Jev / System-One service (or a self-hosted endpoint) directly over HTTPS.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 18.0,
        client: AsyncTypeSafeClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._client = client

    def _ensure_client(self) -> AsyncTypeSafeClient:
        # Lazy: the SDK validates the API key eagerly on construction, so we
        # defer it until the first real request. This keeps Jev optional —
        # without a key the client can still be assembled, and the first call
        # raises JevAdapterError which the routing chain degrades on.
        if self._client is None:
            self._client = AsyncTypeSafeClient(
                api_key=self._api_key,
                model=self._model,
                base_url=self._base_url,
                timeout=self._timeout_seconds,
            )
        return self._client

    async def decide_route(
        self, text: str, options: dict[str, str]
    ) -> dict[str, Any]:
        answer = await self._answer(
            state=text,
            questions={"route": Choice(criteria=options)},
            key="route",
            expected=ChoiceAnswer,
        )
        return {
            "choice": answer.choice,
            "confidence": answer.confidence,
            "probabilities": answer.probabilities,
        }

    async def decide_noul(
        self,
        *,
        state: str,
        instructions: str,
        true_meaning: str,
        false_meaning: str,
    ) -> float:
        answer = await self._answer(
            state=state,
            questions={
                "continuation": Noul(
                    instructions=instructions,
                    criteria={"true": true_meaning, "false": false_meaning},
                )
            },
            key="continuation",
            expected=NoulAnswer,
        )
        probability = answer.noul
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise JevAdapterError("Jev adapter returned an invalid Noul probability")
        return float(probability)

    async def score(
        self,
        *,
        state: str,
        instructions: str,
        criteria: list[dict[str, str]],
    ) -> dict[str, Any]:
        rubric = [criterion["description"] for criterion in criteria]
        if not rubric:
            raise JevAdapterError("Jev score requires a non-empty rubric")
        answer = await self._answer(
            state=state,
            questions={"score": Score(instructions=instructions, criteria=rubric)},
            key="score",
            expected=ScoreAnswer,
        )
        return {
            "score": answer.score,
            "confidence": answer.confidence,
            "legend": {str(key): value for key, value in answer.legend.items()},
            "probabilities": {
                str(key): value for key, value in answer.probabilities.items()
            },
        }

    async def _answer(
        self,
        *,
        state: str,
        questions: dict[str, Any],
        key: str,
        expected: type,
    ) -> Any:
        try:
            client = self._ensure_client()
            response = await client.system_one(
                state=state,
                questions=questions,
            )
        except TypeSafeError as exc:
            raise JevAdapterError(
                f"Jev request failed: {type(exc).__name__}: {exc}"
            ) from exc
        answer = response.answers.get(key)
        if not isinstance(answer, expected):
            raise JevAdapterError(
                f"Jev returned an unexpected answer type for {key!r}"
            )
        return answer


_ROUTE_DESCRIPTIONS = {
    RouteType.TASK_COORDINATION: "差旅行程规划、调整及方案咨询",
    RouteType.KNOWLEDGE_QA: "差旅政策、报销和目的地信息咨询",
    RouteType.WORKFLOW: "差旅审批、报销等流程办理",
}


def _confidence(raw: dict[str, Any]) -> float:
    value = raw.get("confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("confidence is missing or not numeric")
    value = float(value)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("confidence is outside [0, 1]")
    return value


def _probabilities(raw: dict[str, Any], allowed: set[str]) -> tuple[Candidate, ...]:
    values = raw.get("probabilities")
    if not isinstance(values, dict) or set(values) != allowed:
        raise ValueError("probabilities must cover exactly the supplied options")

    scores: dict[RouteType, float] = {}
    for route_value, score in values.items():
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError(f"probability for {route_value!r} is not numeric")
        score = float(score)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError(f"probability for {route_value!r} is outside [0, 1]")
        scores[RouteType(route_value)] = score

    return tuple(
        Candidate(route_type=intent, score=score)
        for intent, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
    )


class JevRouter:
    def __init__(self, client: JevChoiceClient, *, confidence_threshold: float) -> None:
        self._client = client
        self._threshold = confidence_threshold

    @property
    def confidence_threshold(self) -> float:
        return self._threshold

    async def decide_route(
        self, text: str, options: Sequence[RouteType] = tuple(RouteType)
    ) -> JevResult:
        allowed = {option.value for option in options}
        descriptions = {
            option.value: _ROUTE_DESCRIPTIONS[option]
            for option in options
        }
        try:
            raw = await self._client.decide_route(text, descriptions)
        except Exception as exc:  # noqa: BLE001 - RouteChain owns the LLM fallback.
            return JevResult("error", reason=f"{type(exc).__name__}: {exc}")

        try:
            route_value = raw.get("choice")
            if route_value not in allowed:
                raise ValueError(f"choice is not in the supplied options: {route_value!r}")
            confidence = _confidence(raw)
            candidates = _probabilities(raw, allowed)
        except (ValueError, TypeError) as exc:
            return JevResult("invalid", reason=str(exc))

        intent = RouteType(route_value)
        return JevResult(
            status="ok",
            value=intent,
            confidence=confidence,
            candidates=candidates,
        )

    def is_confident(self, result: JevResult) -> bool:
        return result.status == "ok" and result.confidence >= self._threshold