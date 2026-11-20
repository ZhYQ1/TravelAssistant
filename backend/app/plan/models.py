"""行程方案领域模型：完整方案经 Redis 中转，审核只读取版本化快照。"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class TransportMode(str, Enum):
    TRAIN = "train"
    FLIGHT = "flight"
    OTHER = "other"


class TransportOption(BaseModel):
    """一条交通候选；审核依赖这里的出发/到达时间与可替代性信息。"""

    option_id: str = Field(min_length=1, max_length=64)
    mode: TransportMode
    carrier: str = Field(min_length=1, max_length=64)
    depart_at: datetime
    arrive_at: datetime
    price_cny: float = Field(ge=0)
    transfer_count: int = Field(default=0, ge=0)
    refundable: bool = True

    @model_validator(mode="after")
    def _validate_time_order(self) -> "TransportOption":
        if self.arrive_at <= self.depart_at:
            raise ValueError(f"交通候选 {self.option_id} 的到达时间必须晚于出发时间")
        return self


class HotelOption(BaseModel):
    """一条酒店候选；入住日期用于覆盖日程校验。"""

    option_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    check_in: date
    check_out: date
    nightly_price_cny: float = Field(ge=0)
    distance_km: float = Field(default=0.0, ge=0)
    refundable: bool = True

    @model_validator(mode="after")
    def _validate_stay(self) -> "HotelOption":
        if self.check_out <= self.check_in:
            raise ValueError(f"酒店候选 {self.option_id} 的离店日期必须晚于入住日期")
        return self


class DayPlan(BaseModel):
    """一天的安排：引用交通与酒店候选，避免在对话上下文里重复完整信息。"""

    day: date
    activities: list[str] = Field(default_factory=list)
    transport_option_id: Optional[str] = Field(default=None, max_length=64)
    hotel_option_id: Optional[str] = Field(default=None, max_length=64)


class ItineraryPlan(BaseModel):
    """同一版本内容不可变；任何修改必须写入新版本。"""

    plan_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    created_at: datetime
    transport_options: list[TransportOption] = Field(default_factory=list)
    hotel_options: list[HotelOption] = Field(default_factory=list)
    days: list[DayPlan] = Field(default_factory=list)
    preferences: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_references(self) -> "ItineraryPlan":
        transport_ids = [item.option_id for item in self.transport_options]
        if len(transport_ids) != len(set(transport_ids)):
            raise ValueError("交通候选 option_id 重复")
        hotel_ids = [item.option_id for item in self.hotel_options]
        if len(hotel_ids) != len(set(hotel_ids)):
            raise ValueError("酒店候选 option_id 重复")

        day_keys = [item.day for item in self.days]
        if len(day_keys) != len(set(day_keys)):
            raise ValueError("日程日期重复")

        transport_set, hotel_set = set(transport_ids), set(hotel_ids)
        for item in self.days:
            if item.transport_option_id and item.transport_option_id not in transport_set:
                raise ValueError(f"{item.day} 引用了不存在的交通候选 {item.transport_option_id}")
            if item.hotel_option_id and item.hotel_option_id not in hotel_set:
                raise ValueError(f"{item.day} 引用了不存在的酒店候选 {item.hotel_option_id}")
        return self

    def missing_sections(self) -> list[str]:
        """返回缺失的必需部分；缺项是 DATA_INCOMPLETE，而不是“候选不合规”。"""
        missing: list[str] = []
        if not self.transport_options:
            missing.append("transport")
        if not self.hotel_options:
            missing.append("hotel")
        if not self.days:
            missing.append("days")
        return missing


class PlanReference(BaseModel):
    """对话上下文只需携带引用与版本。"""

    plan_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)


class PlanSummary(BaseModel):
    """行程列表条目：仅暴露可展示字段，避免列表接口传输完整方案。"""

    plan_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    title: str = Field(default="", max_length=128)
    created_at: datetime


class DraftPlanRequest(BaseModel):
    """对话草案落库请求：由服务端抽取结构化字段后发布方案。"""

    user_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=64)
    draft: str = Field(min_length=1, max_length=20000)
    plan_id: Optional[str] = Field(default=None, max_length=64)
    version: Optional[int] = Field(default=None, ge=1)


class DraftPlanResponse(PlanSummary):
    start_date: Optional[str] = None
    days: Optional[int] = None
    budget_cny: Optional[float] = None
    hotel_area: str = Field(default="", max_length=64)
