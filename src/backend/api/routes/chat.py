"""自然语言生产任务入口：解析参数后复用既有 PMC 工作流。"""

from __future__ import annotations

import re
from collections.abc import Mapping

from fastapi import APIRouter, HTTPException, status

from api.routes.pmc import _workflow_response, _workflow_state_or_404, start_public_pmc_workflow
from api.schemas.chat import (
    ChatClarificationResponse,
    ChatHistoryResponse,
    ChatRequest,
    ChatResponse,
    ChatScenarioResponse,
    ChatTraceStep,
    ParsedTaskResponse,
)
from api.schemas.explanation import ExplanationResponse
from api.schemas.llm_task import ProductionTaskIntent
from api.schemas.pmc import PmcWorkflowResponse
from services.chat_session_service import append_chat_history, get_chat_history
from services.demo_experience_service import record_demo_analysis, record_demo_scenario
from services.explanation_service import ExplanationTopic, explain_pmc_result
from services.llm_task_parser_service import parse_production_task_with_llm
from services.scenario_service import simulate_reduced_quantity


router = APIRouter(prefix="/api/v1", tags=["Chat"])
_WHY_PATTERN = re.compile(r"为什么|为何|原因")
_REDUCE_PATTERN = re.compile(r"(?:减少到|改为|调整为)\s*(\d+)\s*(?:台|套|件|个|只)?")
_IMPACT_PATTERN = re.compile(r"哪些物料最影响生产|哪些物料影响最大|哪些物料影响生产|哪些物料最关键|哪些材料影响最大|最影响生产")


def _follow_up_questions(missing_items: list[object]) -> list[str]:
    """把既有采购缺失字段转为用户可执行的问题，不推测业务参数。"""
    questions: list[str] = []
    for item in missing_items:
        item_data = item.model_dump() if hasattr(item, "model_dump") else item
        if not isinstance(item_data, dict):
            continue
        material = item_data.get("material")
        fields = item_data.get("missing_fields")
        if isinstance(material, str) and isinstance(fields, list):
            questions.append(f"请为物料 {material} 补充：{'、'.join(str(field) for field in fields)}。")
    return questions


def _task_from_workflow_context(workflow_id: str) -> tuple[dict[str, object], PmcWorkflowResponse]:
    """读取现有工作流上下文，不重新执行 BOM/MRP。"""
    workflow_state = _workflow_state_or_404(workflow_id)
    product = workflow_state.get("product")
    bom_version = workflow_state.get("bom_version")
    quantity = workflow_state.get("quantity")
    if not isinstance(product, str) or not isinstance(bom_version, str) or not isinstance(quantity, int):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="当前工作流缺少可复用的生产任务上下文")
    return (
        {"product": product, "bom_version": bom_version, "quantity": quantity},
        _workflow_response(workflow_id, workflow_state),
    )


def _trace_from_workflow(
    response: PmcWorkflowResponse,
    *,
    include_parse: bool,
    parser_source: str | None = None,
) -> list[ChatTraceStep]:
    trace: list[ChatTraceStep] = []
    if include_parse:
        trace.extend(
            [
                ChatTraceStep(step="用户请求", status="completed", summary="已接收自然语言生产任务。"),
                ChatTraceStep(
                    step="任务解析",
                    status="completed",
                    summary=(
                        "已由 AI 提取产品、BOM 版本和生产数量，并完成本地字段校验。"
                        if parser_source == "llm"
                        else "已由本地规则提取产品、BOM 版本和生产数量。"
                    ),
                ),
            ]
        )
    trace.extend(
        ChatTraceStep(step=item.step, status=item.status, summary=item.summary)
        for item in response.execution_steps
    )
    return trace


def _explain_from_workflow_response(
    response: PmcWorkflowResponse,
    *,
    question: str,
    topic: ExplanationTopic,
) -> ExplanationResponse:
    """只将既有公开报告与阻塞物料交给 Explanation Service。"""
    report = response.report
    report_decision = report.get("production_decision", {}) if isinstance(report, Mapping) else {}
    decision = report_decision if isinstance(report_decision, Mapping) else {}
    return explain_pmc_result(
        question=question,
        topic=topic,
        production_report=report,
        production_decision=decision,
        missing_materials=[item.model_dump() for item in response.blocking_materials],
    )


