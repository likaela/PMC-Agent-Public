"""自然语言 Chat API 的公开契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.explanation import ExplanationResponse
from api.schemas.llm_task import ProductionTaskIntent
from api.schemas.pmc import PmcWorkflowResponse


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, examples=["我要生产100台KQ-Z112，帮我看看能不能开工"])
    workflow_id: str | None = Field(default=None, min_length=1)


class ParsedTaskResponse(BaseModel):
    product: str
    bom_version: str
    quantity: int


class ChatTraceStep(BaseModel):
    step: str
    status: Literal["completed", "running", "waiting", "error"]
    summary: str


class ChatScenarioResponse(BaseModel):
    name: str
    quantity: int
    original_result: PmcWorkflowResponse
    workflow_result: PmcWorkflowResponse
    improvement: str


class ChatHistoryItemResponse(BaseModel):
    role: Literal["user", "agent"]
    content: str
    turn_type: Literal["analysis", "explanation", "scenario", "impact"]
    workflow_id: str


class ChatHistoryResponse(BaseModel):
    workflow_id: str
    history: list[ChatHistoryItemResponse] = Field(default_factory=list)


class ChatClarificationResponse(BaseModel):
    """任务不完整时的安全澄清响应；不会启动或泄露内部 Workflow。"""

    response_type: Literal["clarification"] = "clarification"
    agent_message: str
    task_intent: ProductionTaskIntent
    missing_fields: list[str] = Field(default_factory=list)


class ChatResponse(PmcWorkflowResponse):
    """解析结果加上既有 PMC 工作流的受控公开响应。"""

    response_type: Literal["analysis"] = "analysis"
    parsed_task: ParsedTaskResponse
    agent_message: str
    follow_up_questions: list[str] = Field(default_factory=list)
    conversation_workflow_id: str
    turn_type: Literal["analysis", "explanation", "scenario", "impact"] = "analysis"
    agent_trace: list[ChatTraceStep] = Field(default_factory=list)
    scenario: ChatScenarioResponse | None = None
    task_intent: ProductionTaskIntent | None = None
    explanation: ExplanationResponse | None = None
