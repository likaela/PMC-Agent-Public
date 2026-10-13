"""确定性 PMC 结构化报告服务。"""

from collections.abc import Mapping


def build_pmc_report(
    product: str,
    bom_version: str,
    production_quantity: int,
    bom_requirements: Mapping[str, int],
    mrp_result: Mapping[str, object],
    purchase_analysis: Mapping[str, list[dict[str, object]]],
    data_quality_status: Mapping[str, object],
    production_decision: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """汇总 BOM、MRP 与采购分析，不依赖 LLM 计算核心结论。"""
    materials = list(mrp_result["materials"])
    known_shortages = [
        item
        for item in materials
        if item["inventory_status"] == "known" and item["shortage"] not in (None, 0)
    ]
    unknown_inventory = [item for item in materials if item["inventory_status"] == "missing"]
    pending_confirmation = [
        item for item in materials if item["inventory_status"] == "excluded_pending_confirmation"
    ]
    business_disposition_items = [
        item for item in materials if item["inventory_status"] == "business_disposition"
    ]
    limitations = [
        "一期可用库存仅来自已清洗的主库存表。",
        "reserved 与 incoming 在当前真实来源中按阶段性假设为 0。",
    ]
    if unknown_inventory:
        limitations.append("存在所有审计库存来源均未记录的 BOM 料号，生产可行性不能给出确定结论。")
    if pending_confirmation:
        limitations.append("存在待确认库存来源料号，需完成来源归属、时点与去重对账。")
    if business_disposition_items:
        limitations.append("存在已确认但尚未建模的业务处理口径，当前不折算为普通库存或删除 BOM。")
    if purchase_analysis["parameter_pending"]:
        limitations.append("已知缺料物料的采购参数需在业务交互中逐项补充，未生成虚假采购建议。")

    return {
        "product_information": {"product": product, "production_quantity": production_quantity},
        "bom_version": bom_version,
        "bom_material_count": len(bom_requirements),
        "mrp_material_count": len(materials),
        "inventory_data_integrity": {
            "known_material_count": mrp_result["known_material_count"],
            "unknown_inventory_count": mrp_result["unknown_inventory_count"],
            "missing_inventory_count": mrp_result["missing_inventory_count"],
            "pending_confirmation_count": mrp_result["pending_confirmation_count"],
            "business_disposition_count": mrp_result["business_disposition_count"],
            "source_policy": data_quality_status["source_policy"],
            "excluded_inventory_data_error_count": len(
                data_quality_status.get("inventory_data_error_exclusions", [])
            ),
        },
        "known_inventory_kit_analysis": {
            "material_readiness_rate": mrp_result["known_material_readiness_rate"],
            "quantity_fulfillment_rate": mrp_result["known_quantity_fulfillment_rate"],
            "known_production_status": mrp_result["known_production_status"],
        },
        "shortage_list": known_shortages,
        "unknown_inventory_list": unknown_inventory,
        "pending_confirmation_inventory_list": pending_confirmation,
        "business_disposition_list": business_disposition_items,
        "purchase_recommendations": purchase_analysis["recommendations"],
        "purchase_parameter_missing_items": purchase_analysis["parameter_pending"],
        "production_feasibility": {
            "can_produce": mrp_result["can_produce"],
            "production_status": mrp_result["production_status"],
        },
        "production_decision": dict(production_decision) if production_decision is not None else None,
        "data_limitations_and_assumptions": limitations,
        "data_quality_status": dict(data_quality_status),
    }


def render_pmc_report_markdown(report: Mapping[str, object]) -> str:
    """将结构化报告渲染为便于人工审阅的 Markdown。"""
    product = report["product_information"]
    integrity = report["inventory_data_integrity"]
    feasibility = report["production_feasibility"]
    decision = report.get("production_decision")
    lines = [
        "# KQ-Z112 真实 PMC 报告",
        "",
        "## 产品信息",
        "",
        f"- 产品：{product['product']}",
        f"- BOM 版本：{report['bom_version']}",
        f"- 生产数量：{product['production_quantity']}",
        f"- BOM 物料数量：{report['bom_material_count']}",
        f"- 纳入 MRP 的物料数量：{report['mrp_material_count']}",
        "",
        "## 库存数据完整性",
        "",
        f"- 已知库存物料：{integrity['known_material_count']}",
        f"- 库存未知：{integrity['unknown_inventory_count']}",
        f"- 待确认库存来源：{integrity['pending_confirmation_count']}",
        f"- 已排除的库存错误记录：{integrity['excluded_inventory_data_error_count']}",
        f"- 当前生产可行性：{feasibility['production_status']}（can_produce={feasibility['can_produce']}）",
        "",
        "## 已知库存范围内的齐套分析",
        "",
        f"- 物料种类齐套率：{report['known_inventory_kit_analysis']['material_readiness_rate']}",
        f"- 数量满足率：{report['known_inventory_kit_analysis']['quantity_fulfillment_rate']}",
        "",
        "## 缺料清单",
        "",
    ]
    lines.extend(
        [f"- {item['material']}：缺料 {item['shortage']}" for item in report["shortage_list"]]
        or ["- 无已知库存范围内的缺料记录"]
    )
    if isinstance(decision, Mapping):
        lines.extend(["", "## 生产决策", ""])
        lines.extend(
            [
                f"- 决策状态：{decision['decision_status']}",
                f"- 是否可开工：{decision['can_start_production']}",
                f"- 决策依据：{'；'.join(decision['decision_reasons'])}",
                f"- 缺料优先级规则：{decision['priority_rule']}",
                "",
                "### 阻塞物料与优先级",
                "",
            ]
        )
        lines.extend(
            [
                f"- P{item['priority']} {item['material']}：缺料 {item['shortage']}，"
                f"缺料比例 {item['shortage_ratio']}，采购状态 {item['purchase_status']}"
                for item in decision["blocking_materials"]
            ]
            or ["- 无"]
        )
        lines.extend(["", "### 建议行动", ""])
        lines.extend(
            [
                f"- P{item['priority']} {item['material'] or '全部相关物料'}：{item['action']}"
                for item in decision["recommended_actions"]
            ]
            or ["- 无"]
        )
    lines.extend(["", "## 库存未知清单", ""])
    lines.extend(
        [f"- {item['material']}：需求 {item['required']}" for item in report["unknown_inventory_list"]]
        or ["- 无"]
    )
    lines.extend(["", "## 待确认库存来源清单", ""])
    lines.extend(
        [f"- {item['material']}：需求 {item['required']}" for item in report["pending_confirmation_inventory_list"]]
        or ["- 无"]
    )
    lines.extend(["", "## 已确认业务处理口径", ""])
    lines.extend(
        [
            f"- {item['material']}：{item['business_disposition']}（include_in_mrp={item['include_in_mrp']}）"
            for item in report["business_disposition_list"]
        ]
        or ["- 无"]
    )
    lines.extend(["", "## 采购建议", ""])
    lines.extend(
        [
            f"- {item['material']}：建议采购 {item['suggested_purchase_qty']}，供应商 {item['supplier_code']}，风险 {item['risk_level']}"
            for item in report["purchase_recommendations"]
        ]
        or ["- 无可确定的采购建议"]
    )
    lines.extend(["", "## 采购参数缺失项", ""])
    lines.extend(
        [f"- {item['material']}：缺失 {', '.join(item['missing_fields'])}" for item in report["purchase_parameter_missing_items"]]
        or ["- 无"]
    )
    lines.extend(["", "## 数据限制与假设", ""])
    lines.extend([f"- {item}" for item in report["data_limitations_and_assumptions"]])
    return "\n".join(lines) + "\n"
