"""统一 Agent 入口的稳定公开契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ScenarioId = Literal["demo_scenario_50_vs_100", "review_real_bom_simulation", "kq_z112_review_slim", "kq_z112_review_slim_synthetic"]


class UnifiedAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, examples=["查询 MAT-001 库存"])
    workflow_id: str | None = Field(default=None, min_length=1)
    fact_session_id: str | None = Field(default=None, min_length=1, max_length=64)
    active_context: Literal["pmc_analysis", "fact_query"] | None = None
    session_id: str | None = Field(default=None, min_length=1, max_length=64)
    persist_session: bool = False
    scenario_id: ScenarioId | None = None
    # Order of the catalog actually displayed in this conversation, never a permission grant.
    catalog_order: list[str] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def isolate_published_session(self):
        if (self.session_id or self.persist_session or self.scenario_id) and (self.workflow_id or self.fact_session_id or self.active_context):
            raise ValueError("持久企业会话不接受旧Demo/内存会话上下文")
        return self


class UnifiedAgentResponse(BaseModel):
    intent: Literal["pmc_analysis", "fact_query", "production_capacity", "clarification",
                    "inventory", "inventory_batches", "bom", "inbound", "purchase_planning"]
    status: str
    answer: str
    structured_data: dict[str, Any] | None
    provenance: dict[str, Any] | list[dict[str, Any]] | None
    warnings: list[str] = Field(default_factory=list)
    session_id: str | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    scenario_id: ScenarioId | None = None


class AgentSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: ScenarioId | None = None
    product_code: str | None = Field(default=None, min_length=1, max_length=128)


class AgentSessionResponse(BaseModel):
    session_id: str
    conversation_id: str
    created_at: str
    updated_at: str
    context_version: int
    context: dict[str, Any]
    scenario_id: ScenarioId | None = None
    product_context: dict[str, Any] | None = None


class AgentFactReference(BaseModel):
    tool_name: str
    query: dict[str, Any]
    result: dict[str, Any]
    provenance: dict[str, Any] | list[dict[str, Any]] | None


class AgentTurn(BaseModel):
    turn_id: str
    sequence_no: int
    user_query: str
    response: UnifiedAgentResponse
    created_at: str
    fact_references: list[AgentFactReference]


class AgentSessionHistory(AgentSessionResponse):
    turns: list[AgentTurn]


__all__ = ["UnifiedAgentRequest", "UnifiedAgentResponse", "AgentSessionResponse",
           "AgentFactReference", "AgentTurn", "AgentSessionHistory", "AgentSessionCreate", "ScenarioId"]
