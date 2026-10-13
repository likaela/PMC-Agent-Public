"""PMC LangGraph 工作流：只编排既有 Tool，不实现业务计算。"""

from __future__ import annotations

from collections.abc import Mapping
from operator import add
from typing import Annotated, Literal, NotRequired, TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from tools.agent_tools import (
    production_decision_check,
    real_bom_check,
    real_mrp_check,
    real_pmc_report_check,
    real_purchase_analysis_check,
)
from tools.purchase_tool import (
    clear_real_purchase_parameter_session,
    supplement_real_purchase_parameter,
)


class PmcWorkflowState(TypedDict, total=False):
    """单次 PMC 编排运行所需的可检查状态。"""

    product: str
    bom_version: str
    quantity: int
    bom_result: dict[str, object] | None
    mrp_result: dict[str, object] | None
    data_quality_status: dict[str, object] | None
    purchase_analysis: dict[str, object] | None
    production_decision: dict[str, object] | None
    report: dict[str, object] | None
    missing_purchase_parameters: list[dict[str, object]]
    purchase_parameter_updates: dict[str, dict[str, object]]
    workflow_status: str
    errors: list[str]
    completed_nodes: Annotated[list[str], add]
    error_type: NotRequired[str]


def _success(node_name: str, **updates: object) -> dict[str, object]:
    return {"completed_nodes": [node_name], **updates}


def _failure(node_name: str, error: Exception) -> dict[str, object]:
    return {
        "completed_nodes": [node_name],
        "workflow_status": "error",
        "errors": [str(error)],
        "error_type": type(error).__name__,
    }


def validate_input(state: PmcWorkflowState) -> dict[str, object]:
    """校验输入格式；产品和版本的存在性由 BOM Tool 校验。"""
    product = state.get("product")
    version = state.get("bom_version")
    quantity = state.get("quantity")
    if not isinstance(product, str) or not product.strip():
        return _failure("validate_input", ValueError("产品必须为非空字符串"))
    if not isinstance(version, str) or not version.strip():
        return _failure("validate_input", ValueError("BOM版本必须为非空字符串"))
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        return _failure("validate_input", ValueError("生产数量必须为正整数"))
    return _success("validate_input", workflow_status="running", errors=[])


def bom_analysis(state: PmcWorkflowState) -> dict[str, object]:
    try:
        result = real_bom_check.invoke(
            {
                "product": state["product"],
                "bom_version": state["bom_version"],
                "quantity": state["quantity"],
            }
        )
        return _success("bom_analysis", bom_result=result)
    except Exception as error:
        return _failure("bom_analysis", error)


def mrp_analysis(state: PmcWorkflowState) -> dict[str, object]:
    try:
        bom_result = state["bom_result"]
        if not isinstance(bom_result, Mapping):
            raise ValueError("BOM 工具未返回结构化结果")
        result = real_mrp_check.invoke({"bom_requirements": bom_result["bom_requirements"]})
        return _success(
            "mrp_analysis",
            mrp_result=result["mrp_result"],
            data_quality_status=result["data_quality_status"],
        )
    except Exception as error:
        return _failure("mrp_analysis", error)


def purchase_analysis(state: PmcWorkflowState) -> dict[str, object]:
    try:
        mrp_result = state["mrp_result"]
        if not isinstance(mrp_result, Mapping):
            raise ValueError("MRP 工具未返回结构化结果")
        result = real_purchase_analysis_check.invoke({"mrp_result": mrp_result})
        return _success(
            "purchase_analysis",
            purchase_analysis=result,
            missing_purchase_parameters=result["parameter_pending"],
        )
    except Exception as error:
        return _failure("purchase_analysis", error)


def production_decision(state: PmcWorkflowState) -> dict[str, object]:
    try:
        result = production_decision_check.invoke(
            {
                "product": state["product"],
                "quantity": state["quantity"],
                "mrp_result": state["mrp_result"],
                "purchase_analysis": state["purchase_analysis"],
            }
        )
        waiting = bool(result["missing_purchase_parameters"])
        return _success(
            "production_decision",
            production_decision=result,
            workflow_status="waiting_for_purchase_parameters" if waiting else "running",
        )
    except Exception as error:
        return _failure("production_decision", error)


def generate_report(state: PmcWorkflowState) -> dict[str, object]:
    try:
        bom_result = state["bom_result"]
        if not isinstance(bom_result, Mapping):
            raise ValueError("BOM 工具未返回结构化结果")
        result = real_pmc_report_check.invoke(
            {
                "product": state["product"],
                "bom_version": state["bom_version"],
                "quantity": state["quantity"],
                "bom_requirements": bom_result["bom_requirements"],
                "mrp_result": state["mrp_result"],
                "purchase_analysis": state["purchase_analysis"],
                "data_quality_status": state["data_quality_status"],
                "production_decision": state["production_decision"],
            }
        )
        return _success(
            "generate_report",
            report=result,
            workflow_status=(
                "waiting_for_purchase_parameters"
                if state.get("missing_purchase_parameters")
                else "completed"
            ),
        )
    except Exception as error:
        return _failure("generate_report", error)


def wait_for_purchase_parameters(state: PmcWorkflowState) -> dict[str, object]:
    """暂停并要求业务人员提供参数；MemorySaver 保留本进程状态。"""
    updates = interrupt(
        {
            "workflow_status": "waiting_for_purchase_parameters",
            "missing_purchase_parameters": state.get("missing_purchase_parameters", []),
            "required_fields": ["supplier_code", "safety_stock", "lead_time_days"],
        }
    )
    if not isinstance(updates, Mapping):
        raise ValueError("采购参数补充必须为 {material: {field: value}} 结构")
    return _success(
        "wait_for_purchase_parameters",
        purchase_parameter_updates=dict(updates),
        workflow_status="resuming_purchase_parameters",
    )


