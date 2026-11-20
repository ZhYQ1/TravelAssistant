"""Route requests to isolated AgentScope agents and explicitly bounded demos."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol

from agentscope.agent import ReActAgent
from agentscope.message import Msg
from agentscope.model import ChatModelBase
from pydantic import BaseModel, Field

from app.config import Settings
from app.loop.continuation import (
    DEFAULT_TASK_ID,
    ActiveAgentSession,
    ContinuationAction,
    ContinuationStore,
)
from app.models import Candidate, DecisionLayer, RouteDecision, RouteType
from app.routing.chain import RouteChain

LOGGER = logging.getLogger(__name__)


class AgentFactory(Protocol):
    def __call__(self, route_type: RouteType) -> ReActAgent: ...


class JevNoulClient(Protocol):
    async def decide_noul(
        self,
        *,
        state: str,
        instructions: str,
        true_meaning: str,
        false_meaning: str,
    ) -> float: ...


class AgentMessageRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=64)
    utterance: str = Field(min_length=1, max_length=2000)
    trace_id: str | None = Field(default=None, max_length=64)


class AgentMessageResponse(BaseModel):
    decision: RouteDecision
    agent_id: str
    mode: Literal["draft", "general_generated", "mock", "clarify"]
    content: str
    simulation: bool = False
    policy_verified: bool = False
    live_inventory_checked: bool = False
    bookable: bool = False
    source_label: str


@dataclass
class _SessionAgent:
    agent: ReActAgent
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class _ActiveRoute:
    route_type: RouteType
    last_utterance: str


_AGENT_IDS = {
    RouteType.TASK_COORDINATION: "task_coordination_agent",
    RouteType.KNOWLEDGE_QA: "knowledge_qa_agent",
    RouteType.WORKFLOW: "workflow_agent",
}


class AgentExecutionCoordinator:
    def __init__(
        self,
        chain: RouteChain,
        agent_factory: AgentFactory,
        *,
        noul_client: JevNoulClient | None = None,
        confidence_threshold: float = 0.75,
        session_ttl_seconds: int = 3600,
        max_sessions: int = 256,
        continuation_store: ContinuationStore | None = None,
        continuation_ttl_seconds: int = 3600,
    ) -> None:
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        if session_ttl_seconds < 1 or max_sessions < 1:
            raise ValueError("session_ttl_seconds and max_sessions must be positive")
        self._chain = chain
        self._agent_factory = agent_factory
        self._noul_client = noul_client
        self._confidence_threshold = confidence_threshold
        self._session_ttl = session_ttl_seconds
        self._max_sessions = max_sessions
        self._continuation_store = continuation_store
        self._continuation_ttl = continuation_ttl_seconds
        self._agents: dict[tuple[str, str, RouteType], _SessionAgent] = {}
        self._active_routes: dict[tuple[str, str], _ActiveRoute] = {}
        self._agents_lock = asyncio.Lock()

    async def handle(self, request: AgentMessageRequest) -> AgentMessageResponse:
        decision = await self._continue_active_agent(request)
        if decision is None:
            decision = await self._chain.route(
                request.utterance,
                trace_id=request.trace_id,
            )
        if decision.route_type is None:
            return AgentMessageResponse(
                decision=decision,
                agent_id="clarification",
                mode="clarify",
                content=decision.clarification or "请补充你的差旅需求。",
                source_label="路由澄清",
            )

        agent = await self._session_agent(
            request.user_id,
            request.session_id,
            decision.route_type,
        )
        async with agent.lock:
            result = await agent.agent(Msg("user", request.utterance, "user"))
            content = _message_text(result)
            agent.last_used = time.monotonic()
        async with self._agents_lock:
            self._active_routes[(request.user_id, request.session_id)] = _ActiveRoute(
                decision.route_type,
                request.utterance,
            )

        if decision.route_type is RouteType.TASK_COORDINATION:
            await self._activate_draft(request, content)
            return AgentMessageResponse(
                decision=decision,
                agent_id=_AGENT_IDS[decision.route_type],
                mode="draft",
                content=content,
                source_label="模型生成草案 · 无实时库存",
            )
        if decision.route_type is RouteType.WORKFLOW:
            return AgentMessageResponse(
                decision=decision,
                agent_id=_AGENT_IDS[decision.route_type],
                mode="mock",
                content=content,
                simulation=True,
                source_label="Mock 流程办理",
            )
        return AgentMessageResponse(
            decision=decision,
            agent_id=_AGENT_IDS[decision.route_type],
            mode="general_generated",
            content=content,
            source_label="Mock 大模型自动生成",
        )

    async def handle_stream(self, request: AgentMessageRequest):
        """流式执行：先路由，再以 SSE 事件逐段产出 Agent 回复文本。

        与 ``handle`` 共享路由与会话 Agent 缓存，仅把「一次返回完整正文」
        换成「先 meta、再 delta 增量、最后 done」的事件序列。
        """
        decision = await self._continue_active_agent(request)
        if decision is None:
            decision = await self._chain.route(
                request.utterance,
                trace_id=request.trace_id,
            )

        if decision.route_type is None:
            response = AgentMessageResponse(
                decision=decision,
                agent_id="clarification",
                mode="clarify",
                content=decision.clarification or "请补充你的差旅需求。",
                source_label="路由澄清",
            )
            yield _sse("meta", _meta_event(response))
            yield _sse("done", response.model_dump())
            return

        agent_id = _AGENT_IDS[decision.route_type]
        if decision.route_type is RouteType.TASK_COORDINATION:
            mode = "draft"
            source_label = "模型生成草案 · 无实时库存"
            simulation = False
        elif decision.route_type is RouteType.WORKFLOW:
            mode = "mock"
            source_label = "Mock 流程办理"
            simulation = True
        else:
            mode = "general_generated"
            source_label = "Mock 大模型自动生成"
            simulation = False

        yield _sse(
            "meta",
            _meta_event(
                AgentMessageResponse(
                    decision=decision,
                    agent_id=agent_id,
                    mode=mode,
                    content="",
                    simulation=simulation,
                    source_label=source_label,
                )
            ),
        )

        agent = await self._session_agent(
            request.user_id,
            request.session_id,
            decision.route_type,
        )

        queue: asyncio.Queue = asyncio.Queue()
        task: asyncio.Task | None = None
        content = ""
        try:
            async with agent.lock:
                agent.agent.set_msg_queue_enabled(True, queue)
                try:
                    task = asyncio.create_task(
                        agent.agent(Msg("user", request.utterance, "user"))
                    )
                    seen = ""
                    while not (task.done() and queue.empty()):
                        try:
                            chunk_msg, _last, _speech = await asyncio.wait_for(
                                queue.get(), timeout=0.05
                            )
                        except asyncio.TimeoutError:
                            continue
                        text = _extract_text(chunk_msg)
                        if text and len(text) > len(seen):
                            delta = text[len(seen) :]
                            seen = text
                            yield _sse("delta", {"content": delta})
                    result = await task
                    content = _message_text(result)
                    agent.last_used = time.monotonic()
                finally:
                    agent.agent.set_msg_queue_enabled(False)
        finally:
            if task is not None and not task.done():
                task.cancel()

        async with self._agents_lock:
            self._active_routes[(request.user_id, request.session_id)] = _ActiveRoute(
                decision.route_type,
                request.utterance,
            )

        if decision.route_type is RouteType.TASK_COORDINATION:
            await self._activate_draft(request, content)

        yield _sse(
            "done",
            AgentMessageResponse(
                decision=decision,
                agent_id=agent_id,
                mode=mode,
                content=content,
                simulation=simulation,
                source_label=source_label,
            ).model_dump(),
        )

    async def _activate_draft(self, request: AgentMessageRequest, content: str) -> None:
        """草案产出后登记活跃会话，让前端可按阶段展示确认/修改并直达续跑。

        仅当注入了延续存储时生效；写入失败不得影响主回复。
        """
        if self._continuation_store is None:
            return
        session = ActiveAgentSession.for_phase(
            session_id=request.session_id,
            user_id=request.user_id,
            agent_id=_AGENT_IDS[RouteType.TASK_COORDINATION],
            task_id=DEFAULT_TASK_ID,
            phase="awaiting_confirmation",
            draft=content,
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=self._continuation_ttl),
        )
        try:
            await self._continuation_store.save_session(session)
        except Exception as exc:  # noqa: BLE001 - 会话写入失败不影响主流程
            LOGGER.warning(
                "persist active continuation session failed: %s", type(exc).__name__
            )

    async def generate_draft(
        self,
        request: AgentMessageRequest,
        *,
        action: ContinuationAction,
        instruction: str | None,
        base_draft: str | None,
    ) -> str:
        """不经路由，直接调用任务协调子 Agent 基于当前草案再生成一版。"""
        agent = await self._session_agent(
            request.user_id, request.session_id, RouteType.TASK_COORDINATION
        )
        prompt = _revision_prompt(action, instruction, base_draft)
        async with agent.lock:
            result = await agent.agent(Msg("user", prompt, "user"))
            content = _message_text(result)
            agent.last_used = time.monotonic()
        return content

    async def _continue_active_agent(
        self,
        request: AgentMessageRequest,
    ) -> RouteDecision | None:
        session_key = (request.user_id, request.session_id)
        async with self._agents_lock:
            active = self._active_routes.get(session_key)
            active_key = (
                (request.user_id, request.session_id, active.route_type)
                if active is not None
                else None
            )
            agent = self._agents.get(active_key) if active_key is not None else None
            if (
                active is not None
                and agent is not None
                and time.monotonic() - agent.last_used > self._session_ttl
            ):
                self._remove_agent(active_key)
                active = None
        if active is None or agent is None:
            return None

        # 优先走 Jev Noul 智能判断：由快判模型决定当前消息是否延续活跃任务。
        if self._noul_client is not None:
            state = json.dumps(
                {
                    "active_agent": _AGENT_IDS[active.route_type],
                    "previous_request": active.last_utterance[:1200],
                    "current_message": request.utterance[:1200],
                },
                ensure_ascii=False,
            )
            try:
                probability = await self._noul_client.decide_noul(
                    state=state,
                    instructions=(
                        "判断当前消息是否延续同一会话中的活跃差旅任务，"
                        "应由当前子 Agent 直接处理，而不必重新进行主意图路由。"
                    ),
                    true_meaning="当前消息是在补充、修改、确认或追问上一个差旅任务。",
                    false_meaning="当前消息是新主题、独立请求，或无法确认与上一个任务相关。",
                )
            except Exception as exc:  # noqa: BLE001 - Noul 故障时走确定性兜底。
                LOGGER.warning("Jev Noul continuation check failed: %s", type(exc).__name__)
            else:
                if (
                    not isinstance(probability, bool)
                    and isinstance(probability, (int, float))
                    and math.isfinite(probability)
                    and 0.0 < probability <= 1.0
                    and probability >= self._confidence_threshold
                ):
                    return RouteDecision(
                        trace_id=request.trace_id or uuid.uuid4().hex,
                        route_type=active.route_type,
                        confidence=probability,
                        decision_layer=DecisionLayer.JEV,
                        candidates=[Candidate(route_type=active.route_type, score=probability)],
                        fallback_reason="active_agent_continuation",
                    )

        # 确定性兜底：无 Jev Noul 或 Noul 未确认时，用 L1 规则判断当前消息是否
        # 携带“明确的新意图信号”。L1 未命中（no_match）说明用户大概率是在补充、
        # 修改或追问上一轮任务，此时直达续跑当前子 Agent，减少主 Agent 重复调度。
        l1 = self._chain.matcher.match(request.utterance)
        if l1.status == "no_match":
            return RouteDecision(
                trace_id=request.trace_id or uuid.uuid4().hex,
                route_type=active.route_type,
                confidence=0.9,
                decision_layer=DecisionLayer.L1_RULE,
                candidates=[Candidate(route_type=active.route_type, score=0.9)],
                fallback_reason="active_agent_continuation:l1_no_match",
            )
        return None

    async def _session_agent(
        self,
        user_id: str,
        session_id: str,
        route_type: RouteType,
    ) -> _SessionAgent:
        now = time.monotonic()
        key = (user_id, session_id, route_type)
        async with self._agents_lock:
            expired = [
                candidate for candidate, entry in self._agents.items()
                if now - entry.last_used > self._session_ttl
            ]
            for candidate in expired:
                self._remove_agent(candidate)

            entry = self._agents.get(key)
            if entry is None:
                if len(self._agents) >= self._max_sessions:
                    oldest = min(self._agents, key=lambda item: self._agents[item].last_used)
                    self._remove_agent(oldest)
                entry = _SessionAgent(self._agent_factory(route_type))
                self._agents[key] = entry
            entry.last_used = now
            return entry

    def _remove_agent(self, key: tuple[str, str, RouteType]) -> None:
        del self._agents[key]
        session_key = (key[0], key[1])
        active = self._active_routes.get(session_key)
        if active is not None and active.route_type is key[2]:
            del self._active_routes[session_key]


def build_agent_factory(
    settings: Settings,
    *,
    model: ChatModelBase | None = None,
) -> AgentFactory:
    from agentscope.formatter import OpenAIChatFormatter
    from agentscope.memory import InMemoryMemory
    from agentscope.model import OpenAIChatModel

    chat_model = model or OpenAIChatModel(
        model_name=settings.llm_model,
        api_key=settings.llm_api_key,
        stream=True,
        client_kwargs={
            "base_url": settings.llm_base_url,
            "timeout": settings.llm_timeout_seconds,
        },
        generate_kwargs={
            "temperature": settings.llm_temperature,
            "extra_body": {"enable_thinking": False},
        },
    )
    prompts = {
        RouteType.TASK_COORDINATION: (
            "你是差旅行程协调 Agent。根据用户给出的目的地、出行时间与预算，"
            "直接给出简洁可行的行程安排建议。缺少日期、目的地或预算等关键信息时先询问。"
        ),
        RouteType.KNOWLEDGE_QA: (
            "你是差旅知识问答 Agent。根据常见差旅政策、报销标准与审批规则，"
            "直接回答用户的问题，回答简洁实用。"
        ),
        RouteType.WORKFLOW: (
            "你是差旅流程办理 Agent。根据用户输入推测其需要办理的审批、报销或票务流程，"
            "直接给出需要准备的材料与办理步骤，回答简洁。"
        ),
    }

    def create(route_type: RouteType) -> ReActAgent:
        return ReActAgent(
            name=_AGENT_IDS[route_type],
            sys_prompt=prompts[route_type],
            model=chat_model,
            formatter=OpenAIChatFormatter(),
            memory=InMemoryMemory(),
            max_iters=1,
        )

    return create


def _message_text(message: Msg) -> str:
    text_blocks = message.get_content_blocks("text")
    text = "\n".join(
        block["text"]
        for block in text_blocks
        if isinstance(block.get("text"), str)
    ).strip()
    if not text:
        raise RuntimeError("AgentScope agent returned no text response.")
    return text


def _revision_prompt(
    action: ContinuationAction,
    instruction: str | None,
    base_draft: str | None,
) -> str:
    """构造「修订草案」指令：让子 Agent 直接产出新一版草案，而非重走路由。"""
    lines = ["请基于上一版行程草案，重新生成一版完整的行程草案。"]
    if action is ContinuationAction.REPLAN:
        lines.append("用户希望换一版方案，请给出替代安排并说明与原方案的差异。")
    detail = (instruction or "").strip()
    if detail:
        lines.append(f"修改意见：{detail}")
    elif action is ContinuationAction.MODIFY:
        lines.append("用户希望调整当前草案，请在保持整体可行的前提下优化关键安排。")
    if base_draft:
        lines.append(f"上一版草案参考：\n{base_draft}")
    lines.append("只输出修订后的草案正文，不要解释修改过程。")
    return "\n\n".join(lines)


def _extract_text(message: Msg) -> str:
    """提取流式 chunk 中的文本，空内容不抛异常。"""
    text_blocks = message.get_content_blocks("text")
    return "\n".join(
        block["text"]
        for block in text_blocks
        if isinstance(block.get("text"), str)
    ).strip()


def _meta_event(response: AgentMessageResponse) -> dict:
    """meta 事件携带除正文外的全部元数据，供前端先建立气泡骨架。"""
    data = response.model_dump()
    data.pop("content", None)
    return data


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"