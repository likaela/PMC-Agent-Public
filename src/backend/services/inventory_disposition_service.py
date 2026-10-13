"""库存业务确认的标准化状态应用服务。"""

from collections.abc import Mapping


INDEPENDENT_BUSINESS_DISPOSITIONS = {
    "non_stock_item",
    "purchase_to_order",
    "customer_supplied",
    "outsourced",
    "bom_obsolete",
}


def apply_inventory_dispositions(
    inventory_scope: Mapping[str, Mapping[str, object]],
    dispositions: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, object]]:
    """应用已确认业务口径；不会删除或跳过任何 BOM 物料。"""
    result = {material: dict(item) for material, item in inventory_scope.items()}
    for material, disposition_record in dispositions.items():
        if material not in result:
            continue
        disposition = disposition_record.get("disposition")
        include_in_mrp = disposition_record.get("include_in_mrp")
        if not isinstance(disposition, str) or not isinstance(include_in_mrp, bool):
            raise ValueError(f"物料 {material} 的库存确认记录无效")

        item = result[material]
        item["business_disposition"] = disposition
        item["include_in_mrp"] = include_in_mrp
        item["disposition_reason"] = disposition_record.get("reason", "")
        item["confirmed_by"] = disposition_record.get("confirmed_by", "")

        if disposition == "stock_zero":
            confirmed_stock = disposition_record.get("confirmed_stock")
            if confirmed_stock != 0 or not include_in_mrp:
                raise ValueError(f"物料 {material} 的 stock_zero 确认不完整")
            item.update(
                {
                    "inventory_status": "known",
                    "stock": 0,
                    "reserved": 0,
                    "incoming": 0,
                    "source_assumptions": {
                        "inventory_confirmation": "stock_zero",
                        "reason": disposition_record.get("reason", ""),
                        "confirmed_by": disposition_record.get("confirmed_by", ""),
                    },
                }
            )
        elif disposition in {"inventory_missing", "pending_confirmation"}:
            # 明确保留原库存不确定性，避免把业务确认标签误当作库存数量或来源存在性。
            if disposition == "inventory_missing":
                item["inventory_status"] = "missing"
            item.update({"stock": None, "available": None, "shortage": None})
        elif disposition in INDEPENDENT_BUSINESS_DISPOSITIONS:
            # 业务状态被保留，当前阶段不把它们折算为普通库存或从 BOM 中删除。
            item["inventory_status"] = "business_disposition"
            item.update({"stock": None, "available": None, "shortage": None})
        else:
            raise ValueError(f"物料 {material} 的业务处理口径不受支持")
    return result
