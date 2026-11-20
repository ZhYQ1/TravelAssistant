"""审核规则引擎：体验、韧性、偏好三组确定性规则。

规则版本参与审核幂等键，修改规则必须升级版本，避免旧结论被误复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

from app.plan.models import ItineraryPlan
from app.review.models import Dimension, Severity

RULES_VERSION = "review-rules-v1"

_EARLY_HOUR = 7
_LATE_HOUR = 23
_FAR_HOTEL_KM = 10.0
_MAX_TRANSFERS = 2
_TRUTHY = {"1", "true", "yes", "是", "需要", "需要避免"}


@dataclass(frozen=True)
class IssueDraft:
    """规则产出的原始问题，尚未计算历史重复与优先级。"""

    code: str
    dimension: Dimension
    severity: Severity
    message: str
    target: str
    evidence: dict[str, object] = field(default_factory=dict)

    @property
    def issue_id(self) -> str:
        return f"{self.code}::{self.target}"


Rule = Callable[[ItineraryPlan], list[IssueDraft]]


def _pref_float(plan: ItineraryPlan, key: str) -> float | None:
    raw = plan.preferences.get(key)
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _is_truthy(plan: ItineraryPlan, key: str) -> bool:
    return (plan.preferences.get(key) or "").strip().lower() in _TRUTHY


def _experience_rules(plan: ItineraryPlan) -> list[IssueDraft]:
    issues: list[IssueDraft] = []
    for option in plan.transport_options:
        if option.depart_at.hour < _EARLY_HOUR:
            issues.append(IssueDraft(
                "EXP-EARLY-DEPARTURE", Dimension.EXPERIENCE, Severity.MEDIUM,
                f"交通 {option.option_id} 出发过早（{option.depart_at:%H:%M}）",
                option.option_id,
                {"depart_at": option.depart_at.isoformat()},
            ))
        if option.arrive_at.hour >= _LATE_HOUR:
            issues.append(IssueDraft(
                "EXP-LATE-ARRIVAL", Dimension.EXPERIENCE, Severity.MEDIUM,
                f"交通 {option.option_id} 到达过晚（{option.arrive_at:%H:%M}）",
                option.option_id,
                {"arrive_at": option.arrive_at.isoformat()},
            ))
    for hotel in plan.hotel_options:
        if hotel.distance_km > _FAR_HOTEL_KM:
            issues.append(IssueDraft(
                "EXP-HOTEL-FAR", Dimension.EXPERIENCE, Severity.MEDIUM,
                f"酒店 {hotel.name} 距活动地点 {hotel.distance_km} 公里",
                hotel.option_id,
                {"distance_km": hotel.distance_km},
            ))
    ordered_days = sorted(plan.days, key=lambda item: item.day)
    for day in ordered_days[:-1]:
        if day.hotel_option_id is None:
            issues.append(IssueDraft(
                "EXP-NO-OVERNIGHT", Dimension.EXPERIENCE, Severity.HIGH,
                f"{day.day} 未安排过夜酒店",
                day.day.isoformat(),
                {"day": day.day.isoformat()},
            ))
    return issues


def _resilience_rules(plan: ItineraryPlan) -> list[IssueDraft]:
    issues: list[IssueDraft] = []
    if len(plan.transport_options) == 1:
        issues.append(IssueDraft(
            "RES-SINGLE-TRANSPORT", Dimension.RESILIENCE, Severity.HIGH,
            "只有一个交通候选，缺少可替代方案",
            plan.transport_options[0].option_id,
            {"candidates": 1},
        ))
    if len(plan.hotel_options) == 1:
        issues.append(IssueDraft(
            "RES-SINGLE-HOTEL", Dimension.RESILIENCE, Severity.MEDIUM,
            "只有一个酒店候选，缺少可替代方案",
            plan.hotel_options[0].option_id,
            {"candidates": 1},
        ))
    for option in plan.transport_options:
        if option.transfer_count >= _MAX_TRANSFERS:
            issues.append(IssueDraft(
                "RES-MULTI-TRANSFER", Dimension.RESILIENCE, Severity.MEDIUM,
                f"交通 {option.option_id} 需要换乘 {option.transfer_count} 次",
                option.option_id,
                {"transfer_count": option.transfer_count},
            ))
    if plan.hotel_options and all(not item.refundable for item in plan.hotel_options):
        issues.append(IssueDraft(
            "RES-NO-REFUNDABLE-HOTEL", Dimension.RESILIENCE, Severity.MEDIUM,
            "全部酒店候选均不可取消，行程变更风险高",
            "hotel-set",
            {"candidates": len(plan.hotel_options)},
        ))
    return issues


def _preference_rules(plan: ItineraryPlan) -> list[IssueDraft]:
    issues: list[IssueDraft] = []
    budget = _pref_float(plan, "max_budget_cny")
    if budget is not None:
        transport_cost = min((item.price_cny for item in plan.transport_options), default=0.0)
        hotel_costs = [
            item.nightly_price_cny * (item.check_out - item.check_in).days
            for item in plan.hotel_options
        ]
        total = transport_cost + (min(hotel_costs) if hotel_costs else 0.0)
        if total > budget:
            issues.append(IssueDraft(
                "PREF-BUDGET-OVER", Dimension.PREFERENCE, Severity.HIGH,
                f"最低组合成本 {total:.0f} 元超过预算 {budget:.0f} 元",
                "budget",
                {"estimated_cost_cny": round(total, 2), "max_budget_cny": budget},
            ))
    brand = (plan.preferences.get("hotel_brand") or "").strip()
    if brand and not any(brand in item.name for item in plan.hotel_options):
        issues.append(IssueDraft(
            "PREF-BRAND-MISS", Dimension.PREFERENCE, Severity.LOW,
            f"酒店候选未覆盖偏好品牌「{brand}」",
            f"brand:{brand}",
            {"hotel_brand": brand},
        ))
    if _is_truthy(plan, "avoid_red_eye"):
        for option in plan.transport_options:
            if option.depart_at.hour < _EARLY_HOUR or option.arrive_at.hour >= _LATE_HOUR:
                issues.append(IssueDraft(
                    "PREF-RED-EYE", Dimension.PREFERENCE, Severity.MEDIUM,
                    f"交通 {option.option_id} 属于用户希望避免的红眼时段",
                    option.option_id,
                    {"depart_at": option.depart_at.isoformat(),
                     "arrive_at": option.arrive_at.isoformat()},
                ))
    return issues


DEFAULT_RULES: tuple[Rule, ...] = (
    _experience_rules,
    _resilience_rules,
    _preference_rules,
)


class ReviewRuleEngine:
    """规则集可注入，便于评审调参与回归。"""

    def __init__(self, rules: Sequence[Rule] = DEFAULT_RULES, *,
                 rules_version: str = RULES_VERSION) -> None:
        self._rules = tuple(rules)
        self._rules_version = rules_version

    @property
    def rules_version(self) -> str:
        return self._rules_version

    def evaluate(self, plan: ItineraryPlan) -> list[IssueDraft]:
        drafts: list[IssueDraft] = []
        for rule in self._rules:
            drafts.extend(rule(plan))
        # 规则执行顺序与集合遍历顺序均确定，便于复现。
        unique: dict[str, IssueDraft] = {}
        for draft in drafts:
            unique.setdefault(draft.issue_id, draft)
        return sorted(unique.values(), key=lambda item: (item.code, item.target))
