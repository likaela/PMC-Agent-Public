"""供 Agent 调用的采购工具适配层。"""

from collections.abc import Mapping

from repositories.data_source import resolve_data_source_mode
from repositories.demo_purchase_parameter_repository import load_demo_purchase_parameters
from repositories.purchase_repository import load_purchase_data
from services.purchase_service import (
    analyze_real_purchase,
    analyze_interactive_purchase,
    purchase_recommendation as calculate_purchase,
    validate_purchase_parameter_update,
)


# 仅保存当前 Python 进程中由业务人员明确补充的参数；不落盘、不读取供应商主数据。
_session_purchase_parameters: dict[str, dict[str, object]] = {}
_session_shortages: dict[str, int] = {}


def _effective_purchase_parameters() -> dict[str, dict[str, object]]:
    """Demo 使用显式合成参数；raw 只使用业务人员当前会话补充值。"""
    parameters = load_demo_purchase_parameters() if resolve_data_source_mode() == "demo" else {}
    for material, values in _session_purchase_parameters.items():
        parameters[material] = {**parameters.get(material, {}), **values}
    return parameters


def purchase_recommendation(shortages: Mapping[str, int]) -> dict[str, dict[str, int | str]]:
    """读取采购参数并生成采购建议。"""
    return calculate_purchase(shortages, load_purchase_data())


def check_real_purchase_parameters(shortages: Mapping[str, int]) -> list[dict[str, object]]:
    """登记本次真实 MRP 缺料，并逐项返回采购参数待补充状态。"""
    _session_shortages.clear()
    _session_shortages.update(shortages)
    return analyze_interactive_purchase(shortages, _effective_purchase_parameters())


def analyze_real_purchase_for_mrp(mrp_result: Mapping[str, object]) -> dict[str, list[dict[str, object]]]:
    """将 MRP 的结构化物料结果交给采购服务，不解析文字或猜测参数。"""
    materials = mrp_result.get("materials")
    if not isinstance(materials, list):
        raise ValueError("MRP 结果缺少 materials 列表")
    shortages: dict[str, int] = {}
    for item in materials:
        if not isinstance(item, Mapping):
            raise ValueError("MRP 物料格式无效")
        material = item.get("material")
        shortage = item.get("shortage")
        if (
            item.get("inventory_status") == "known"
            and isinstance(material, str)
            and isinstance(shortage, int)
            and not isinstance(shortage, bool)
            and shortage > 0
        ):
            shortages[material] = shortage
    _session_shortages.clear()
    _session_shortages.update(shortages)
    return analyze_real_purchase(materials, _effective_purchase_parameters())


def supplement_real_purchase_parameter(
    material: str,
    *,
    supplier_code: object | None = None,
    safety_stock: object | None = None,
    lead_time_days: object | None = None,
) -> dict[str, object]:
    """补充一个物料的部分或全部参数，并立即按当前会话缺料重新计算。"""
    if material not in _session_shortages:
        raise ValueError("请先使用当前 MRP 缺料清单检查采购参数")

    parameters = dict(_effective_purchase_parameters().get(material, {}))
    for field, value in {
        "supplier_code": supplier_code,
        "safety_stock": safety_stock,
        "lead_time_days": lead_time_days,
    }.items():
        if value is not None:
            parameters[field] = value

    validate_purchase_parameter_update(parameters)
    _session_purchase_parameters[material] = parameters

    return analyze_interactive_purchase(
        {material: _session_shortages[material]},
        {material: parameters},
    )[0]


def clear_real_purchase_parameter_session() -> None:
    """清空当前进程会话参数，供新的独立分析或测试使用。"""
    _session_purchase_parameters.clear()
    _session_shortages.clear()


__all__ = [
    "purchase_recommendation",
    "check_real_purchase_parameters",
    "analyze_real_purchase_for_mrp",
    "supplement_real_purchase_parameter",
    "clear_real_purchase_parameter_session",
]
