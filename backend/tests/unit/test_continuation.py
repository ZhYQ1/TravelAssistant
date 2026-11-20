import asyncio
from datetime import datetime, timedelta, timezone

from app.loop.continuation import (
    ActiveAgentSession,
    ContinuationAction,
    ContinuationRequest,
    ContinuationService,
    InMemoryContinuationStore,
    TaskContinuationHandler,
    actions_for_phase,
)


def run(coro):
    return asyncio.run(coro)


class FakeHandler:
    def __init__(self):
        self.calls = []

    async def handle(self, *, session, action, instruction=None):
        self.calls.append((session.session_id, session.agent_id, session.task_id, action))
        return {"continued": True, "action": action.value, "plan_version": session.plan_version}


def build(phase="awaiting_confirmation", **session_overrides):
    store = InMemoryContinuationStore()
    handler = FakeHandler()
    params = dict(
        session_id="s-1", user_id="u-1", agent_id="task_agent", task_id="task-1",
        phase=phase, plan_id="p-1", plan_version=2,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    params.update(session_overrides)
    session = ActiveAgentSession.for_phase(**params)
    run(store.save_session(session))
    return store, handler, ContinuationService(store, {"task_agent": handler})


def request(**overrides):
    params = dict(
        session_id="s-1", user_id="u-1", agent_id="task_agent", task_id="task-1",
        action=ContinuationAction.CONFIRM, click_id="click-1",
        plan_id="p-1", plan_version=2,
    )
    params.update(overrides)
    return ContinuationRequest(**params)


def test_phase_vocabulary_only_exposes_current_actions():
    awaiting = [item.action for item in actions_for_phase("awaiting_confirmation")]
    assert awaiting == [ContinuationAction.CONFIRM, ContinuationAction.MODIFY,
                        ContinuationAction.CANCEL]
    assert [item.action for item in actions_for_phase("changes_required")] == [
        ContinuationAction.MODIFY, ContinuationAction.REPLAN, ContinuationAction.CANCEL
    ]
    assert actions_for_phase("approved") == []
    assert actions_for_phase("no-active-task") == []


def test_valid_click_resumes_same_agent_without_main_routing():
    _, handler, service = build()
    result = run(service.resume(request()))
    assert result.status == "resumed"
    assert result.agent_id == "task_agent"
    assert result.task_id == "task-1"
    assert result.output == {"continued": True, "action": "confirm", "plan_version": 2}
    assert len(handler.calls) == 1


def test_repeated_click_id_returns_first_result_without_side_effect():
    _, handler, service = build()
    first = run(service.resume(request()))
    second = run(service.resume(request()))
    assert second.status == first.status == "resumed"
    assert second.duplicate is True
    assert second.output == first.output
    assert len(handler.calls) == 1


def test_pending_click_returns_processing():
    store, handler, service = build()
    run(store.begin_click(user_id="u-1", session_id="s-1", click_id="click-1"))
    result = run(service.resume(request()))
    assert result.status == "processing"
    assert handler.calls == []


def test_expired_or_missing_session_falls_back_to_main_route():
    _, handler, service = build(
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    expired = run(service.resume(request()))
    assert expired.status == "fallback"
    assert expired.error_code == "SESSION_EXPIRED"

    missing = run(service.resume(request(session_id="not-exist", click_id="click-2")))
    assert missing.status == "fallback"
    assert handler.calls == []


def test_wrong_user_agent_or_action_is_rejected():
    _, handler, service = build()
    forbidden = run(service.resume(request(user_id="u-2")))
    assert forbidden.error_code == "FORBIDDEN"

    changed = run(service.resume(request(agent_id="other_agent", click_id="click-2")))
    assert changed.error_code == "ACTIVE_TASK_CHANGED"

    not_allowed = run(service.resume(request(action=ContinuationAction.REPLAN,
                                             click_id="click-3")))
    assert not_allowed.error_code == "ACTION_NOT_ALLOWED"
    assert handler.calls == []


def test_confirm_never_applies_to_stale_plan_version():
    _, handler, service = build()
    stale = run(service.resume(request(plan_version=1)))
    assert stale.status == "fallback"
    assert stale.error_code == "VERSION_CONFLICT"
    assert handler.calls == []

    latest = run(service.resume(request(click_id="click-latest")))
    assert latest.status == "resumed"
    assert len(handler.calls) == 1


def test_no_allowed_action_in_reviewing_phase_except_cancel():
    _, handler, service = build(phase="reviewing")
    confirm = run(service.resume(request()))
    assert confirm.error_code == "ACTION_NOT_ALLOWED"
    cancel = run(service.resume(request(action=ContinuationAction.CANCEL,
                                        click_id="click-cancel")))
    assert cancel.status == "resumed"


def test_task_handler_advances_session_and_calls_review_boundaries():
    store, _, _ = build(phase="changes_required")

    class Reviewer:
        def __init__(self):
            self.calls = []

        async def begin_replan(self, **kwargs):
            self.calls.append(("replan", kwargs))

        async def cancel(self, **kwargs):
            self.calls.append(("cancel", kwargs))

    reviewer = Reviewer()
    handler = TaskContinuationHandler(store, reviewer)
    session = run(store.get_session("s-1"))

    replanned = run(handler.handle(session=session, action=ContinuationAction.REPLAN))
    assert replanned["phase"] == "replanning"
    assert reviewer.calls[0][0] == "replan"
    assert reviewer.calls[0][1]["version"] == 2

    run(handler.handle(session=session, action=ContinuationAction.CANCEL))
    assert reviewer.calls[1][0] == "cancel"
    updated = run(store.get_session("s-1"))
    assert updated.phase == "cancelled"
    assert updated.allowed_actions == []