def _save_turn(
    conversation_workflow_id: str,
    *,
    user_message: str,
    agent_message: str,
    turn_type: str,
    active_workflow_id: str,
) -> None:
    append_chat_history(
        conversation_workflow_id,
        role="user",
        content=user_message,
        turn_type=turn_type,
        workflow_id=active_workflow_id,
    )
    append_chat_history(
        conversation_workflow_id,
        role="agent",
        content=agent_message,
        turn_type=turn_type,
        workflow_id=active_workflow_id,
    )


def _chat_response(
    *,
    parsed_task: Mapping[str, object],
    workflow_response: PmcWorkflowResponse,
    conversation_workflow_id: str,
    turn_type: str,
    agent_message: str,
    agent_trace: list[ChatTraceStep],
    scenario: ChatScenarioResponse | None = None,
    task_intent: ProductionTaskIntent | None = None,
    explanation: ExplanationResponse | None = None,
) -> ChatResponse:
    follow_up_questions = _follow_up_questions(workflow_response.missing_purchase_parameters)
    return ChatResponse(
        parsed_task=ParsedTaskResponse(**parsed_task),
        agent_message=agent_message,
        follow_up_questions=follow_up_questions,
        conversation_workflow_id=conversation_workflow_id,
        turn_type=turn_type,
        agent_trace=agent_trace,
        scenario=scenario,
        task_intent=task_intent,
        explanation=explanation,
        **workflow_response.model_dump(),
    )


def _clarification_message(task_intent: ProductionTaskIntent) -> str:
    """根据已校验的部分字段生成用户可执行的补充提示。"""
    recognized: list[str] = []
    if task_intent.product:
        recognized.append(f"已识别产品 {task_intent.product}")
    if task_intent.quantity is not None:
        recognized.append(f"已识别生产数量 {task_intent.quantity} 台")
    if recognized:
        return f"{'。'.join(recognized)}。请补充{'、'.join(task_intent.missing_fields)}。"
    return "暂时无法识别生产任务，请补充产品型号和数量。"