def apply_purchase_parameter_updates(state: PmcWorkflowState) -> dict[str, object]:
    """经现有采购 Tool 写入当前会话参数；随后只重跑采购及后续节点。"""
    try:
        updates = state.get("purchase_parameter_updates", {})
        if not isinstance(updates, Mapping) or not updates:
            raise ValueError("未提供采购参数补充内容")
        for material, parameters in updates.items():
            if not isinstance(material, str) or not isinstance(parameters, Mapping):
                raise ValueError("采购参数补充必须为 {material: {field: value}} 结构")
            supplement_real_purchase_parameter(
                material,
                supplier_code=parameters.get("supplier_code"),
                safety_stock=parameters.get("safety_stock"),
                lead_time_days=parameters.get("lead_time_days"),
            )
        return _success("apply_purchase_parameter_updates")
    except Exception as error:
        return _failure("apply_purchase_parameter_updates", error)


def finalize_error(state: PmcWorkflowState) -> dict[str, object]:
    """将已捕获错误结构化返回，避免工作流以不可解释异常结束。"""
    return _success(
        "finalize_error",
        workflow_status="error",
        report={"status": "error", "errors": state.get("errors", [])},
    )


def route_after_step(state: PmcWorkflowState) -> Literal["next", "error"]:
    return "error" if state.get("errors") else "next"


def route_after_report(state: PmcWorkflowState) -> Literal["wait", "complete", "error"]:
    if state.get("errors"):
        return "error"
    if state.get("missing_purchase_parameters"):
        return "wait"
    return "complete"


def route_after_production_status(state: PmcWorkflowState) -> str:
    """仅路由 production_service 已给出的状态，不重判生产可行性。"""
    decision = state.get("production_decision")
    if not isinstance(decision, Mapping):
        return "error"
    status = decision.get("decision_status")
    return status if status in {"ready", "blocked", "procurement_pending", "data_incomplete"} else "error"


def build_pmc_graph():
    """构建带内存 checkpoint 的 PMC 工作流图。"""
    graph = StateGraph(PmcWorkflowState)
    graph.add_node("validate_input", validate_input)
    graph.add_node("bom_analysis", bom_analysis)
    graph.add_node("mrp_analysis", mrp_analysis)
    graph.add_node("purchase_analysis", purchase_analysis)
    graph.add_node("production_decision", production_decision)
    graph.add_node("generate_report", generate_report)
    graph.add_node("wait_for_purchase_parameters", wait_for_purchase_parameters)
    graph.add_node("apply_purchase_parameter_updates", apply_purchase_parameter_updates)
    graph.add_node("finalize_error", finalize_error)

    graph.add_edge(START, "validate_input")
    graph.add_conditional_edges("validate_input", route_after_step, {"next": "bom_analysis", "error": "finalize_error"})
    graph.add_conditional_edges("bom_analysis", route_after_step, {"next": "mrp_analysis", "error": "finalize_error"})
    graph.add_conditional_edges("mrp_analysis", route_after_step, {"next": "purchase_analysis", "error": "finalize_error"})
    graph.add_conditional_edges("purchase_analysis", route_after_step, {"next": "production_decision", "error": "finalize_error"})
    graph.add_conditional_edges(
        "production_decision",
        route_after_production_status,
        {
            "ready": "generate_report",
            "blocked": "generate_report",
            "procurement_pending": "generate_report",
            "data_incomplete": "generate_report",
            "error": "finalize_error",
        },
    )
    graph.add_conditional_edges(
        "generate_report",
        route_after_report,
        {"wait": "wait_for_purchase_parameters", "complete": END, "error": "finalize_error"},
    )
    graph.add_edge("wait_for_purchase_parameters", "apply_purchase_parameter_updates")
    graph.add_conditional_edges(
        "apply_purchase_parameter_updates",
        route_after_step,
        {"next": "purchase_analysis", "error": "finalize_error"},
    )
    graph.add_edge("finalize_error", END)
    return graph.compile(checkpointer=MemorySaver())


PMC_GRAPH = build_pmc_graph()


def run_pmc_workflow(product: str, bom_version: str, quantity: int, workflow_id: str | None = None) -> str:
    """启动新工作流并返回可用于获取状态或恢复的内存 thread_id。"""
    clear_real_purchase_parameter_session()
    thread_id = workflow_id or str(uuid4())
    PMC_GRAPH.invoke(
        {
            "product": product,
            "bom_version": bom_version,
            "quantity": quantity,
            "errors": [],
            "completed_nodes": [],
        },
        {"configurable": {"thread_id": thread_id}},
    )
    return thread_id


def get_pmc_workflow_state(workflow_id: str) -> dict[str, object]:
    """读取当前进程 MemorySaver 中的工作流状态。"""
    snapshot = PMC_GRAPH.get_state({"configurable": {"thread_id": workflow_id}})
    return dict(snapshot.values)


def resume_pmc_workflow(
    workflow_id: str,
    purchase_parameter_updates: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """恢复暂停的工作流；仅从采购参数更新后的节点继续。"""
    PMC_GRAPH.invoke(
        Command(resume=dict(purchase_parameter_updates)),
        {"configurable": {"thread_id": workflow_id}},
    )
    return get_pmc_workflow_state(workflow_id)


__all__ = [
    "PmcWorkflowState",
    "PMC_GRAPH",
    "build_pmc_graph",
    "get_pmc_workflow_state",
    "resume_pmc_workflow",
    "route_after_production_status",
    "run_pmc_workflow",
]
