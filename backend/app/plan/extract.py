"""把对话草案抽取为结构化方案字段；抽取失败时退化为纯文本草案。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.llm.client import LLMError, JsonLLM
from app.plan.models import DayPlan, ItineraryPlan, PlanSummary

_SYSTEM = """你是差旅方案的结构化抽取器。只输出一个 JSON 对象，不要输出解释或 Markdown。
字段与类型：
- title: 字符串，不超过 24 字，概括本次出差（例如“深圳三日出差”）
- city: 字符串，目的地城市；无法确定填空字符串
- start_date: 字符串，格式 YYYY-MM-DD；无法确定填 null
- days: 整数，行程天数；无法确定填 null
- budget_cny: 数字，预算上限（元）；无法确定填 null
- hotel_area: 字符串，住宿区域或商圈；无法确定填空字符串
今天的日期是 {today}。遇到“下周”“明天”等相对时间时按今天换算；换算不出具体日期就填 null。"""


def _iso_date(value: Any) -> str | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if 1 <= parsed <= 30 else None


def _non_negative_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if 0 <= parsed <= 10_000_000 else None


def _fallback_title(draft: str) -> str:
    first = next((line.strip() for line in draft.splitlines() if line.strip()), "")
    cleaned = first.lstrip("#*- >").replace("**", "").strip()
    if not cleaned:
        return "差旅草案"
    return cleaned[:24]


class DraftExtractor:
    """基于 chat 模型把草案抽取为方案字段；任何失败都不阻断草案落库。"""

    def __init__(self, llm: JsonLLM) -> None:
        self._llm = llm

    async def extract(self, draft: str, *, today: date | None = None) -> dict[str, Any]:
        today = today or date.today()
        try:
            payload = await self._llm.complete_json(
                _SYSTEM.format(today=today.isoformat()),
                draft[:6000],
                name="plan_extraction",
            )
        except LLMError:
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload

    @staticmethod
    def normalize(payload: dict[str, Any], draft: str) -> dict[str, Any]:
        """逐字段容错，单字段异常不影响其余字段。"""
        title = str(payload.get("title") or "").strip()[:24]
        return {
            "title": title or _fallback_title(draft),
            "city": str(payload.get("city") or "").strip()[:32],
            "start_date": _iso_date(payload.get("start_date")),
            "days": _positive_int(payload.get("days")),
            "budget_cny": _non_negative_float(payload.get("budget_cny")),
            "hotel_area": str(payload.get("hotel_area") or "").strip()[:32],
        }

    @staticmethod
    def build_plan(
        *,
        facts: dict[str, Any],
        draft: str,
        plan_id: str,
        user_id: str,
        session_id: str,
        version: int,
        created_at,
    ) -> ItineraryPlan:
        preferences = {"draft": draft, "title": facts.get("title") or _fallback_title(draft)}
        if facts.get("city"):
            preferences["city"] = str(facts["city"])
        if facts.get("budget_cny") is not None:
            preferences["budget_cny"] = str(facts["budget_cny"])
        if facts.get("hotel_area"):
            preferences["hotel_area"] = str(facts["hotel_area"])
        if facts.get("start_date"):
            preferences["start_date"] = str(facts["start_date"])
        if facts.get("days") is not None:
            preferences["days"] = str(facts["days"])

        days: list[DayPlan] = []
        if facts.get("start_date") and facts.get("days"):
            start = date.fromisoformat(str(facts["start_date"]))
            for offset in range(int(facts["days"])):
                days.append(DayPlan(day=start + timedelta(days=offset)))

        return ItineraryPlan(
            plan_id=plan_id,
            user_id=user_id,
            session_id=session_id,
            version=version,
            created_at=created_at,
            transport_options=[],
            hotel_options=[],
            days=days,
            preferences=preferences,
        )

    @staticmethod
    def summary(plan: ItineraryPlan) -> PlanSummary:
        return PlanSummary(
            plan_id=plan.plan_id,
            version=plan.version,
            title=plan.preferences.get("title", ""),
            created_at=plan.created_at,
        )
