"""LangChain Tool 定义：仅负责 Agent 与业务服务之间的适配。"""

from langchain.tools import tool

from .bom_tool import explode_bom
from .inventory_tool import check_inventory
from .mrp_tool import analyze_real_mrp
from .purchase_tool import (
    analyze_real_purchase_for_mrp,
    check_real_purchase_parameters,
    purchase_recommendation,
    supplement_real_purchase_parameter,
)
from .production_tool import build_real_pmc_report, make_production_decision
from .real_bom_tool import explode_real_bom


@tool
def bom_check(product: str, quantity: int) -> dict[str, int]:
    """根据产品型号和生产数量展开 BOM，返回 {物料: 需求数量}。"""
    return explode_bom(product, quantity)


@tool
def inventory_check(materials: dict[str, int]) -> dict[str, dict[str, int]]:
    """查询 BOM 需求的库存，返回每种物料的 stock、available、shortage。"""
    return check_inventory(materials)


@tool
def purchase_check(shortages: dict[str, int]) -> dict[str, dict[str, int | str]]:
    """根据 {物料: 缺口} 生成采购数量、安全库存和采购周期建议。"""
    return purchase_recommendation(shortages)


@tool
def real_purchase_parameter_check(shortages: dict[str, int]) -> list[dict[str, object]]:
    """针对真实 MRP 的缺料逐项检查采购参数，返回待补充字段或确定性采购建议。"""
    return check_real_purchase_parameters(shortages)


@tool
def supplement_real_purchase_parameter_check(
    material: str,
    supplier_code: str | None = None,
    safety_stock: int | None = None,
    lead_time_days: int | None = None,
) -> dict[str, object]:
    """补充一个缺料物料的采购参数；参数完整后立即重新计算采购数量和风险。"""
    return supplement_real_purchase_parameter(
        material,
        supplier_code=supplier_code,
        safety_stock=safety_stock,
        lead_time_days=lead_time_days,
    )


@tool
def real_bom_check(product: str, bom_version: str, quantity: int) -> dict[str, object]:
    """读取真实 BOM 并展开需求。必须使用返回的 bom_requirements 继续 MRP。"""
    return explode_real_bom(product, bom_version, quantity)


@tool
def real_mrp_check(bom_requirements: dict[str, int]) -> dict[str, object]:
    """用一期真实库存执行 MRP，返回 mrp_result 与数据口径状态。"""
    return analyze_real_mrp(bom_requirements)


@tool
def real_purchase_analysis_check(mrp_result: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    """分析 MRP 已知缺料的采购参数完整性；不会猜测采购参数。"""
    return analyze_real_purchase_for_mrp(mrp_result)


@tool
def production_decision_check(
    product: str,
    quantity: int,
    mrp_result: dict[str, object],
    purchase_analysis: dict[str, object],
) -> dict[str, object]:
    """根据 MRP 和采购分析生成确定性生产决策，不能由 Agent 自行判断。"""
    return make_production_decision(product, quantity, mrp_result, purchase_analysis)


@tool
def real_pmc_report_check(
    product: str,
    bom_version: str,
    quantity: int,
    bom_requirements: dict[str, int],
    mrp_result: dict[str, object],
    purchase_analysis: dict[str, list[dict[str, object]]],
    data_quality_status: dict[str, object],
    production_decision: dict[str, object],
) -> dict[str, object]:
    """基于前序工具返回构建结构化 PMC 报告，不重新计算任何业务数值。"""
    return build_real_pmc_report(
        product,
        bom_version,
        quantity,
        bom_requirements,
        mrp_result,
        purchase_analysis,
        data_quality_status,
        production_decision,
    )


STANDARD_AGENT_TOOLS = [
    real_bom_check,
    real_mrp_check,
    real_purchase_analysis_check,
    production_decision_check,
    real_pmc_report_check,
]


ALL_AGENT_TOOLS = [
    bom_check,
    inventory_check,
    purchase_check,
    real_purchase_parameter_check,
    supplement_real_purchase_parameter_check,
    *STANDARD_AGENT_TOOLS,
]
