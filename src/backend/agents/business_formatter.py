"""Presentation of existing evidence only: no stock, cost or risk calculations."""

from __future__ import annotations

from agents.fact_query_agent import _answer_from_result
from agents.business_answer import bom_overview, human_warning, incomplete_production


def known(value):
    return "未提供" if value is None else str(value)


def _present_fields(fields):
    """Render only supplied values, including a valid zero."""
    return "；".join(f"{label}{value}" for label, value in fields if value is not None)


def _purchase_row(row):
    unit = row.get("unit") or ""
    fields = [("缺口 ", row.get("shortage")),
              ("建议补料 ", None if row.get("suggested_purchase_qty") is None else f"{row['suggested_purchase_qty']}{unit}"),
              ("供应商 ", row.get("supplier")),
              ("成本 ", None if row.get("purchase_cost") is None else f"{row['purchase_cost']} {row.get('currency') or ''}".strip())]
    return f"{row['material']}：" + _present_fields(fields) + "。"


_FIELDS = {
    "supplier_code": "供应商", "price.supplier_scope": "价格适用供应商", "price_record_id": "权威价格",
    "minimum_order_quantity": "最小采购量MOQ", "order_multiple": "订单倍数", "packaging_multiple": "包装倍数",
    "lead_time_days": "采购交期", "safety_stock_qty": "安全库存", "available_qty": "可用库存",
    "eligible_inbound_qty": "权威在途数量", "purchase_parameters": "采购参数", "currency": "币种",
}
_RISK = {"high": "高风险", "general": "一般风险", "none": "无缺料风险", "buffer_only": "安全库存补充"}


def format_business_answer(kind: str, result: dict) -> str:
    data = result.get("data") or {}
    if kind == "purchase_planning" and data.get("bom_structure"):
        structure = data["bom_structure"]
        if structure.get("completeness", {}).get("price") is False:
            parent = structure["data"]["parent_item"]["material_code"]
            return (f"已找到 {parent} 的 {structure['data']['revision']} BOM。"
                    "价格数据未提供。")
    if kind == "bom" and result["status"] == "ok":
        return bom_overview(data, result.get("completeness", {}))
    if kind == "production_capacity" and result["status"] in {"data_unavailable", "invalid_query"}:
        return incomplete_production(result)
    if kind == "inventory" and result["status"] == "ok":
        query = result["query"]
        code = query.get("resolved_material_code") or query.get("material_code") or "该物料"
        fields = _present_fields([("在库数量 ", data.get("on_hand_qty")), ("可用数量 ", data.get("available_qty")),
                                  ("预留数量 ", data.get("reserved_qty"))])
        return f"{code}：{fields}。" if fields else f"{code}：库存数据未匹配。"
    if kind == "fact_query":
        if result["status"] != "ok":
            return "当前没有匹配的已发布产品BOM信息；请确认名称关键词或产品编码，也可以请求查看产品目录。" if result["status"] == "data_unavailable" else "产品目录暂不可用，请稍后重试。"
        lines = ["已发布产品："]
        for product in data.get("products", []):
            versions = "、".join(row["revision"] for row in product["revisions"])
            name = f"（{product['product_name']}）" if product.get("product_name") else ""
            lines.append(f"{product['product_code']}{name}，BOM版本 {versions}。")
        if data.get("truncated"):
            lines.append("目录结果已截断，请提供名称或编码关键词缩小查询范围。")
        return "\n".join(lines)
    if kind == "purchase_planning" and data.get("recommendations"):
        text = ["物料补料需求："]
        for row in data["recommendations"][:10]:
            text.append(_purchase_row(row))
            if row.get("missing_fields"):
                text.append("建议核实：" + "、".join(_FIELDS.get(key, "业务参数") for key in row["missing_fields"]) + "。")
        if data.get("total_purchase_cost") is not None:
            text.append(f"总采购成本：{data['total_purchase_cost']} {data.get('total_cost_currency') or ''}。")
        return " ".join(text) + " 结合供应商交期与未交付订单评估补料方案。"
    if kind == "inventory_risk":
        if result["status"] != "ok" or not data.get("inventory_lots"):
            return "库存风险数据未匹配。"
        fields = _present_fields([("在库 ", data.get("on_hand_qty")), ("可用 ", data.get("available_qty")),
                                  ("预留 ", data.get("reserved_qty"))])
        text = [f"库存健康诊断：{fields}。"] if fields else ["库存健康诊断："]
        labels = {"normal": "正常", "expired": "已过期", "near_expiry": "临期", "stagnant": "呆滞", "data_unavailable": "数据不足"}
        for title, key, age in (("临期风险", "expiry_assessments", "days_remaining"), ("呆滞风险", "stagnant_assessments", "age_days")):
            for row in data.get(key, [])[:10]:
                detail = f"{title}，批次 {row.get('batch_no') or '未标批次'}：{labels.get(row.get('status'), '状态待核实')}"
                if row.get(age) is not None:
                    detail += f"，{'剩余天数' if age == 'days_remaining' else '库存事件距分析日天数'} {row[age]}"
                text.append(detail + "。")
        safety = data.get("safety_stock") or {}
        fields = _present_fields([("安全库存 ", safety.get("safety_stock_qty")), ("安全库存缺口 ", safety.get("gap_qty"))])
        if fields:
            text.append(fields + "。")
        return " ".join(text) + " " + "；".join(human_warning(w) for w in result.get("warnings", []))
    if kind == "inventory_fifo":
        if result["status"] != "ok":
            return "FIFO分析数据不足：" + "；".join(human_warning(w) for w in result.get("warnings", []))
        text = ["FIFO领料方案：按入库顺序使用。"]
        for row in data.get("allocations", []):
            text.append(f"批次 {row['batch_no']}，入库时间 {row['received_at']}，分配 {row['allocated_qty']}；原因：按已有入库顺序。")
        return " ".join(text) + (f" 未满足需求：{data['shortage_qty']}。" if data.get("shortage_qty") is not None else "")
    if kind == "inventory_batches":
        answer = _answer_from_result(kind, result).split(" 数据来源：", 1)[0]
        if result["status"] == "ok":
            for row in data.get("batches", [])[:10]:
                fields = _present_fields([("有效期 ", row.get("expiry_date")), ("入库日期 ", row.get("received_at"))])
                if fields:
                    answer += f" 批次 {row.get('batch_no') or '未标批次'}：{fields}。"

            if result.get("warnings"):
                answer += " 提示：" + "；".join(human_warning(w) for w in result["warnings"]) + "。"
        return answer
    from services.persistent_agent_service import _answer
    return _answer(kind, result)


def format_purchase_email(kind: str, result: dict) -> str:
    data = result.get("data") or {}
    if kind != "purchase_planning" or not data.get("recommendations"):
        return "请先选择生产数量并生成采购清单。"
    lines = ["主题：采购需求确认（草稿）", "采购及供应商业务负责人：", "请审核以下基于已发布事实的采购需求："]
    for row in data["recommendations"][:10]:
        lines.append(_purchase_row(row))
        if row.get("missing_fields"):
            lines.append("待确认：" + "、".join(_FIELDS.get(key, "相应权威业务字段") for key in row["missing_fields"]))
    lines += ["请核实库存、在途数量及未交付订单，并确认供应商报价与交期。"]
    return "\n".join(lines)
