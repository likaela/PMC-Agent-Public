"""采购建议业务计算服务。"""

from collections.abc import Mapping


PURCHASE_PARAMETER_FIELDS = ("supplier_code", "safety_stock", "lead_time_days")


def _is_nonnegative_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def assess_purchase_risks(
    shortages: Mapping[str, int],
    purchase_data: Mapping[str, Mapping[str, int]],
) -> dict[str, dict[str, int | str]]:
    """根据缺口和采购周期确定采购风险等级。"""
    result: dict[str, dict[str, int | str]] = {}

    for material, shortage in shortages.items():
        if (
            not isinstance(material, str)
            or not isinstance(shortage, int)
            or isinstance(shortage, bool)
            or shortage < 0
        ):
            raise ValueError("缺料数据必须为 {物料名称: 非负整数} 格式")
        if material not in purchase_data:
            raise ValueError(f"采购数据中不存在物料：{material}")

        lead_time_days = purchase_data[material].get("lead_time_days")
        if (
            not isinstance(lead_time_days, int)
            or isinstance(lead_time_days, bool)
            or lead_time_days < 0
        ):
            raise ValueError(f"物料 {material} 的采购参数无效")

        if shortage == 0:
            risk_level = "无缺料风险"
        elif lead_time_days <= 7:
            risk_level = "一般风险"
        else:
            risk_level = "高风险"
        result[material] = {
            "shortage": shortage,
            "lead_time_days": lead_time_days,
            "risk_level": risk_level,
        }

    return result


def purchase_recommendation(
    shortages: Mapping[str, int],
    purchase_data: Mapping[str, Mapping[str, int]],
) -> dict[str, dict[str, int | str]]:
    """根据 ``{material: shortage}`` 生成采购建议。"""
    result: dict[str, dict[str, int | str]] = {}
    risks = assess_purchase_risks(shortages, purchase_data)

    for material, shortage in shortages.items():
        if shortage == 0:
            continue

        material_info = purchase_data[material]
        safety_stock = material_info.get("safety_stock")
        lead_time_days = material_info.get("lead_time_days")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (safety_stock, lead_time_days)
        ):
            raise ValueError(f"物料 {material} 的采购参数无效")
        result[material] = {
            "shortage": shortage,
            "safety_stock": safety_stock,
            "recommended_quantity": shortage + safety_stock,
            "lead_time_days": lead_time_days,
            "risk_level": risks[material]["risk_level"],
        }

    return result


def _validate_interactive_shortage(material: object, shortage: object) -> tuple[str, int]:
    """校验交互式采购分析使用的确定性输入。"""
    if not isinstance(material, str) or not material.strip():
        raise ValueError("物料编码必须是非空字符串")
    if not isinstance(shortage, int) or isinstance(shortage, bool) or shortage < 0:
        raise ValueError("缺料数量必须为非负整数")
    return material, shortage


def _missing_purchase_fields(parameters: Mapping[str, object] | None) -> list[str]:
    """按固定顺序列出尚未补充的采购字段。"""
    if parameters is None:
        return list(PURCHASE_PARAMETER_FIELDS)
    return [field for field in PURCHASE_PARAMETER_FIELDS if parameters.get(field) is None]


def _validate_complete_purchase_parameters(
    material: str,
    parameters: Mapping[str, object],
) -> tuple[str, int, int]:
    """校验完整采购参数；安全库存为零属于有效业务值。"""
    supplier_code = parameters.get("supplier_code")
    safety_stock = parameters.get("safety_stock")
    lead_time_days = parameters.get("lead_time_days")

    if not isinstance(supplier_code, str) or not supplier_code.strip():
        raise ValueError(f"物料 {material} 的供应商编码必须为非空字符串")
    if not _is_nonnegative_number(safety_stock):
        raise ValueError(f"物料 {material} 的安全库存必须为非负数")
    if (
        not isinstance(lead_time_days, int)
        or isinstance(lead_time_days, bool)
        or lead_time_days < 0
    ):
        raise ValueError(f"物料 {material} 的采购提前期必须为非负整数")
    return supplier_code.strip(), safety_stock, lead_time_days


