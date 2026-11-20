import asyncio
from datetime import date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.plan.models import (
    DayPlan,
    HotelOption,
    ItineraryPlan,
    TransportMode,
    TransportOption,
)
from app.plan.store import (
    InMemoryPlanStore,
    PlanNotFound,
    PlanService,
    PlanVersionConflict,
)


def run(coro):
    return asyncio.run(coro)


def make_plan(version: int = 1, *, hotel_count: int = 1, transport_count: int = 1) -> ItineraryPlan:
    base = datetime(2026, 12, 1, 8, 0)
    transports = [
        TransportOption(
            option_id=f"T{index}",
            mode=TransportMode.TRAIN,
            carrier="G1001",
            depart_at=base + timedelta(hours=index),
            arrive_at=base + timedelta(hours=3 + index),
            price_cny=553.0,
            transfer_count=0,
        )
        for index in range(transport_count)
    ]
    hotels = [
        HotelOption(
            option_id=f"H{index}",
            name=f"酒店{index}",
            check_in=date(2026, 12, 1),
            check_out=date(2026, 12, 3),
            nightly_price_cny=420.0,
            distance_km=2.5,
        )
        for index in range(hotel_count)
    ]
    first_transport = transports[0].option_id if transports else None
    first_hotel = hotels[0].option_id if hotels else None
    return ItineraryPlan(
        plan_id="p-1",
        user_id="u-1",
        session_id="s-1",
        version=version,
        created_at=datetime(2026, 11, 19, 10, 0),
        transport_options=transports,
        hotel_options=hotels,
        days=[
            DayPlan(day=date(2026, 12, 1), activities=["客户拜访"],
                    transport_option_id=first_transport, hotel_option_id=first_hotel),
            DayPlan(day=date(2026, 12, 2), activities=["复盘"], hotel_option_id=first_hotel),
        ],
        preferences={"max_budget_cny": "2000"},
    )


def load(store, **overrides):
    params = dict(user_id="u-1", session_id="s-1", plan_id="p-1", version=1)
    params.update(overrides)
    return run(store.load(**params))


def test_save_and_load_roundtrip_keeps_nested_candidates():
    store = InMemoryPlanStore()
    plan = make_plan()
    run(store.save(plan))

    loaded = load(store)
    assert loaded == plan
    assert loaded.transport_options[0].arrive_at > loaded.transport_options[0].depart_at
    assert loaded.days[0].hotel_option_id == "H0"


def test_same_version_is_immutable_but_identical_retry_is_idempotent():
    store = InMemoryPlanStore()
    plan = make_plan()
    run(store.save(plan))
    run(store.save(plan))  # 重试同内容不报错

    changed = plan.model_copy(update={"preferences": {"max_budget_cny": "1500"}})
    with pytest.raises(PlanVersionConflict):
        run(store.save(changed))


def test_new_version_becomes_latest_and_old_version_is_rejected_by_default():
    store = InMemoryPlanStore()
    run(store.save(make_plan(version=1)))
    run(store.save(make_plan(version=2)))

    assert run(store.latest_version(user_id="u-1", session_id="s-1", plan_id="p-1")) == 2
    with pytest.raises(PlanVersionConflict):
        load(store, version=1)
    assert load(store, version=1, require_latest=False).version == 1
    assert load(store, version=2).version == 2


def test_missing_plan_and_cross_user_access_are_not_found():
    store = InMemoryPlanStore()
    run(store.save(make_plan()))

    with pytest.raises(PlanNotFound):
        load(store, version=9)
    with pytest.raises(PlanNotFound):
        load(store, user_id="u-2")
    with pytest.raises(PlanNotFound):
        load(store, session_id="s-2")


def test_missing_sections_reports_categories_not_rule_violations():
    plan = make_plan(hotel_count=0, transport_count=0)
    plan = plan.model_copy(update={"days": []})
    assert plan.missing_sections() == ["transport", "hotel", "days"]
    assert make_plan().missing_sections() == []


def test_invalid_times_and_dangling_references_are_rejected_on_write():
    with pytest.raises(ValidationError):
        TransportOption(
            option_id="T1",
            mode=TransportMode.FLIGHT,
            carrier="CA1001",
            depart_at=datetime(2026, 12, 1, 10, 0),
            arrive_at=datetime(2026, 12, 1, 9, 0),
            price_cny=800,
        )
    day = DayPlan(day=date(2026, 12, 1), transport_option_id="NOT-EXIST")
    plan = make_plan().model_copy(update={"days": [day]})
    with pytest.raises(ValidationError):
        ItineraryPlan.model_validate(plan.model_dump())


def test_publish_confirms_readback_and_returns_reference():
    store = InMemoryPlanStore()
    service = PlanService(store)
    ref = run(service.publish(make_plan(version=3)))
    assert (ref.plan_id, ref.version) == ("p-1", 3)
    assert load(store, version=3).version == 3
