"""减量生产方案适配器：只复用既有 PMC 工作流结果。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


WorkflowRunner = Callable[[str, str, int], Mapping[str, Any]]


def simulate_reduced_quantity(
    *,
    product: str,
    bom_version: str,
    original_quantity: int,
    reduced_quantity: int,
    original_result: Mapping[str, Any],
    workflow_runner: WorkflowRunner,
) -> dict[str, Any]:
    """针对新数量重新调用既有 PMC 工作流，不改变其内部规则。"""
    if not isinstance(reduced_quantity, int) or isinstance(reduced_quantity, bool) or reduced_quantity <= 0:
        raise ValueError("方案生产数量必须为正整数")
    if reduced_quantity >= original_quantity:
        raise ValueError("方案生产数量必须低于当前生产数量")

    workflow_result = dict(workflow_runner(product, bom_version, reduced_quantity))
    original_blockers = original_result.get("blocking_materials", [])
    scenario_blockers = workflow_result.get("blocking_materials", [])
    original_count = len(original_blockers) if isinstance(original_blockers, list) else 0
    scenario_count = len(scenario_blockers) if isinstance(scenario_blockers, list) else 0
    if workflow_result.get("decision_status") == "ready":
        improvement = "降低数量后既有 PMC 工作流返回 ready，可作为优先开工候选方案。"
    elif scenario_count < original_count:
        improvement = f"阻塞物料由 {original_count} 项降至 {scenario_count} 项，仍需处理剩余缺料。"
    else:
        improvement = "降低数量后仍未达到可开工状态，需继续依据既有缺料与采购结论处理。"

    return {
        "name": "降低生产数量",
        "quantity": reduced_quantity,
        "workflow_result": workflow_result,
        "improvement": improvement,
    }
