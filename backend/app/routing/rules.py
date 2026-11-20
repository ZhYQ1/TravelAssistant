"""L1 规则层：先消解反向排除语义，再判断是否存在无冲突命中。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

from app.models import RouteType

# 反向排除标记：其后的当前分句不再具备正向意图资格
EXCLUSION_MARKERS: tuple[str, ...] = (
    "不是",
    "不是要",
    "不用",
    "不需要",
    "不要",
    "别",
    "无需",
    "没必要",
)

# 后置排除标记：其前面的当前分句不再具备正向意图资格，如“政策我不管”
POSTFIX_EXCLUSION_MARKERS: tuple[str, ...] = (
    "不管",
    "不关心",
    "不考虑",
    "不用管",
)

# 分句边界：排除作用域到边界为止，避免“不是”吞掉整句
CLAUSE_BOUNDARIES: str = "，。；！？、,.;!?\n"


@dataclass(frozen=True)
class IntentRule:
    """一条意图规则：命中关键词则获得该意图资格。"""

    rule_id: str
    intent: RouteType
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class RuleHit:
    rule_id: str
    intent: RouteType
    keyword: str
    position: int


@dataclass(frozen=True)
class L1Result:
    """L1 判定结果；status 为 decided 时才可直接生效。"""

    status: Literal["decided", "no_match", "conflict"]
    intent: RouteType | None = None
    rule_id: str | None = None
    hits: tuple[RuleHit, ...] = field(default_factory=tuple)
    excluded_hits: tuple[RuleHit, ...] = field(default_factory=tuple)
    reason: str = ""

    @property
    def excluded_keywords(self) -> list[str]:
        return [hit.keyword for hit in self.excluded_hits]


DEFAULT_RULES: tuple[IntentRule, ...] = (
    IntentRule(
        rule_id="R-TASK-PLAN",
        intent=RouteType.TASK_COORDINATION,
        keywords=("帮我规划", "重新规划", "规划一下", "安排出差", "做行程"),
    ),
    IntentRule(
        rule_id="R-TASK-MODIFY",
        intent=RouteType.TASK_COORDINATION,
        keywords=(
            "改行程",
            "修改行程",
            "调整行程",
            "行程调整",
            "改签",
            "换个酒店",
            "换酒店",
            "再订",
            "重排",
            "确认行程",
        ),
    ),
    IntentRule(
        rule_id="R-KB-POLICY",
        intent=RouteType.KNOWLEDGE_QA,
        keywords=("报销标准", "差旅标准", "政策", "规定", "制度", "发票"),
    ),
    IntentRule(
        rule_id="R-KB-ADVICE",
        intent=RouteType.KNOWLEDGE_QA,
        keywords=("有什么建议", "怎么选", "能不能带", "注意事项"),
    ),
    IntentRule(
        rule_id="R-WF-APPROVAL",
        intent=RouteType.WORKFLOW,
        keywords=("发起审批", "提交审批", "审批流程", "走流程"),
    ),
    IntentRule(
        rule_id="R-WF-EXPENSE",
        intent=RouteType.WORKFLOW,
        keywords=("提交报销单", "报销申请", "办理报销"),
    ),
)


def excluded_spans(text: str) -> list[tuple[int, int]]:
    """返回被反向排除语义覆盖的字符区间 [start, end)。"""
    spans: list[tuple[int, int]] = []
    for marker in EXCLUSION_MARKERS:
        start = text.find(marker)
        while start != -1:
            cursor = start + len(marker)
            while cursor < len(text) and text[cursor] not in CLAUSE_BOUNDARIES:
                cursor += 1
            spans.append((start, cursor))
            start = text.find(marker, start + len(marker))

    for marker in POSTFIX_EXCLUSION_MARKERS:
        end = text.find(marker)
        while end != -1:
            boundary = max(text.rfind(char, 0, end) for char in CLAUSE_BOUNDARIES)
            spans.append((boundary + 1, end + len(marker)))
            end = text.find(marker, end + len(marker))
    return spans


def _inside(position: int, spans: Sequence[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)


class RuleMatcher:
    """确定性规则匹配器；规则集可注入，便于固定集回归与灰度。"""

    def __init__(self, rules: Sequence[IntentRule] = DEFAULT_RULES) -> None:
        self._rules = tuple(rules)

    @property
    def rules(self) -> tuple[IntentRule, ...]:
        return self._rules

    def match(self, text: str) -> L1Result:
        spans = excluded_spans(text)
        hits: list[RuleHit] = []
        excluded: list[RuleHit] = []

        for rule in self._rules:
            for keyword in rule.keywords:
                start = text.find(keyword)
                while start != -1:
                    hit = RuleHit(rule.rule_id, rule.intent, keyword, start)
                    (excluded if _inside(start, spans) else hits).append(hit)
                    start = text.find(keyword, start + len(keyword))

        if not hits:
            reason = "无有效正向命中"
            if excluded:
                reason += "（存在被反向排除词消解的关键词）"
            return L1Result("no_match", excluded_hits=tuple(excluded), reason=reason)

        grouped: dict[RouteType, list[RuleHit]] = {}
        for hit in hits:
            grouped.setdefault(hit.intent, []).append(hit)

        if len(grouped) > 1:
            intents = "、".join(sorted(intent.value for intent in grouped))
            return L1Result(
                "conflict",
                hits=tuple(hits),
                excluded_hits=tuple(excluded),
                reason=f"多意图同时命中且未被排除：{intents}",
            )

        intent, intent_hits = next(iter(grouped.items()))
        first = min(intent_hits, key=lambda item: item.position)
        return L1Result(
            "decided",
            intent=intent,
            rule_id=first.rule_id,
            hits=tuple(hits),
            excluded_hits=tuple(excluded),
            reason=f"规则 {first.rule_id} 命中 {first.keyword}",
        )
