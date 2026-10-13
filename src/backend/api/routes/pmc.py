"""PMC 分析、状态查询和采购参数恢复路由。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from api.schemas.pmc import (
    AnalyzeRequest,
    HealthResponse,
    PmcWorkflowResponse,
    PurchaseParameterResumeRequest,
)
from api.schemas.errors import PublicErrorResponse
from workflows.pmc_graph import (
    get_pmc_workflow_state,
    resume_pmc_workflow,
    run_pmc_workflow,
)
from services.startup_check_service import build_runtime_checks, overall_health_status


router = APIRouter(prefix="/api/v1/pmc", tags=["PMC"])


_EXECUTION_STEP_CATALOG: dict[str, tuple[str, str, str]] = {
    "validate_input": ("任务校验", "输入校验", "已校验产品、版本和生产数量。"),
    "bom_analysis": ("BOM 分析", "real_bom_check", "已调用 BOM 工具展开物料需求。"),
    "mrp_analysis": ("MRP 齐套分析", "real_mrp_check", "已调用 MRP 工具分析库存齐套情况。"),
    "purchase_analysis": ("采购分析", "real_purchase_analysis_check", "已调用采购工具检查参数与采购建议。"),
    "production_decision": ("生产决策", "production_decision_check", "已调用生产决策工具生成确定性结论。"),
    "generate_report": ("报告生成", "real_pmc_report_check", "已调用报告工具汇总结构化 PMC 报告。"),
    "wait_for_purchase_parameters": ("等待业务补充", "purchase_parameter_interaction", "需要业务人员补充采购参数后才能继续。"),
}


def _workflow_state_or_404(workflow_id: str) -> dict[str, object]:
    workflow_state = get_pmc_workflow_state(workflow_id)
    if not workflow_state:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow 不存在或已失效")
    return workflow_state


def _public_execution_steps(workflow_state: Mapping[str, object]) -> list[dict[str, str]]:
    """将已有节点完成记录转换为展示轨迹，不暴露内部推理或完整 State。"""
    completed = workflow_state.get("completed_nodes", [])
    completed_nodes = completed if isinstance(completed, list) else []
    steps: list[dict[str, str]] = []
    for node in completed_nodes:
        if not isinstance(node, str) or node not in _EXECUTION_STEP_CATALOG:
            continue
        step, tool, summary = _EXECUTION_STEP_CATALOG[node]
        steps.append({"step": step, "tool": tool, "status": "completed", "summary": summary})

    waiting_for_parameters = workflow_state.get("workflow_status") == "waiting_for_purchase_parameters"
    if waiting_for_parameters and not any(item["tool"] == "purchase_parameter_interaction" for item in steps):
        step, tool, summary = _EXECUTION_STEP_CATALOG["wait_for_purchase_parameters"]
        steps.append({"step": step, "tool": tool, "status": "waiting", "summary": summary})
    if workflow_state.get("errors"):
        steps.append({"step": "工作流异常", "tool": "error_handler", "status": "error", "summary": "工作流已返回结构化错误信息。"})
    return steps


def _workflow_response(workflow_id: str, workflow_state: Mapping[str, object]) -> PmcWorkflowResponse:
    """从内部工作流 State 构建受控的公开响应。"""
    workflow_status = str(workflow_state.get("workflow_status", "unknown"))
    decision = workflow_state.get("production_decision")
    decision_data = decision if isinstance(decision, Mapping) else {}
    blockers = decision_data.get("blocking_materials", [])
    missing = workflow_state.get("missing_purchase_parameters", [])
    report = workflow_state.get("report")
    public_error = (
        PublicErrorResponse(
            code="ANALYSIS_UNAVAILABLE",
            user_message="分析服务暂时异常，请稍后重试。",
        )
        if workflow_status == "error"
        else None
    )
    return PmcWorkflowResponse(
        workflow_id=workflow_id,
        workflow_status=workflow_status,
        decision_status=decision_data.get("decision_status"),
        can_start_production=decision_data.get("can_start_production"),
        blocking_materials=blockers if isinstance(blockers, list) else [],
        missing_purchase_parameters=missing if isinstance(missing, list) else [],
        execution_steps=_public_execution_steps(workflow_state),
        # 错误工作流的报告可能含内部异常原文；只保留公开错误契约。
        report=report if isinstance(report, dict) and public_error is None else {},
        public_error=public_error,
    )


def start_public_pmc_workflow(product: str, bom_version: str, quantity: int) -> PmcWorkflowResponse:
    """启动既有工作流并返回公开响应，供 Agent 交互层复用。"""
    try:
        workflow_id = run_pmc_workflow(product, bom_version, quantity)
        workflow_state = _workflow_state_or_404(workflow_id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="PMC 工作流启动失败") from None
    return _workflow_response(workflow_id, workflow_state)


@router.post("/analyze", response_model=PmcWorkflowResponse)
def analyze_pmc(request: AnalyzeRequest) -> PmcWorkflowResponse:
    """启动一次新的内存 PMC 工作流。"""
    return start_public_pmc_workflow(request.product, request.bom_version, request.quantity)


@router.get("/{workflow_id}", response_model=PmcWorkflowResponse)
def get_pmc_workflow(workflow_id: str) -> PmcWorkflowResponse:
    """读取当前进程 MemorySaver 中的工作流公开快照。"""
    return _workflow_response(workflow_id, _workflow_state_or_404(workflow_id))


@router.post("/{workflow_id}/purchase-parameters", response_model=PmcWorkflowResponse)
def resume_purchase_parameters(
    workflow_id: str,
    request: PurchaseParameterResumeRequest,
) -> PmcWorkflowResponse:
    """从暂停点恢复：只补采购参数，不重跑 BOM 或 MRP。"""
    _workflow_state_or_404(workflow_id)
    updates: dict[str, dict[str, Any]] = {
        material: values.model_dump(exclude_none=True)
        for material, values in request.updates.items()
    }
    try:
        workflow_state = resume_pmc_workflow(workflow_id, updates)
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="PMC 工作流恢复失败") from None
    return _workflow_response(workflow_id, workflow_state)


health_router = APIRouter(tags=["System"])


@health_router.get("/health", response_model=HealthResponse, response_model_exclude_none=True)
def health(details: bool = Query(default=False)) -> HealthResponse:
    """默认返回稳定存活契约；details=true 时返回不含敏感值的就绪检查。"""
    if not details:
        return HealthResponse()
    checks = build_runtime_checks()
    return HealthResponse(status=overall_health_status(checks), checks=checks)
