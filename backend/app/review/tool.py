"""ReviewTool：按约定 Key 从 Redis 读取完整方案并审核。

对话上下文只携带方案引用（plan_id + version），完整方案（含交通/酒店候选）
经 Redis 中转；审核工具按 Key 读取版本化快照，避免候选信息在上下文传递中丢失。
"""

from __future__ import annotations

import json
from typing import Any

from agentscope.message import TextBlock
from agentscope.tool import Toolkit, ToolResponse

from app.plan.models import PlanReference
from app.review.models import ReviewReport
from app.review.orchestrator import ReviewOrchestrator

REVIEW_TOOL_NAME = "review_travel_plan"


class ReviewTool:
    """把审核编排封装为可按约定 Key 调用的工具。

    Key 由 user_id / session_id / plan_id / version 组成；工具内部从存储读取
    完整方案并审核，调用方（Agent 或上下文）只需持有引用，无需传递完整方案。
    """

    def __init__(self, orchestrator: ReviewOrchestrator) -> None:
        self._orchestrator = orchestrator

    async def review(
        self,
        *,
        user_id: str,
        session_id: str,
        plan_id: str,
        version: int,
        trace_id: str | None = None,
    ) -> ReviewReport:
        return await self._orchestrator.review(
            user_id=user_id,
            session_id=session_id,
            plan_id=plan_id,
            version=version,
            trace_id=trace_id,
        )

    async def review_by_reference(
        self,
        reference: PlanReference,
        *,
        user_id: str,
        session_id: str,
        trace_id: str | None = None,
    ) -> ReviewReport:
        """按 PlanReference 这一约定 Key 审核，只读引用不读上下文。"""
        return await self.review(
            user_id=user_id,
            session_id=session_id,
            plan_id=reference.plan_id,
            version=reference.version,
            trace_id=trace_id,
        )


def _report_payload(report: ReviewReport) -> dict[str, Any]:
    return report.model_dump(mode="json")


def build_review_toolkit(tool: ReviewTool) -> Toolkit:
    """注册 review_travel_plan 工具：Agent 按 Key 触发审核，不传完整方案。"""
    toolkit = Toolkit()

    async def review_travel_plan(
        user_id: str,
        session_id: str,
        plan_id: str,
        version: int,
    ) -> ToolResponse:
        report = await tool.review(
            user_id=user_id,
            session_id=session_id,
            plan_id=plan_id,
            version=version,
        )
        return ToolResponse(content=[
            TextBlock(
                type="text",
                text=json.dumps(_report_payload(report), ensure_ascii=False),
            ),
        ])

    toolkit.register_tool_function(
        review_travel_plan,
        func_name=REVIEW_TOOL_NAME,
        func_description=(
            "按方案 Key（plan_id + version）从 Redis 读取完整行程方案并审核；"
            "只传递引用，不得把完整方案塞进对话上下文。"
        ),
    )
    return toolkit