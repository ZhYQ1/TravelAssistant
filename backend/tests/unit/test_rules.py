from app.models import RouteType
from app.routing.rules import (
    CLAUSE_BOUNDARIES,
    DEFAULT_RULES,
    IntentRule,
    RuleMatcher,
    excluded_spans,
)


def test_single_intent_hit_decides_immediately():
    result = RuleMatcher().match("帮我规划下周去上海的出差安排")
    assert result.status == "decided"
    assert result.intent is RouteType.TASK_COORDINATION
    assert result.rule_id == "R-TASK-PLAN"
    assert result.excluded_hits == ()


def test_reverse_exclusion_blocks_knowledge_intent():
    result = RuleMatcher().match("不是查政策，直接帮我改行程")
    assert result.status == "decided"
    assert result.intent is RouteType.TASK_COORDINATION
    assert "政策" in result.excluded_keywords


def test_exclusion_scope_stops_at_clause_boundary():
    text = "不是查政策，报销标准也顺便说一下"
    spans = excluded_spans(text)
    assert spans == [(0, text.index("，"))]
    result = RuleMatcher().match(text)
    # 排除只在首句生效，第二句的知识意图仍然有效
    assert result.status == "decided"
    assert result.intent == RouteType.KNOWLEDGE_QA


def test_all_keywords_excluded_yields_no_match():
    result = RuleMatcher().match("别改行程")
    assert result.status == "no_match"
    assert result.hits == ()
    assert result.excluded_keywords == ["改行程"]


def test_conflicting_intents_do_not_silently_win():
    result = RuleMatcher().match("帮我规划行程，顺便说下报销标准")
    assert result.status == "conflict"
    assert result.intent is None
    assert "task_coordination" in result.reason
    assert "knowledge_qa" in result.reason


def test_postfix_exclusion_covers_preceding_clause():
    result = RuleMatcher().match("政策我不管，帮我把行程调整一下")
    assert result.status == "decided"
    assert result.intent is RouteType.TASK_COORDINATION
    assert "政策" in result.excluded_keywords


def test_postfix_exclusion_does_not_leak_to_next_clause():
    result = RuleMatcher().match("发票我不管，报销标准是多少")
    assert result.status == "decided"
    assert result.intent == RouteType.KNOWLEDGE_QA
    assert "发票" in result.excluded_keywords


def test_custom_rule_set_is_injectable():
    rules = (
        IntentRule("R-CUSTOM", RouteType.WORKFLOW, ("走个流程",)),
    )
    matcher = RuleMatcher(rules)
    assert matcher.match("走个流程吧").rule_id == "R-CUSTOM"
    assert matcher.match("帮我规划").status == "no_match"
    assert matcher.rules == rules


def test_boundaries_and_defaults_are_exposed_for_regression():
    assert "，".startswith(CLAUSE_BOUNDARIES[0])
    assert all(rule.rule_id for rule in DEFAULT_RULES)
    assert {rule.intent for rule in DEFAULT_RULES} == set(RouteType)


def test_rule_id_comes_from_earliest_hit():
    result = RuleMatcher().match("先确认行程，再改签")
    assert result.status == "decided"
    assert result.rule_id == "R-TASK-MODIFY"
