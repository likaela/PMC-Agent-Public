"""脱敏 Demo 采购参数读取；不从供应商主数据推断任何关系。"""

from __future__ import annotations

from repositories.data_source import load_demo_data


def load_demo_purchase_parameters() -> dict[str, dict[str, object]]:
    """读取 JSON 中逐项声明的合成采购参数并执行字段校验。"""
    result: dict[str, dict[str, object]] = {}
    for item in load_demo_data().get("purchase_parameters", []):
        if not isinstance(item, dict):
            raise ValueError("Demo 采购参数必须为对象列表")
        material = item.get("material_code")
        if not isinstance(material, str) or not material.strip():
            raise ValueError("Demo 采购参数缺少 material_code")
        if material in result:
            raise ValueError(f"Demo 采购参数存在重复料号：{material}")
        parameters = {
            "supplier_code": item.get("supplier_code"),
            "safety_stock": item.get("safety_stock"),
            "lead_time_days": item.get("lead_time_days"),
        }
        supplier_code = parameters["supplier_code"]
        safety_stock = parameters["safety_stock"]
        lead_time_days = parameters["lead_time_days"]
        if (
            not isinstance(supplier_code, str)
            or not supplier_code.strip()
            or not isinstance(safety_stock, (int, float))
            or isinstance(safety_stock, bool)
            or safety_stock < 0
            or not isinstance(lead_time_days, int)
            or isinstance(lead_time_days, bool)
            or lead_time_days < 0
        ):
            raise ValueError(f"Demo 采购参数不完整：{material}")
        result[material] = parameters
    return result


__all__ = ["load_demo_purchase_parameters"]
