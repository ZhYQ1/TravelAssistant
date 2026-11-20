import asyncio
from datetime import date, datetime, timedelta

import pytest

from app.plan.models import (
    DayPlan,
    HotelOption,
    ItineraryPlan,
    PlanReference,
    TransportMode,
    TransportOption,
)
from app.plan.store import InMemoryPlanStore
from app.review.models import ReviewOutcome
from app.review.orchestrator import ReviewOrchestrator, ReviewVersionConflict
from app.review.rules import ReviewRuleEngine
from app.review.tool import REVIEW_TOOL_NAME, ReviewTool, build_review_toolkit
from app.review.state import (
    InMemoryReviewStateStore,
    RequiresUserDecision,
    ReviewCancelled,
    ReviewState,
    ReviewStateMachine,
)
from app.review.store import InMemoryReviewReportStore


def run(coro):
    return asyncio.run(coro)


def _clean_plan(version: int = 1) -> ItineraryPlan:
    base = datetime(2026, 12, 1, 9, 0)
    return ItineraryPlan(
        plan_id="p-1",
        user_id="u-1",
        session_id="s-1",
        version=version,
        created_at=datetime(2026, 11, 19, 10, 0),
        transport_options=[
            TransportOption(option_id="T1", mode=TransportMode.TRAIN, carrier="G1",
                            depart_at=base, arrive_at=base + timedelta(hours=3),
                            price_cny=500),
            TransportOption(option_id="T2", mode=TransportMode.FLIGHT, carrier="CA1",
                            depart_at=base + timedelta(hours=1),
                            arrive_at=base + timedelta(hours=4), price_cny=600),
        ],
        hotel_options=[
            HotelOption(option_id="H1", name="测试酒店A", check_in=date(2026, 12, 1),
                        check_out=date(2026, 12, 3), nightly_price_cny=400, distance_km=2.5),
            HotelOption(option_id="H2", name="测试酒店B", check_in=date(2026, 12, 1),
                        check_out=date(2026, 12, 3), nightly_price_cny=450, distance_km=3.0),
        ],
        days=[
            DayPlan(day=date(2026, 12, 1), activities=["拜访"],
                    transport_option_id="T1", hotel_option_id="H1"),
            DayPlan(day=date(2026, 12, 2), activities=["复盘"], hotel_option_id="H1"),
        ],
        preferences={"max_budget_cny": "5000", "hotel_brand": "测试", "avoid_red_eye": "0"},
    )


def _problem_plan(version: int = 1) -> ItineraryPlan:
    base = datetime(2026, 12, 1, 5, 0)
    return ItineraryPlan(
        plan_id="p-1",
        user_id="u-1",
        session_id="s-1",
        version=version,
        created_at=datetime(2026, 11, 19, 10, 0),
        transport_options=[
            TransportOption(option_id="T1", mode=TransportMode.TRAIN, carrier="G9",
                            depart_at=base, arrive_at=base + timedelta(hours=18, minutes=30),
                            price_cny=1500, transfer_count=2),
        ],
        hotel_options=[
            HotelOption(option_id="H1", name="远酒店", check_in=date(2026, 12, 1),
                        check_out=date(2026, 12, 3), nightly_price_cny=800,
                        distance_km=12, refundable=False),
        ],
        days=[
            DayPlan(day=date(2026, 12, 1), activities=["拜访"], transport_option_id="T1"),
            DayPlan(day=date(2026, 12, 2), activities=["复盘"], hotel_option_id="H1"),
        ],
        preferences={"max_budget_cny": "1000", "hotel_brand": "近", "avoid_red_eye": "1"},
    )


def build(max_rounds: int = 3):
    plans = InMemoryPlanStore()
    reports = InMemoryReviewReportStore()
    states = InMemoryReviewStateStore()
    orchestrator = ReviewOrchestrator(
        plans, reports, states,
        engine=ReviewRuleEngine(),
        state_machine=ReviewStateMachine(max_review_rounds=max_rounds),
    )
    return plans, reports, states, orchestrator


def review(orchestrator, version: int = 1):
    return run(orchestrator.review(user_id="u-1", session_id="s-1",
                                   plan_id="p-1", version=version))


def test_clean_plan_is_approved_without_issues():
    plans, _, _, orchestrator = build()
    run(plans.save(_clean_plan()))
    report = review(orchestrator)
    assert report.outcome is ReviewOutcome.APPROVED
    assert report.issues == []
    assert report.passed is True
    assert report.plan_version == 1


