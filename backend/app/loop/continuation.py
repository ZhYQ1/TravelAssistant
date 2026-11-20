"""阶段化动作词表与活跃子 Agent 直达续跑。"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Mapping, Optional, Protocol

from pydantic import BaseModel, Field


# 任务协调 Agent 在会话内的稳定任务标识，前后端续跑请求需保持一致。
DEFAULT_TASK_ID = "task-active"


class ContinuationAction(str, Enum):
    CONFIRM = "confirm"
    MODIFY = "modify"
    REPLAN = "replan"
    CANCEL = "cancel"


class ActionOption(BaseModel):
    action: ContinuationAction
    label: str


_ACTION_LABELS = {
    ContinuationAction.CONFIRM: "确认",
    ContinuationAction.MODIFY: "修改",
    ContinuationAction.REPLAN: "重新规划",
    ContinuationAction.CANCEL: "取消",
}

# 词表按阶段配置，互不矛盾的确认/修改动作不会同时出现在其他阶段。
PHASE_ACTIONS: dict[str, tuple[ContinuationAction, ...]] = {
    "awaiting_confirmation": (
        ContinuationAction.CONFIRM,
        ContinuationAction.MODIFY,
        ContinuationAction.CANCEL,
    ),
    "changes_required": (
        ContinuationAction.MODIFY,
        ContinuationAction.REPLAN,
        ContinuationAction.CANCEL,
    ),
    "reviewing": (ContinuationAction.CANCEL,),
    "approved": (),
}


def actions_for_phase(phase: str) -> list[ActionOption]:
    """无活跃阶段时不生成续跑动作，由调用方转入普通路由。"""
    return [
        ActionOption(action=action, label=_ACTION_LABELS[action])
        for action in PHASE_ACTIONS.get(phase, ())
    ]


class ActiveAgentSession(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    agent_id: str = Field(min_length=1, max_length=64)
    task_id: str = Field(min_length=1, max_length=64)
    phase: str = Field(min_length=1, max_length=64)
    plan_id: Optional[str] = Field(default=None, max_length=64)
    plan_version: Optional[int] = Field(default=None, ge=1)
    draft: Optional[str] = Field(default=None, max_length=20000)
    allowed_actions: list[ContinuationAction] = Field(default_factory=list)
    expires_at: datetime

    @classmethod
    def for_phase(
        cls, *, session_id: str, user_id: str, agent_id: str, task_id: str,
        phase: str, expires_at: datetime, plan_id: str | None = None,
        plan_version: int | None = None, draft: str | None = None,
    ) -> "ActiveAgentSession":
        return cls(
            session_id=session_id, user_id=user_id, agent_id=agent_id,
            task_id=task_id, phase=phase, plan_id=plan_id,
            plan_version=plan_version, draft=draft,
            allowed_actions=[item.action for item in actions_for_phase(phase)],
            expires_at=expires_at,
        )


class ContinuationRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    agent_id: str = Field(min_length=1, max_length=64)
    task_id: str = Field(min_length=1, max_length=64)
    action: ContinuationAction
    click_id: str = Field(min_length=1, max_length=64)
    plan_id: Optional[str] = Field(default=None, max_length=64)
    plan_version: Optional[int] = Field(default=None, ge=1)
    instruction: Optional[str] = Field(default=None, max_length=2000)


class ContinuationResult(BaseModel):
    status: Literal["resumed", "fallback", "processing"]
    session_id: str
    action: ContinuationAction
    agent_id: Optional[str] = None
    task_id: Optional[str] = None
    reason: Optional[str] = None
    error_code: Optional[str] = None
    duplicate: bool = False
    output: dict[str, Any] = Field(default_factory=dict)


class ContinuationHandler(Protocol):
    async def handle(self, *, session: ActiveAgentSession,
                     action: ContinuationAction,
                     instruction: str | None = None) -> Mapping[str, Any]:
        """直接续跑活跃子 Agent，不经过主 Agent 路由。"""


class DraftRegenerator(Protocol):
    async def regenerate(self, *, session: ActiveAgentSession,
                         action: ContinuationAction,
                         instruction: str | None) -> str:
        """由活跃子 Agent 基于当前草案重新生成一版草案正文。"""


def _as_str(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


class ContinuationStore(Protocol):
    async def save_session(self, session: ActiveAgentSession) -> None: ...
    async def get_session(self, session_id: str) -> ActiveAgentSession | None: ...
    async def get_click(self, *, user_id: str, session_id: str,
                        click_id: str) -> ContinuationResult | None: ...
    async def begin_click(self, *, user_id: str, session_id: str, click_id: str) -> bool: ...
    async def finish_click(self, *, user_id: str, session_id: str, click_id: str,
                           result: ContinuationResult) -> None: ...
    async def abort_click(self, *, user_id: str, session_id: str, click_id: str) -> None: ...


class InMemoryContinuationStore:
    def __init__(self) -> None:
        self._sessions: dict[str, str] = {}
        self._clicks: dict[str, str] = {}

    def _click_key(self, user_id: str, session_id: str, click_id: str) -> str:
        return f"{user_id}:{session_id}:{click_id}"

    async def save_session(self, session: ActiveAgentSession) -> None:
        self._sessions[session.session_id] = session.model_dump_json()

    async def get_session(self, session_id: str) -> ActiveAgentSession | None:
        raw = self._sessions.get(session_id)
        return ActiveAgentSession.model_validate_json(raw) if raw else None

    async def get_click(self, *, user_id: str, session_id: str,
                        click_id: str) -> ContinuationResult | None:
        raw = self._clicks.get(self._click_key(user_id, session_id, click_id))
        if not raw or raw == "processing":
            return None
        return ContinuationResult.model_validate_json(raw)

    async def begin_click(self, *, user_id: str, session_id: str, click_id: str) -> bool:
        key = self._click_key(user_id, session_id, click_id)
        if key in self._clicks:
            return False
        self._clicks[key] = "processing"
        return True

    async def finish_click(self, *, user_id: str, session_id: str, click_id: str,
                           result: ContinuationResult) -> None:
        self._clicks[self._click_key(user_id, session_id, click_id)] = result.model_dump_json()

    async def abort_click(self, *, user_id: str, session_id: str, click_id: str) -> None:
        self._clicks.pop(self._click_key(user_id, session_id, click_id), None)


class RedisContinuationStore:
    def __init__(self, redis, prefix: str, ttl_seconds: int) -> None:
        self._redis = redis
        self._prefix = prefix
        self._ttl = ttl_seconds

    def _session_key(self, session_id: str) -> str:
        return f"{self._prefix}:active_session:{session_id}"

    def _click_key(self, user_id: str, session_id: str, click_id: str) -> str:
        return f"{self._prefix}:click:{user_id}:{session_id}:{click_id}"

    async def save_session(self, session: ActiveAgentSession) -> None:
        ttl = max(int((session.expires_at - datetime.now(timezone.utc)).total_seconds()), 1)
        await self._redis.set(
            self._session_key(session.session_id), session.model_dump_json(), ex=ttl
        )

    async def get_session(self, session_id: str) -> ActiveAgentSession | None:
        raw = await self._redis.get(self._session_key(session_id))
        return None if raw is None else ActiveAgentSession.model_validate_json(_as_str(raw))

    async def get_click(self, *, user_id: str, session_id: str,
                        click_id: str) -> ContinuationResult | None:
        raw = await self._redis.get(self._click_key(user_id, session_id, click_id))
        if raw is None or _as_str(raw) == "processing":
            return None
        return ContinuationResult.model_validate_json(_as_str(raw))

    async def begin_click(self, *, user_id: str, session_id: str, click_id: str) -> bool:
        return bool(await self._redis.set(
            self._click_key(user_id, session_id, click_id), "processing",
            ex=self._ttl, nx=True,
        ))

    async def finish_click(self, *, user_id: str, session_id: str, click_id: str,
                           result: ContinuationResult) -> None:
        await self._redis.set(
            self._click_key(user_id, session_id, click_id), result.model_dump_json(),
            ex=self._ttl, xx=True,
        )

    async def abort_click(self, *, user_id: str, session_id: str, click_id: str) -> None:
        await self._redis.delete(self._click_key(user_id, session_id, click_id))


class ReviewActions(Protocol):
    async def begin_replan(self, *, user_id: str, session_id: str,
                           plan_id: str, version: int) -> Any: ...
    async def cancel(self, *, user_id: str, session_id: str,
                     plan_id: str, version: int) -> Any: ...


class TaskContinuationHandler:
    """任务协调 Agent 的确定性续跑入口：只推进当前任务，不做意图重路由。"""

    def __init__(self, store: ContinuationStore,
                 reviewer: ReviewActions | None = None,
                 regenerator: DraftRegenerator | None = None) -> None:
        self._store = store
        self._reviewer = reviewer
        self._regenerator = regenerator

    async def handle(self, *, session: ActiveAgentSession,
                     action: ContinuationAction,
                     instruction: str | None = None) -> Mapping[str, Any]:
        draft: str | None = None
        if action is ContinuationAction.CONFIRM:
            phase = "approved"
        elif action is ContinuationAction.MODIFY:
            if self._regenerator is not None and session.draft:
                draft = await self._regenerator.regenerate(
                    session=session, action=action, instruction=instruction,
                )
                phase = "awaiting_confirmation"
            else:
                phase = "changes_required"
        elif action is ContinuationAction.REPLAN:
            if self._reviewer is not None and session.plan_id and session.plan_version:
                await self._reviewer.begin_replan(
                    user_id=session.user_id, session_id=session.session_id,
                    plan_id=session.plan_id, version=session.plan_version,
                )
            if self._regenerator is not None and session.draft:
                draft = await self._regenerator.regenerate(
                    session=session, action=action, instruction=instruction,
                )
                phase = "awaiting_confirmation"
            else:
                phase = "replanning"
        else:
            if self._reviewer is not None and session.plan_id and session.plan_version:
                await self._reviewer.cancel(
                    user_id=session.user_id, session_id=session.session_id,
                    plan_id=session.plan_id, version=session.plan_version,
                )
            phase = "cancelled"

        updated = session.model_copy(update={
            "phase": phase,
            "draft": draft if draft is not None else session.draft,
            "allowed_actions": [item.action for item in actions_for_phase(phase)],
        })
        await self._store.save_session(updated)
        return {
            "phase": phase,
            "plan_id": session.plan_id,
            "plan_version": session.plan_version,
            "draft": updated.draft,
        }


class ContinuationService:
    def __init__(self, store: ContinuationStore,
                 handlers: Mapping[str, ContinuationHandler]) -> None:
        self._store = store
        self._handlers = dict(handlers)

    async def resume(self, request: ContinuationRequest) -> ContinuationResult:
        click = dict(user_id=request.user_id, session_id=request.session_id,
                     click_id=request.click_id)
        cached = await self._store.get_click(**click)
        if cached is not None:
            return cached.model_copy(update={"duplicate": True})
        if not await self._store.begin_click(**click):
            cached = await self._store.get_click(**click)
            if cached is not None:
                return cached.model_copy(update={"duplicate": True})
            return ContinuationResult(
                status="processing", session_id=request.session_id,
                action=request.action, reason="同一点击正在处理中",
            )

        try:
            result = await self._dispatch(request)
        except Exception:
            await self._store.abort_click(**click)
            raise
        await self._store.finish_click(result=result, **click)
        return result

    async def _dispatch(self, request: ContinuationRequest) -> ContinuationResult:
        session = await self._store.get_session(request.session_id)

        def fallback(reason: str, error_code: str) -> ContinuationResult:
            return ContinuationResult(
                status="fallback", session_id=request.session_id,
                action=request.action, reason=reason, error_code=error_code,
            )

        if session is None:
            return fallback("活跃会话不存在或已过期", "SESSION_EXPIRED")
        if session.expires_at <= datetime.now(timezone.utc):
            return fallback("活跃会话已过期，请重新描述需求", "SESSION_EXPIRED")
        if session.user_id != request.user_id:
            return fallback("无权操作该会话", "FORBIDDEN")
        if session.agent_id != request.agent_id or session.task_id != request.task_id:
            return fallback("活跃 Agent 或任务已变化，转入主路由", "ACTIVE_TASK_CHANGED")
        if request.action not in session.allowed_actions:
            return fallback("当前阶段不允许该动作", "ACTION_NOT_ALLOWED")
        if session.plan_id is not None and (
            request.plan_id != session.plan_id
            or request.plan_version != session.plan_version
        ):
            return fallback(
                f"计划版本已变化，当前版本为 {session.plan_version}，请查看最新方案",
                "VERSION_CONFLICT",
            )

        handler = self._handlers.get(session.agent_id)
        if handler is None:
            return fallback("活跃子 Agent 当前不可用", "AGENT_UNAVAILABLE")
        output = await handler.handle(
            session=session, action=request.action, instruction=request.instruction,
        )
        return ContinuationResult(
            status="resumed", session_id=session.session_id, action=request.action,
            agent_id=session.agent_id, task_id=session.task_id,
            output=dict(output),
        )
