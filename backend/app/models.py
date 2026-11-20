"""路由领域模型：所有判定层最终归一化为 RouteDecision。"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field, model_validator


class RouteType(str, Enum):
    """三类 Agent 路由目标。"""

    TASK_COORDINATION = "task_coordination"
    KNOWLEDGE_QA = "knowledge_qa"
    WORKFLOW = "workflow"


class DecisionLayer(str, Enum):
    """生成本次决策的层级，用于审计与回归分析。"""

    L1_RULE = "l1_rule"
    L2_VECTOR = "l2_vector"
    JEV = "jev"
    LLM = "llm"
    CLARIFY = "clarify"


class Candidate(BaseModel):
    """某一层的候选意图及其分数。"""

    route_type: RouteType
    score: float = Field(ge=0.0, le=1.0)


class RouteDecision(BaseModel):
    """一次输入的唯一生效路由决策。"""

    trace_id: str = Field(min_length=1)
    route_type: Optional[RouteType] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    decision_layer: DecisionLayer
    fallback: bool = False
    fallback_reason: Optional[str] = None
    clarification: Optional[str] = None
    rule_id: Optional[str] = None
    candidates: list[Candidate] = Field(default_factory=list)
    latency_ms: Optional[float] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_contract(self) -> "RouteDecision":
        if self.decision_layer is DecisionLayer.CLARIFY:
            if self.route_type is not None:
                raise ValueError("澄清决策不得携带 route_type")
            if not self.clarification:
                raise ValueError("澄清决策必须给出 clarification 文案")
            return self
        if self.route_type is None:
            raise ValueError("已路由决策必须给出 route_type")
        if self.confidence <= 0:
            raise ValueError("已路由决策的 confidence 必须大于 0")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def target_agent(self) -> str:
        """路由目标 Agent 名称，与 RouteType 一一对应。"""
        if self.route_type is None:
            return "clarification"
        return {
            RouteType.TASK_COORDINATION: "task_coordination_agent",
            RouteType.KNOWLEDGE_QA: "knowledge_qa_agent",
            RouteType.WORKFLOW: "workflow_agent",
        }[self.route_type]


def clarify(trace_id: str, reason: str, question: str, *,
            fallback: bool = True, fallback_reason: Optional[str] = None) -> RouteDecision:
    """构造无法判定时的安全结果：不猜测目标 Agent。"""
    return RouteDecision(
        trace_id=trace_id,
        decision_layer=DecisionLayer.CLARIFY,
        clarification=question,
        fallback=fallback,
        fallback_reason=fallback_reason or reason,
    )