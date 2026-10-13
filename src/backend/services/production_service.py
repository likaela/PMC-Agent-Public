"""确定性生产决策服务，不依赖 LLM。"""

from __future__ import annotations

from collections.abc import Mapping


def _as_purchase_index(purchase_analysis: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    """按物料索引采购分析，供 MRP 缺料逐项关联。"""
    index: dict[str, Mapping[str, object]] = {}
    for collection_name in ("recommendations", "parameter_pending"):
        collection = purchase_analysis.get(collection_name, [])
        if not isinstance(collection, list):
            raise ValueError(f"采购分析字段 {collection_name} 必须为列表")
        for item in collection:
            if not isinstance(item, Mapping) or not isinstance(item.get("material"), str):
                raise ValueError("采购分析物料格式无效")
            index[item["material"]] = item
    return index


def _priority_sort_key(item: Mapping[str, object]) -> tuple[int, int, float, int, str]:
    """固定优先级：风险等级、交期、缺料比例、缺料数量、料号。"""
    risk_rank = {"高风险": 2, "一般风险": 1}.get(item.get("risk_level"), 0)
    lead_time = item.get("lead_time_days")
    lead_rank = lead_time if isinstance(lead_time, int) and not isinstance(lead_time, bool) else 0
    shortage_ratio = item["shortage_ratio"]
    shortage = item["shortage"]
    assert isinstance(shortage_ratio, float)
    assert isinstance(shortage, int)
    return (-risk_rank, -lead_rank, -shortage_ratio, -shortage, str(item["material"]))


def _build_blocking_materials(
    materials: list[Mapping[str, object]],
    purchase_index: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    """将确定缺料与采购状态合并，并按固定优先级排序。"""
    blockers: list[dict[str, object]] = []
    for item in materials:
        shortage = item.get("shortage")
        required = item.get("required")
        if (
            item.get("inventory_status") != "known"
            or not isinstance(shortage, int)
            or isinstance(shortage, bool)
            or shortage <= 0
            or not isinstance(required, int)
            or isinstance(required, bool)
            or required <= 0
        ):
            continue

        material = item.get("material")
        if not isinstance(material, str):
            raise ValueError("MRP 物料格式无效")
        purchase_item = purchase_index.get(material, {})
        blockers.append(
            {
                "material": material,
                "required": required,
                "available": item.get("available"),
                "shortage": shortage,
                "shortage_ratio": shortage / required,
                "purchase_status": purchase_item.get("purchase_status", "需要补充采购参数"),
                "lead_time_days": purchase_item.get("lead_time_days"),
                "risk_level": purchase_item.get("risk_level"),
                "missing_fields": list(purchase_item.get("missing_fields", [])),
            }
        )
    blockers.sort(key=_priority_sort_key)
    for position, item in enumerate(blockers, start=1):
        item["priority"] = position
    return blockers


def build_production_decision(
    product: str,
    quantity: int,
    mrp_result: Mapping[str, object],
    purchase_analysis: Mapping[str, object],
) -> dict[str, object]:
    """根据 MRP 与采购状态构建可解释、可复算的生产决策。"""
    if not isinstance(product, str) or not product.strip():
        raise ValueError("产品必须为非空字符串")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise ValueError("生产数量必须为正整数")

    materials_value = mrp_result.get("materials")
    if not isinstance(materials_value, list):
        raise ValueError("MRP 结果缺少 materials 列表")
    materials = [item for item in materials_value if isinstance(item, Mapping)]
    if len(materials) != len(materials_value):
        raise ValueError("MRP 物料格式无效")

    purchase_index = _as_purchase_index(purchase_analysis)
    blocking_materials = _build_blocking_materials(materials, purchase_index)
    unknown_materials = [
        item
        for item in materials
        if item.get("inventory_status") != "known"
    ]
    missing_purchase_parameters = [
        item for item in blocking_materials if item["purchase_status"] == "需要补充采购参数"
    ]
    risk_materials = [item for item in blocking_materials if item["risk_level"] == "高风险"]

    if unknown_materials:
        decision_status = "data_incomplete"
        can_start_production: bool | None = None
        decision_reasons = ["存在库存未知或待确认的纳入 MRP 物料，无法给出确定开工结论。"]
    elif not blocking_materials:
        decision_status = "ready"
        can_start_production = True
        decision_reasons = ["所有纳入 MRP 的物料均已齐套。"]
    elif missing_purchase_parameters:
        decision_status = "blocked"
        can_start_production = False
        decision_reasons = ["存在确定缺料，且至少一项缺料尚未补齐采购参数。"]
    else:
        decision_status = "procurement_pending"
        can_start_production = False
        decision_reasons = ["缺料已有采购方案，但尚无物料到货或库存更新证明，不能开工。"]

    recommended_actions: list[dict[str, object]] = []
    for item in blocking_materials:
        action = (
            "补充采购参数"
            if item["purchase_status"] == "需要补充采购参数"
            else "发起采购"
        )
        recommended_actions.append(
            {
                "priority": item["priority"],
                "material": item["material"],
                "action": action,
                **({"missing_fields": item["missing_fields"]} if action == "补充采购参数" else {}),
            }
        )
        if item["lead_time_days"] is not None and item["lead_time_days"] > 7:
            recommended_actions.append(
                {
                    "priority": item["priority"],
                    "material": item["material"],
                    "action": "优先处理长周期缺料物料",
                }
            )
    if unknown_materials:
        recommended_actions.append(
            {
                "priority": None,
                "material": None,
                "action": "核实库存未知或待确认物料",
            }
        )

    return {
        "product": product,
        "quantity": quantity,
        "can_start_production": can_start_production,
        "decision_status": decision_status,
        "blocking_materials": blocking_materials,
        "risk_materials": risk_materials,
        "missing_purchase_parameters": missing_purchase_parameters,
        "recommended_actions": recommended_actions,
        "decision_reasons": decision_reasons,
        "priority_rule": "按风险等级（高风险、一般风险、未知）、采购提前期、缺料比例、缺料数量、料号升序排序。",
    }