@router.post("/chat", response_model=ChatResponse | ChatClarificationResponse)
def chat(request: ChatRequest) -> ChatResponse | ChatClarificationResponse:
    """支持首轮分析、原因解释和减量方案的当前进程内多轮会话。"""

    if request.workflow_id:
        parsed_task, original_response = _task_from_workflow_context(request.workflow_id)
        if _WHY_PATTERN.search(request.message):
            topic = "ready" if original_response.decision_status == "ready" else "blocked"
            explanation = _explain_from_workflow_response(
                original_response,
                question=request.message,
                topic=topic,
            )
            agent_message = explanation.answer
            response = _chat_response(
                parsed_task=parsed_task,
                workflow_response=original_response,
                conversation_workflow_id=request.workflow_id,
                turn_type="explanation",
                agent_message=agent_message,
                agent_trace=[
                    ChatTraceStep(step="会话上下文复用", status="completed", summary="已读取当前 workflow 的结构化报告。"),
                    ChatTraceStep(step="结果解释", status="completed", summary="已基于确定性报告生成自然语言解释。"),
                ],
                explanation=explanation,
            )
            _save_turn(
                request.workflow_id,
                user_message=request.message,
                agent_message=agent_message,
                turn_type="explanation",
                active_workflow_id=original_response.workflow_id,
            )
            return response

        if _IMPACT_PATTERN.search(request.message):
            explanation = _explain_from_workflow_response(
                original_response,
                question=request.message,
                topic="impact",
            )
            agent_message = explanation.answer
            response = _chat_response(
                parsed_task=parsed_task,
                workflow_response=original_response,
                conversation_workflow_id=request.workflow_id,
                turn_type="impact",
                agent_message=agent_message,
                agent_trace=[
                    ChatTraceStep(step="会话上下文复用", status="completed", summary="已读取当前 workflow 的生产决策。"),
                    ChatTraceStep(step="结果解释", status="completed", summary="已按既有优先级生成缺料解释。"),
                ],
                explanation=explanation,
            )
            _save_turn(
                request.workflow_id,
                user_message=request.message,
                agent_message=agent_message,
                turn_type="impact",
                active_workflow_id=original_response.workflow_id,
            )
            return response

        reduction_match = _REDUCE_PATTERN.search(request.message)
        if reduction_match:
            reduced_quantity = int(reduction_match.group(1))
            try:
                scenario_data = simulate_reduced_quantity(
                    product=str(parsed_task["product"]),
                    bom_version=str(parsed_task["bom_version"]),
                    original_quantity=int(parsed_task["quantity"]),
                    reduced_quantity=reduced_quantity,
                    original_result=original_response.model_dump(),
                    workflow_runner=lambda product, bom_version, quantity: start_public_pmc_workflow(
                        product, bom_version, quantity
                    ).model_dump(),
                )
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from None
            scenario_response = ChatScenarioResponse.model_validate(
                {**scenario_data, "original_result": original_response.model_dump()}
            )
            active_response = scenario_response.workflow_result
            agent_message = f"已按 {reduced_quantity} 台重新调用既有 PMC 工作流。{scenario_response.improvement}"
            response = _chat_response(
                parsed_task={**parsed_task, "quantity": reduced_quantity},
                workflow_response=active_response,
                conversation_workflow_id=request.workflow_id,
                turn_type="scenario",
                agent_message=agent_message,
                agent_trace=[
                    ChatTraceStep(step="会话上下文复用", status="completed", summary="已复用原生产任务与工作流报告。"),
                    ChatTraceStep(step="方案数量解析", status="completed", summary=f"已将备选生产数量设为 {reduced_quantity}。"),
                    *_trace_from_workflow(active_response, include_parse=False),
                    ChatTraceStep(step="方案生成", status="completed", summary="已比较原方案与减量方案。"),
                ],
                scenario=scenario_response,
            )
            _save_turn(
                request.workflow_id,
                user_message=request.message,
                agent_message=agent_message,
                turn_type="scenario",
                active_workflow_id=active_response.workflow_id,
            )
            record_demo_scenario()
            return response

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="当前会话支持“为什么不能生产？”或“减少到50台呢？”；也可以发送新的完整生产任务。",
        )

    task_intent = parse_production_task_with_llm(request.message)
    if not task_intent.is_complete:
        return ChatClarificationResponse(
            agent_message=_clarification_message(task_intent),
            task_intent=task_intent,
            missing_fields=task_intent.missing_fields,
        )

    parsed_task = task_intent.to_workflow_task()
    workflow_response = start_public_pmc_workflow(**parsed_task)
    if workflow_response.public_error:
        agent_message = workflow_response.public_error.user_message
    elif workflow_response.missing_purchase_parameters:
        agent_message = "PMC 分析已完成，但仍需业务人员补充采购参数后才能继续采购分析。"
    else:
        agent_message = "PMC 分析已完成，结果来自确定性业务工作流。"
    response = _chat_response(
        parsed_task=parsed_task,
        workflow_response=workflow_response,
        conversation_workflow_id=workflow_response.workflow_id,
        turn_type="analysis",
        agent_message=agent_message,
        agent_trace=_trace_from_workflow(
            workflow_response,
            include_parse=True,
            parser_source=task_intent.parser_source,
        ),
        task_intent=task_intent,
    )
    _save_turn(
        workflow_response.workflow_id,
        user_message=request.message,
        agent_message=agent_message,
        turn_type="analysis",
        active_workflow_id=workflow_response.workflow_id,
    )
    record_demo_analysis(workflow_response.workflow_id, request.message, analysis_completed=True)
    return response


@router.get("/chat/{workflow_id}/history", response_model=ChatHistoryResponse)
def chat_history(workflow_id: str) -> ChatHistoryResponse:
    """返回当前进程内指定 Chat 会话的历史，不创建或重跑工作流。"""
    _workflow_state_or_404(workflow_id)
    return ChatHistoryResponse(workflow_id=workflow_id, history=get_chat_history(workflow_id))
