"""审核领域模型：结论只允许通过、需整改或失败。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, computed_field


class Dimension(str, Enum):
    EXPERIENCE = "experience"
    RESILIENCE = "resilience"
    PREFERENCE = "preference"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return {"low": 1, "medium": 2, "high": 3, "critical": 4}[self.value]


class ReviewOutcome(str, Enum):
    APPROVED = "approved"
    CHANGES_REQUIRED = "changes_required"
    REVIEW_FAILED = "review_failed"


class ReviewIssue(BaseModel):
    """每个整改项必须携带规则证据与可解释的优先级。"""

    issue_id: str
    code: str
    dimension: Dimension
    severity: Severity
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    repeat_count: int = Field(default=0, ge=0)
    priority: int = Field(default=0, ge=0)
    priority_score: int = 0


class ReviewReport(BaseModel):
    review_id: str
    trace_id: str
    user_id: str
    session_id: str
    plan_id: str
    plan_version: int = Field(ge=1)
    rules_version: str
    outcome: ReviewOutcome
    issues: list[ReviewIssue] = Field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    requires_user_decision: bool = False
    review_round: int = Field(default=1, ge=1)
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return self.outcome is ReviewOutcome.APPROVED