def validate_purchase_parameter_update(parameters: Mapping[str, object]) -> None:
    """校验本次已填写的字段，允许其余字段稍后逐项补充。"""
    if "supplier_code" in parameters:
        supplier_code = parameters["supplier_code"]
        if not isinstance(supplier_code, str) or not supplier_code.strip():
            raise ValueError("供应商编码必须为非空字符串")
    if "safety_stock" in parameters and not _is_nonnegative_number(parameters["safety_stock"]):
        raise ValueError("安全库存必须为非负数")
    if "lead_time_days" in parameters:
        lead_time_days = parameters["lead_time_days"]
        if (
            not isinstance(lead_time_days, int)
            or isinstance(lead_time_days, bool)
            or lead_time_days < 0
        ):
            raise ValueError("采购提前期必须为非负整数")


def evaluate_interactive_purchase(
    material: object,
    shortage: object,
    parameters: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """基于业务人员已补充的参数生成单一物料的确定性采购状态。

    参数不完整时只返回待补充字段，绝不猜测供应商、安全库存或采购提前期。
    """
    material_code, valid_shortage = _validate_interactive_shortage(material, shortage)
    if parameters is not None:
        validate_purchase_parameter_update(parameters)
    missing_fields = _missing_purchase_fields(parameters)
    if missing_fields:
        return {
            "material": material_code,
            "shortage": valid_shortage,
            "purchase_status": "需要补充采购参数",
            "missing_fields": missing_fields,
        }

    assert parameters is not None
    supplier_code, safety_stock, lead_time_days = _validate_complete_purchase_parameters(
        material_code,
        parameters,
    )
    risk = assess_purchase_risks(
        {material_code: valid_shortage},
        {material_code: {"lead_time_days": lead_time_days}},
    )[material_code]
    return {
        "material": material_code,
        "shortage": valid_shortage,
        "purchase_status": "采购建议已生成",
        "missing_fields": [],
        "supplier_code": supplier_code,
        "safety_stock": safety_stock,
        "lead_time_days": lead_time_days,
        "suggested_purchase_qty": valid_shortage + safety_stock,
        "risk_level": risk["risk_level"],
    }


def analyze_interactive_purchase(
    shortages: Mapping[str, int],
    session_parameters: Mapping[str, Mapping[str, object]] | None = None,
) -> list[dict[str, object]]:
    """逐项评估缺料采购状态；参数由调用方传入，服务层不保存状态。"""
    parameter_source = session_parameters or {}
    return [
        evaluate_interactive_purchase(material, shortage, parameter_source.get(material))
        for material, shortage in shortages.items()
        if shortage > 0
    ]


def analyze_real_purchase(
    mrp_materials: list[Mapping[str, object]],
    session_parameters: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, list[dict[str, object]]]:
    """分析真实 MRP 中已知缺料物料的采购参数完整性。"""
    recommendations: list[dict[str, object]] = []
    parameter_pending: list[dict[str, object]] = []
    parameter_source = session_parameters or {}

    for item in mrp_materials:
        if item.get("inventory_status") != "known" or not _is_nonnegative_number(item.get("shortage")):
            continue
        shortage = item["shortage"]
        if shortage == 0:
            continue
        material = item.get("material")
        if not isinstance(material, str):
            raise ValueError("MRP 物料格式无效")

        purchase_item = evaluate_interactive_purchase(
            material,
            shortage,
            parameter_source.get(material),
        )
        if purchase_item["purchase_status"] == "需要补充采购参数":
            # 保留第六阶段报告所使用的兼容字段。
            purchase_item["data_status"] = "采购参数待逐项补充"
            parameter_pending.append(purchase_item)
        else:
            recommendations.append(purchase_item)
    return {"recommendations": recommendations, "parameter_pending": parameter_pending}
