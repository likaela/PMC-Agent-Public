"""MRP 齐套与生产可行性计算服务。"""

from collections.abc import Mapping

from services.inventory_service import check_inventory


def analyze_mrp(
    requirements: Mapping[str, int],
    inventory_data: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """计算齐套与数据完整性；未知库存不被伪造成零库存。"""
    if not requirements:
        raise ValueError("最终物料需求不能为空")

    materials: list[dict[str, object]] = []
    ready_material_count = 0
    fulfilled_known_quantity = 0
    total_known_required_quantity = 0
    has_zero_available_material = False
    known_material_count = 0
    missing_inventory_count = 0
    pending_confirmation_count = 0

    for material, required in requirements.items():
        if (
            not isinstance(material, str)
            or not isinstance(required, int)
            or isinstance(required, bool)
            or required <= 0
        ):
            raise ValueError("最终物料需求必须为 {物料名称: 正整数} 格式")

        item = inventory_data.get(material, {"inventory_status": "missing"})
        inventory_status = item.get("inventory_status", "known")
        if inventory_status not in {"known", "missing", "excluded_pending_confirmation", "business_disposition"}:
            raise ValueError(f"物料 {material} 的库存状态无效")

        if inventory_status != "known":
            if inventory_status == "missing":
                missing_inventory_count += 1
            elif inventory_status == "excluded_pending_confirmation":
                pending_confirmation_count += 1
            materials.append(
                {
                    "material": material,
                    "required": required,
                    "inventory_status": inventory_status,
                    "stock": None,
                    "available": None,
                    "shortage": None,
                    "is_ready": None,
                    **(
                        {
                            "business_disposition": item.get("business_disposition"),
                            "include_in_mrp": item.get("include_in_mrp"),
                        }
                        if inventory_status == "business_disposition"
                        else {}
                    ),
                }
            )
            continue

        inventory_result = check_inventory({material: required}, {material: item})
        known_item = inventory_result[material]
        stock = known_item["stock"]
        available = known_item["available"]
        shortage = known_item["shortage"]
        is_ready = shortage == 0
        known_material_count += 1
        if is_ready:
            ready_material_count += 1
        if available == 0:
            has_zero_available_material = True

        fulfilled_known_quantity += min(required, available)
        total_known_required_quantity += required
        materials.append(
            {
                "material": material,
                "required": required,
                "inventory_status": "known",
                "stock": stock,
                "available": available,
                "shortage": shortage,
                "is_ready": is_ready,
            }
        )

    business_disposition_count = sum(
        item["inventory_status"] == "business_disposition" for item in materials
    )
    unknown_inventory_count = (
        missing_inventory_count + pending_confirmation_count + business_disposition_count
    )
    known_material_readiness_rate = (
        ready_material_count / known_material_count if known_material_count else None
    )
    known_quantity_fulfillment_rate = (
        fulfilled_known_quantity / total_known_required_quantity if total_known_required_quantity else None
    )
    if unknown_inventory_count:
        can_produce: bool | None = None
        production_status = "库存数据不完整"
        known_production_status = "严重缺料" if has_zero_available_material else "已知库存范围内分析完成"
    elif ready_material_count == len(materials):
        can_produce = True
        production_status = "齐套"
        known_production_status = production_status
    elif has_zero_available_material:
        can_produce = False
        production_status = "严重缺料"
        known_production_status = production_status
    else:
        can_produce = False
        production_status = "部分缺料"
        known_production_status = production_status

    return {
        "materials": materials,
        "material_readiness_rate": known_material_readiness_rate if not unknown_inventory_count else None,
        "quantity_fulfillment_rate": known_quantity_fulfillment_rate if not unknown_inventory_count else None,
        "known_material_readiness_rate": known_material_readiness_rate,
        "known_quantity_fulfillment_rate": known_quantity_fulfillment_rate,
        "known_material_count": known_material_count,
        "unknown_inventory_count": unknown_inventory_count,
        "missing_inventory_count": missing_inventory_count,
        "pending_confirmation_count": pending_confirmation_count,
        "business_disposition_count": business_disposition_count,
        "can_produce": can_produce,
        "production_status": production_status,
        "known_production_status": known_production_status,
    }
