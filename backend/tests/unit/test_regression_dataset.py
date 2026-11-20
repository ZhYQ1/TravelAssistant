"""固定回归集的结构校验与 L1 不变量校验（离线可跑）。"""

from tests.conftest import load_regression_cases

from app.models import RouteType
from app.routing.rules import RuleMatcher

ALLOWED = {item.value for item in RouteType} | {"clarify"}


def test_dataset_has_exactly_60_unique_cases():
    cases = load_regression_cases()
    assert len(cases) == 60
    ids = [case["id"] for case in cases]
    assert len(set(ids)) == 60


def test_dataset_labels_and_coverage():
    cases = load_regression_cases()
    assert all(case["expected"] in ALLOWED for case in cases)
    assert all(case["utterance"].strip() for case in cases)
    distribution = {label: 0 for label in ALLOWED}
    for case in cases:
        distribution[case["expected"]] += 1
    assert distribution["clarify"] >= 5
    assert all(distribution[label] >= 8 for label in ALLOWED - {"clarify"})
    assert len({case["category"] for case in cases}) >= 5


def test_l1_never_contradicts_expected_label():
    """L1 一旦给出结论就必须与标注一致；未决时交给 L2/Jev/LLM。"""
    matcher = RuleMatcher()
    violations = []
    for case in load_regression_cases():
        result = matcher.match(case["utterance"])
        if result.status == "decided" and result.intent is not None:
            if result.intent.value != case["expected"]:
                violations.append(
                    f"{case['id']} {case['utterance']} -> {result.intent.value} "
                    f"(expected {case['expected']})"
                )
    assert not violations, "\n".join(violations)


def test_l1_handles_reverse_exclusion_cases():
    cases = {case["id"]: case for case in load_regression_cases()}
    matcher = RuleMatcher()

    negated = matcher.match(cases["R41"]["utterance"])
    assert negated.status == "decided"
    assert negated.intent is RouteType.TASK_COORDINATION
    assert "政策" in negated.excluded_keywords

    second_clause = matcher.match(cases["R48"]["utterance"])
    assert second_clause.status == "decided"
    assert second_clause.intent is RouteType.KNOWLEDGE_QA


def test_conflicting_case_is_left_to_lower_layers():
    cases = {case["id"]: case for case in load_regression_cases()}
    result = RuleMatcher().match(cases["R47"]["utterance"])
    assert result.status == "conflict"
