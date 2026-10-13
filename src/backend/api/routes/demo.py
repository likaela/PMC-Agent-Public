"""小范围 Demo 体验 API：案例、反馈与匿名聚合统计。"""

from __future__ import annotations

from fastapi import APIRouter

from api.routes.pmc import _workflow_state_or_404
from api.schemas.demo import (
    DemoCasesResponse,
    DemoFeedbackRequest,
    DemoFeedbackResponse,
    DemoStatisticsResponse,
)
from services.chat_session_service import get_chat_history
from services.demo_experience_service import add_demo_feedback, get_demo_statistics


router = APIRouter(prefix="/api/v1/demo", tags=["Demo Experience"])

_DEMO_CASES = [
    {"id": "analyze-100", "label": "我要生产100台KQ-Z112", "message": "我要生产100台KQ-Z112，帮我分析", "requires_workflow": False},
    {"id": "why-blocked", "label": "为什么不能生产？", "message": "为什么不能生产？", "requires_workflow": True},
    {"id": "reduce-50", "label": "如果减少到50台呢？", "message": "如果减少到50台呢？", "requires_workflow": True},
    {"id": "production-impact", "label": "哪些物料最影响生产？", "message": "哪些物料最影响生产？", "requires_workflow": True},
]


@router.get("/cases", response_model=DemoCasesResponse)
def get_demo_cases() -> DemoCasesResponse:
    """返回公开演示话术，不携带企业主数据。"""
    return DemoCasesResponse(cases=_DEMO_CASES)


@router.post("/feedback", response_model=DemoFeedbackResponse)
def submit_demo_feedback(request: DemoFeedbackRequest) -> DemoFeedbackResponse:
    """保存当前进程内的体验反馈，不写入磁盘或外部服务。"""
    _workflow_state_or_404(request.workflow_id)
    history = get_chat_history(request.workflow_id)
    first_user_message = next((item["content"] for item in history if item["role"] == "user"), "")
    add_demo_feedback(request.workflow_id, request.feedback, user_input=first_user_message)
    return DemoFeedbackResponse(workflow_id=request.workflow_id, message="感谢反馈，已记录到当前 Demo 进程。")


@router.get("/statistics", response_model=DemoStatisticsResponse)
def get_statistics() -> DemoStatisticsResponse:
    """返回当前进程的匿名体验聚合统计。"""
    return DemoStatisticsResponse(**get_demo_statistics())