def test_problem_plan_has_deterministic_priorities_and_evidence():
    plans, _, _, orchestrator = build()
    run(plans.save(_problem_plan()))
    first = review(orchestrator)
    second = review(orchestrator)

    assert first.outcome is ReviewOutcome.CHANGES_REQUIRED
    assert first.review_id == second.review_id  # 相同版本 + 规则版本幂等
    codes = [issue.code for issue in first.issues]
    assert "RES-SINGLE-TRANSPORT" in codes
    assert "EXP-NO-OVERNIGHT" in codes
    assert "PREF-BUDGET-OVER" in codes
    assert [issue.priority for issue in first.issues] == list(range(1, len(codes) + 1))
    assert first.issues[0].code == "RES-SINGLE-TRANSPORT"
    assert all(issue.evidence for issue in first.issues)
    assert first.issues == second.issues


def test_missing_plan_returns_data_incomplete_and_can_retry():
    plans, _, states, orchestrator = build()
    failed = review(orchestrator)
    assert failed.outcome is ReviewOutcome.REVIEW_FAILED
    assert failed.error_code == "DATA_INCOMPLETE"
    task = run(states.get(user_id="u-1", session_id="s-1", plan_id="p-1"))
    assert task.state is ReviewState.REVIEW_FAILED

    run(plans.save(_clean_plan()))
    recovered = review(orchestrator)
    assert recovered.outcome is ReviewOutcome.APPROVED


def test_incomplete_plan_reports_missing_categories_not_failures():
    plans, _, _, orchestrator = build()
    plan = _clean_plan().model_copy(update={"hotel_options": []})
    plan = plan.model_dump()
    plan["days"] = [{"day": "2026-12-01", "activities": ["拜访"], "transport_option_id": "T1"},
                    {"day": "2026-12-02", "activities": ["复盘"]}]
    run(plans.save(ItineraryPlan.model_validate(plan)))
    report = review(orchestrator)
    assert report.outcome is ReviewOutcome.REVIEW_FAILED
    assert report.error_code == "DATA_INCOMPLETE"
    assert "hotel" in (report.error_message or "")


def test_stale_version_is_rejected_and_new_version_rolls_forward():
    plans, _, states, orchestrator = build()
    run(plans.save(_problem_plan(version=1)))
    review(orchestrator, version=1)
    run(plans.save(_clean_plan(version=2)))

    with pytest.raises(ReviewVersionConflict):
        review(orchestrator, version=1)

    report = review(orchestrator, version=2)
    task = run(states.get(user_id="u-1", session_id="s-1", plan_id="p-1"))
    assert report.plan_version == 2
    assert task.replan_round == 1
    assert task.review_round == 2
    assert report.outcome is ReviewOutcome.APPROVED


def test_history_repeat_count_raises_priority_before_decision_required():
    plans, _, _, orchestrator = build(max_rounds=2)
    run(plans.save(_problem_plan(version=1)))
    review(orchestrator, version=1)
    run(plans.save(_problem_plan(version=2)))
    report = review(orchestrator, version=2)

    repeated = {issue.code: issue for issue in report.issues}
    assert repeated["RES-SINGLE-TRANSPORT"].repeat_count == 1
    assert repeated["RES-SINGLE-TRANSPORT"].priority_score > 33
    assert report.requires_user_decision is True
    assert report.review_round == 2

    with pytest.raises(RequiresUserDecision):
        run(orchestrator.begin_replan(
            user_id="u-1", session_id="s-1", plan_id="p-1", version=2
        ))


def test_cancelled_review_publishes_no_further_result():
    plans, _, _, orchestrator = build()
    run(plans.save(_problem_plan()))
    review(orchestrator)
    run(orchestrator.cancel(user_id="u-1", session_id="s-1", plan_id="p-1", version=1))

    with pytest.raises(ReviewCancelled):
        review(orchestrator)


def test_multi_plan_review_binds_each_report_to_its_version():
    plans, _, _, orchestrator = build()
    run(plans.save(_clean_plan()))
    second = _clean_plan().model_copy(update={"plan_id": "p-2", "version": 1})
    run(plans.save(second))

    reports = run(orchestrator.review_many(
        [PlanReference(plan_id="p-1", version=1), PlanReference(plan_id="p-2", version=1)],
        user_id="u-1", session_id="s-1",
    ))
    assert [(item.plan_id, item.plan_version) for item in reports] == [
        ("p-1", 1), ("p-2", 1)
    ]
    assert all(item.outcome is ReviewOutcome.APPROVED for item in reports)


def test_review_tool_reviews_by_reference_key_without_full_plan_in_context():
    plans, _, _, orchestrator = build()
    run(plans.save(_clean_plan()))
    tool = ReviewTool(orchestrator)

    report = run(tool.review_by_reference(
        PlanReference(plan_id="p-1", version=1),
        user_id="u-1", session_id="s-1",
    ))
    assert report.outcome is ReviewOutcome.APPROVED
    assert report.plan_id == "p-1"
    assert report.plan_version == 1


def test_review_toolkit_registers_review_tool_for_agents():
    _, _, _, orchestrator = build()
    toolkit = build_review_toolkit(ReviewTool(orchestrator))
    assert REVIEW_TOOL_NAME in toolkit.tools