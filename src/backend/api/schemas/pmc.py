"""PMC HTTP API 的稳定公开请求与响应契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api.schemas.errors import PublicErrorResponse


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product: str = Field(min_length=1, examples=["KQ-Z112"])
    bom_version: str = Field(min_length=1, examples=["V01"])
    quantity: int = Field(gt=0, examples=[1])


class PurchaseParameterValues(BaseModel):
    """单一物料本次补充的采购字段；允许逐项补充。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    supplier_code: str | None = Field(default=None, min_length=1, examples=["SUP001"])
    safety_stock: int | None = Field(default=None, ge=0, examples=[10])
    lead_time_days: int | None = Field(default=None, ge=0, examples=[8])

    @field_validator("supplier_code")
    @classmethod
    def supplier_code_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("supplier_code 不能为空")
        return value

    @model_validator(mode="after")
    def at_least_one_field_is_provided(self) -> "PurchaseParameterValues":
        if self.supplier_code is None and self.safety_stock is None and self.lead_time_days is None:
            raise ValueError("至少补充一项采购参数")
        return self


class PurchaseParameterResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updates: dict[str, PurchaseParameterValues] = Field(min_length=1)

    @field_validator("updates")
    @classmethod
    def material_codes_must_not_be_blank(
        cls,
        value: dict[str, PurchaseParameterValues],
    ) -> dict[str, PurchaseParameterValues]:
        if any(not material.strip() for material in value):
            raise ValueError("updates 中的物料编码不能为空")
        return value


class BlockingMaterialResponse(BaseModel):
    material: str
    required: int
    available: int | None
    shortage: int
    shortage_ratio: float
    purchase_status: str
    lead_time_days: int | None
    risk_level: str | None
    priority: int
    missing_fields: list[str] = Field(default_factory=list)


class MissingPurchaseParameterResponse(BaseModel):
    material: str
    shortage: int
    purchase_status: str
    missing_fields: list[str]


class AgentExecutionStepResponse(BaseModel):
    """面向展示的执行轨迹，不包含提示词、推理过程或内部 State。"""

    step: str
    tool: str
    status: Literal["completed", "waiting", "error"]
    summary: str


class PmcWorkflowResponse(BaseModel):
    """API 对外的工作流快照；不暴露内部 LangGraph State。"""

    workflow_id: str
    workflow_status: str
    decision_status: Literal["ready", "blocked", "procurement_pending", "data_incomplete"] | None = None
    can_start_production: bool | None = None
    blocking_materials: list[BlockingMaterialResponse] = Field(default_factory=list)
    missing_purchase_parameters: list[MissingPurchaseParameterResponse] = Field(default_factory=list)
    execution_steps: list[AgentExecutionStepResponse] = Field(default_factory=list)
    report: dict[str, Any] = Field(default_factory=dict)
    public_error: PublicErrorResponse | None = None


class HealthCheckResponse(BaseModel):
    status: Literal["ok", "warning", "error"]
    message: str
    mode: str | None = None
    provider: str | None = None
    fallback_available: bool | None = None
    directory_exists: bool | None = None
    allowed_origin_count: int | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    checks: dict[str, HealthCheckResponse] | None = None
