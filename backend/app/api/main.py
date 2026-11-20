"""HTTP 入口：路由、方案、审核、续跑与指标接口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

from agentscope.agent import ReActAgent
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents.orchestrator import (
    AgentExecutionCoordinator,
    AgentMessageRequest,
    AgentMessageResponse,
    build_agent_factory,
)
from app.config import ConfigError, Settings
from app.context.folding import RedisSkillContentStore, SkillContentCollapseHook
from app.loop.continuation import (
    DEFAULT_TASK_ID,
    ActionOption,
    ActiveAgentSession,
    ContinuationAction,
    ContinuationRequest,
    ContinuationResult,
    ContinuationService,
    ContinuationStore,
    RedisContinuationStore,
    TaskContinuationHandler,
    actions_for_phase,
)
from app.models import RouteDecision
from app.observability.metrics import MetricsRegistry
from app.plan.extract import DraftExtractor
from app.plan.models import (
    DraftPlanRequest,
    DraftPlanResponse,
    ItineraryPlan,
    PlanReference,
    PlanSummary,
)
from app.plan.store import (
    PlanNotFound,
    PlanService,
    PlanStore,
    PlanStoreError,
    PlanVersionConflict,
    RedisPlanStore,
)
from app.review.models import ReviewOutcome, ReviewReport
from app.review.orchestrator import ReviewOrchestrator, ReviewVersionConflict
from app.review.state import (
    RedisReviewStateStore,
    ReviewCancelled,
    ReviewStateError,
    ReviewStateMachine,
)
from app.review.store import RedisReviewReportStore
from app.routing.audit import RedisAuditSink
from app.routing.chain import RouteChain
from app.routing.jev import JevAdapterClient, JevRouter
from app.routing.llm_router import LLMRouter
from app.routing.rules import RuleMatcher
from app.routing.vector import RedisIntentVectorStore, VectorRouter
from app.skill.adapter import (
    RedisCredentialResolver,
    RedisSkillStateStore,
    SkillAdapter,
)
from app.skill.agentscope_hook import build_skill_agent


class RouteRequest(BaseModel):
    utterance: str = Field(min_length=1, max_length=500)
    trace_id: str | None = Field(default=None, max_length=64)


class ReviewRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    trace_id: str | None = Field(default=None, max_length=64)


class ContinuationState(BaseModel):
    """对话页读取活跃会话上下文，用于按阶段渲染确认/修改按钮。"""

    active: bool = False
    phase: str | None = None
    agent_id: str | None = None
    task_id: str | None = None
    plan_id: str | None = None
    plan_version: int | None = None
    options: list[ActionOption] = Field(default_factory=list)


@dataclass
class AppServices:
    chain: RouteChain
    plan_store: PlanStore
    plan_service: PlanService
    review_orchestrator: ReviewOrchestrator
    continuation_store: ContinuationStore
    continuation_service: ContinuationService
    skill_adapter: SkillAdapter
    content_hook: SkillContentCollapseHook
    metrics: MetricsRegistry
    skill_agent: ReActAgent | None = None
    agent_coordinator: AgentExecutionCoordinator | None = None
    draft_extractor: DraftExtractor | None = None
    continuation_ttl_seconds: int = 3600


def build_jev_client(settings: Settings) -> JevAdapterClient:
    """按配置装配 TypeSafe Jev 客户端（官方 Python SDK 直连）。"""
    return JevAdapterClient(
        api_key=settings.typesafe_api_key or None,
        model=settings.jev_model,
        base_url=settings.jev_base_url,
        timeout_seconds=settings.jev_timeout_seconds,
    )


def build_chain(settings: Settings, *, metrics: MetricsRegistry | None = None,
                redis_client=None) -> RouteChain:
    """按配置装配路由依赖：Redis + Embedding + 官方 Jev + LLM 回退。"""
    import redis.asyncio as redis_async

    from app.llm.client import HttpChatClient
    from app.llm.embedding import HttpEmbedder

    client = redis_client or redis_async.from_url(settings.redis_url)
    llm = HttpChatClient(
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_model,
        temperature=settings.llm_temperature,
        timeout_seconds=settings.llm_timeout_seconds,
        usage_sink=metrics,
    )
    embedder = HttpEmbedder(
        settings.embedding_base_url,
        settings.embedding_api_key,
        settings.embedding_model,
        timeout_seconds=settings.embedding_timeout_seconds,
    )
    return RouteChain(
        matcher=RuleMatcher(),
        vector_router=VectorRouter(
            embedder,
            RedisIntentVectorStore(client, settings.key_prefix, settings.audit_ttl_seconds),
            model_name=settings.embedding_model,
            min_score=settings.vector_min_score,
            margin=settings.vector_margin,
        ),
        jev=JevRouter(
            build_jev_client(settings),
            confidence_threshold=settings.jev_confidence_threshold,
        ),
        llm_router=LLMRouter(llm),
        audit=RedisAuditSink(client, settings.key_prefix, settings.audit_ttl_seconds),
        metrics=metrics,
    )


class _CoordinatorDraftRegenerator:
    """把续跑处理器接到子 Agent：修改/重规划时直接再生成一版草案。"""

    def __init__(self, coordinator: AgentExecutionCoordinator) -> None:
        self._coordinator = coordinator

    async def regenerate(
        self,
        *,
        session: ActiveAgentSession,
        action: ContinuationAction,
        instruction: str | None,
    ) -> str:
        request = AgentMessageRequest(
            user_id=session.user_id,
            session_id=session.session_id,
            utterance=(instruction or "").strip(),
            trace_id=None,
        )
        return await self._coordinator.generate_draft(
            request,
            action=action,
            instruction=instruction,
            base_draft=session.draft,
        )


def build_services(settings: Settings) -> AppServices:
    """生产装配：各链路共享 Redis，但使用独立逻辑 Key 空间。"""
    import redis.asyncio as redis_async

    client = redis_async.from_url(settings.redis_url)
    metrics = MetricsRegistry()
    chain = build_chain(settings, metrics=metrics, redis_client=client)
    plan_store = RedisPlanStore(client, settings.key_prefix, settings.plan_ttl_seconds)
    reviewer = ReviewOrchestrator(
        plan_store,
        RedisReviewReportStore(client, settings.key_prefix, settings.review_ttl_seconds),
        RedisReviewStateStore(client, settings.key_prefix, settings.review_ttl_seconds),
        state_machine=ReviewStateMachine(settings.max_review_rounds),
        metrics=metrics,
    )
    continuation_store = RedisContinuationStore(
        client, settings.key_prefix, settings.continuation_ttl_seconds
    )
    agent_coordinator = AgentExecutionCoordinator(
        chain,
        build_agent_factory(settings),
        noul_client=build_jev_client(settings),
        confidence_threshold=settings.jev_confidence_threshold,
        continuation_store=continuation_store,
        continuation_ttl_seconds=settings.continuation_ttl_seconds,
    )
    continuation_service = ContinuationService(
        continuation_store,
        {
            "task_coordination_agent": TaskContinuationHandler(
                continuation_store,
                reviewer,
                regenerator=_CoordinatorDraftRegenerator(agent_coordinator),
            )
        },
    )
    skill_adapter = SkillAdapter(
        RedisCredentialResolver(client, settings.key_prefix),
        RedisSkillStateStore(client, settings.key_prefix, settings.skill_state_ttl_seconds),
        base_dir=settings.skill_base_dir,
        default_timeout_seconds=settings.skill_timeout_seconds,
    )
    content_hook = SkillContentCollapseHook(
        RedisSkillContentStore(client, settings.key_prefix, settings.skill_content_ttl_seconds),
        collapse_after_turns=settings.collapse_after_turns,
    )
    skill_agent = build_skill_agent(settings, skill_adapter, content_hook=content_hook)
    from app.llm.client import HttpChatClient

    draft_extractor = DraftExtractor(
        HttpChatClient(
            settings.llm_base_url,
            settings.llm_api_key,
            settings.llm_model,
            temperature=0.0,
            timeout_seconds=max(settings.llm_timeout_seconds, 20.0),
            usage_sink=metrics,
        )
    )
    return AppServices(
        chain=chain,
        plan_store=plan_store,
        plan_service=PlanService(plan_store),
        review_orchestrator=reviewer,
        continuation_store=continuation_store,
        continuation_service=continuation_service,
        skill_adapter=skill_adapter,
        content_hook=content_hook,
        metrics=metrics,
        skill_agent=skill_agent,
        agent_coordinator=agent_coordinator,
        draft_extractor=draft_extractor,
        continuation_ttl_seconds=settings.continuation_ttl_seconds,
    )


_REVIEW_PHASE = {
    ReviewOutcome.CHANGES_REQUIRED: "changes_required",
    ReviewOutcome.APPROVED: "awaiting_confirmation",
}


async def _activate_reviewed_plan(
    services: AppServices, *, user_id: str, session_id: str,
    plan_id: str, version: int, outcome: ReviewOutcome,
) -> None:
    """审核产出后登记活跃会话，使前端可按阶段展示确认/修改并直达续跑。"""
    phase = _REVIEW_PHASE.get(outcome)
    if phase is None:
        return
    session = ActiveAgentSession.for_phase(
        session_id=session_id,
        user_id=user_id,
        agent_id="task_coordination_agent",
        task_id=DEFAULT_TASK_ID,
        phase=phase,
        plan_id=plan_id,
        plan_version=version,
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=services.continuation_ttl_seconds),
    )
    try:
        await services.continuation_store.save_session(session)
    except Exception:  # noqa: BLE001 - 会话写入失败不影响审核结论
        pass


def create_app(
    chain_factory: Callable[[], RouteChain] | None = None,
    services_factory: Callable[[], AppServices] | None = None,
) -> FastAPI:
    app = FastAPI(title="Travel Assistant Router", version="0.2.0")
    state: dict[str, RouteChain | AppServices | None] = {"chain": None, "services": None}

    def resolve_services() -> AppServices:
        if state["services"] is None:
            try:
                state["services"] = (
                    services_factory or (lambda: build_services(Settings.from_env()))
                )()
            except ConfigError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        return state["services"]  # type: ignore[return-value]

    def resolve_chain() -> RouteChain:
        if chain_factory is not None:
            if state["chain"] is None:
                try:
                    state["chain"] = chain_factory()
                except ConfigError as exc:
                    raise HTTPException(status_code=503, detail=str(exc)) from exc
            return state["chain"]  # type: ignore[return-value]
        return resolve_services().chain

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/route", response_model=RouteDecision)
    async def route(payload: RouteRequest) -> RouteDecision:
        return await resolve_chain().route(payload.utterance, trace_id=payload.trace_id)

    @app.post("/v1/assistant/message", response_model=AgentMessageResponse)
    async def assistant_message(payload: AgentMessageRequest) -> AgentMessageResponse:
        coordinator = resolve_services().agent_coordinator
        if coordinator is None:
            raise HTTPException(status_code=503, detail="Agent execution is not configured.")
        return await coordinator.handle(payload)

    @app.post("/v1/assistant/message/stream")
    async def assistant_message_stream(payload: AgentMessageRequest) -> StreamingResponse:
        coordinator = resolve_services().agent_coordinator
        if coordinator is None:
            raise HTTPException(status_code=503, detail="Agent execution is not configured.")
        return StreamingResponse(
            coordinator.handle_stream(payload),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/v1/plans", response_model=PlanReference, status_code=201)
    async def create_plan(plan: ItineraryPlan) -> PlanReference:
        try:
            return await resolve_services().plan_service.publish(plan)
        except PlanVersionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PlanStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/plans/draft", response_model=DraftPlanResponse, status_code=201)
    async def create_draft_plan(payload: DraftPlanRequest) -> DraftPlanResponse:
        services = resolve_services()
        raw: dict = {}
        if services.draft_extractor is not None:
            raw = await services.draft_extractor.extract(payload.draft)
        facts = DraftExtractor.normalize(raw, payload.draft)
        plan = DraftExtractor.build_plan(
            facts=facts,
            draft=payload.draft.strip(),
            plan_id=payload.plan_id or f"plan-{uuid4().hex[:12]}",
            user_id=payload.user_id,
            session_id=payload.session_id,
            version=payload.version or 1,
            created_at=datetime.now(timezone.utc),
        )
        try:
            await services.plan_service.publish(plan)
        except PlanVersionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PlanStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return DraftPlanResponse(
            plan_id=plan.plan_id,
            version=plan.version,
            title=plan.preferences.get("title", ""),
            created_at=plan.created_at,
            start_date=facts.get("start_date"),
            days=facts.get("days"),
            budget_cny=facts.get("budget_cny"),
            hotel_area=facts.get("hotel_area", ""),
        )

    @app.get("/v1/plans", response_model=list[PlanSummary])
    async def list_plans(user_id: str, session_id: str,
                         limit: int = 20) -> list[PlanSummary]:
        try:
            return await resolve_services().plan_service.list_summaries(
                user_id=user_id, session_id=session_id, limit=max(1, min(limit, 50))
            )
        except PlanStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/plans/{plan_id}", response_model=ItineraryPlan)
    async def get_plan(plan_id: str, user_id: str, session_id: str,
                       version: int | None = None) -> ItineraryPlan:
        store = resolve_services().plan_store
        try:
            resolved_version = version or await store.latest_version(
                user_id=user_id, session_id=session_id, plan_id=plan_id
            )
            if resolved_version is None:
                raise PlanNotFound(f"方案 {plan_id} 不存在或已过期")
            return await store.load(
                user_id=user_id, session_id=session_id, plan_id=plan_id,
                version=resolved_version, require_latest=False,
            )
        except PlanNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PlanStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/plans/{plan_id}/reviews", response_model=ReviewReport)
    async def review_plan(plan_id: str, payload: ReviewRequest) -> ReviewReport:
        services = resolve_services()
        try:
            report = await services.review_orchestrator.review(
                user_id=payload.user_id, session_id=payload.session_id,
                plan_id=plan_id, version=payload.version, trace_id=payload.trace_id,
            )
        except (ReviewCancelled, ReviewVersionConflict) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ReviewStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await _activate_reviewed_plan(
            services,
            user_id=payload.user_id,
            session_id=payload.session_id,
            plan_id=plan_id,
            version=payload.version,
            outcome=report.outcome,
        )
        return report

    @app.post("/v1/plans/{plan_id}/cancel")
    async def cancel_review(plan_id: str, payload: ReviewRequest) -> dict:
        try:
            task = await resolve_services().review_orchestrator.cancel(
                user_id=payload.user_id, session_id=payload.session_id,
                plan_id=plan_id, version=payload.version,
            )
            return task.to_dict()
        except ReviewStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/continuation/actions", response_model=list[ActionOption])
    async def continuation_actions(session_id: str, user_id: str) -> list[ActionOption]:
        session = await resolve_services().continuation_store.get_session(session_id)
        if (
            session is None
            or session.user_id != user_id
            or session.expires_at <= datetime.now(timezone.utc)
        ):
            return []
        return [
            option for option in actions_for_phase(session.phase)
            if option.action in session.allowed_actions
        ]

    @app.get("/v1/continuation/state", response_model=ContinuationState)
    async def continuation_state(session_id: str, user_id: str) -> ContinuationState:
        """返回活跃会话的完整上下文，供对话页按阶段渲染并直达续跑。"""
        session = await resolve_services().continuation_store.get_session(session_id)
        if (
            session is None
            or session.user_id != user_id
            or session.expires_at <= datetime.now(timezone.utc)
        ):
            return ContinuationState()
        return ContinuationState(
            active=True,
            phase=session.phase,
            agent_id=session.agent_id,
            task_id=session.task_id,
            plan_id=session.plan_id,
            plan_version=session.plan_version,
            options=[
                option for option in actions_for_phase(session.phase)
                if option.action in session.allowed_actions
            ],
        )

    @app.post("/v1/continuation/resume", response_model=ContinuationResult)
    async def resume(payload: ContinuationRequest) -> ContinuationResult:
        services = resolve_services()
        result = await services.continuation_service.resume(payload)
        if not result.duplicate:
            try:
                services.metrics.observe_continuation(result)
            except Exception:  # noqa: BLE001
                pass
        return result

    @app.get("/metrics")
    async def metrics() -> dict:
        return resolve_services().metrics.snapshot()

    return app


app = create_app()