"""Demo 体验 API 的稳定公开契约。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DemoCaseResponse(BaseModel):
    id: str
    label: str
    message: str
    requires_workflow: bool = False


class DemoCasesResponse(BaseModel):
    product: str = "KQ-Z112"
    bom_version: str = "V01"
    cases: list[DemoCaseResponse]


class DemoFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workflow_id: str = Field(min_length=1)
    feedback: str = Field(min_length=1, max_length=1000)


class DemoFeedbackResponse(BaseModel):
    accepted: bool = True
    workflow_id: str
    message: str


class DemoStatisticsResponse(BaseModel):
    total_experience_count: int
    successful_analysis_count: int
    scenario_count: int
    feedback_count: int
