"""Presentation of existing evidence only: no stock, cost or risk calculations."""

from __future__ import annotations

from agents.fact_query_agent import _answer_from_result
from agents.business_answer import bom_overview, human_warning, incomplete_production


def known(value):
    return "未知" if value is None else str(value)


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
                    "目前缺少可靠的价格数据，暂时无法计算完整成本；未知价格不会补零，也不估算采购金额。")
    if kind == "bom" and result["status"] == "ok":
        return bom_overview(data, result.get("completeness", {}))
    if kind == "production_capacity" and result["status"] in {"data_unavailable", "invalid_query"}:
        return incomplete_production(result)
    if kind == "inventory" and result["status"] == "ok":
        query = result["query"]
        code = query.get("resolved_material_code") or query.get("material_code") or "该物料"
        return (f"{code} 的已发布在库数量为 {known(data.get('on_hand_qty'))}；"
                f"可用数量为 {known(data.get('available_qty'))}；预留数量为 {known(data.get('reserved_qty'))}。"
                + ("尚缺少权威可用库存数据，不能仅凭在库数量判断是否可用。" if data.get("available_qty") is None else ""))
    if kind == "fact_query":
        if result["status"] != "ok":
            return "当前没有匹配的已发布产品BOM信息；请确认名称关键词或产品编码，也可以请求查看产品目录。" if result["status"] == "data_unavailable" else "产品目录暂不可用，请稍后重试。"
        lines = ["已找到以下已发布BOM父件（可能包含中间组件）："]
        for product in data.get("products", []):
            versions = "、".join(row["revision"] for row in product["revisions"])
            lines.append(f"{product['product_code']}：{product['product_name'] or '名称未提供'}；BOM已发布，版本 {versions}。")
        lines.append("这不是可生产清单，尚未检查库存。请选定产品编码并告诉我生产数量，我再核验有效BOM和齐套情况。")
        if data.get("truncated"):
            lines.append("目录结果已截断，请提供名称或编码关键词缩小查询范围。")
        return "\n".join(lines)
    if kind == "purchase_planning" and data.get("recommendations"):
        text = ["采购分析基于已发布参数、库存和在途事实。"]
        for row in data["recommendations"][:10]:
            text.append(f"{row['material']}：缺口 {known(row.get('shortage'))}，建议采购 {known(row.get('suggested_purchase_qty'))} {row.get('unit') or ''}；"
                        f"供应商 {known(row.get('supplier'))}；成本 {known(row.get('purchase_cost'))} {row.get('currency') or ''}；"
                        f"风险 {_RISK.get(row.get('risk_level'), '未知')}。")
            if row.get("missing_fields"):
                text.append("待补充：" + "、".join(_FIELDS.get(key, "相应权威业务字段") for key in row["missing_fields"]) + "。")
        text.append(f"总采购成本：{known(data.get('total_purchase_cost'))} {data.get('total_cost_currency') or ''}。")
        return " ".join(text) + " 未知字段不作估算；建议不代表已下单或已到货。"
    if kind == "inventory_risk":
        if result["status"] != "ok" or not data.get("inventory_lots"):
            return "当前没有足够的已发布库存事实支持风险判断。" + "；".join(human_warning(w) for w in result.get("warnings", []))
        text = [f"库存健康诊断：在库 {known(data.get('on_hand_qty'))}；可用 {known(data.get('available_qty'))}；预留 {known(data.get('reserved_qty'))}。"]
        labels = {"normal": "正常", "expired": "已过期", "near_expiry": "临期", "stagnant": "呆滞", "data_unavailable": "数据不足"}
        for title, key, age in (("临期风险", "expiry_assessments", "days_remaining"), ("呆滞风险", "stagnant_assessments", "age_days")):
            for row in data.get(key, [])[:10]:
                text.append(f"{title}，批次 {known(row.get('batch_no'))}：{labels.get(row.get('status'), '未知')}，"
                            f"{'剩余天数' if age == 'days_remaining' else '库存事件距分析日天数'} {known(row.get(age))}。")
        safety = data.get("safety_stock", {})
        text.append(f"安全库存：{known(safety.get('safety_stock_qty'))}；安全库存缺口：{known(safety.get('gap_qty'))}。")
        text.append("生产缺料风险需要明确生产需求和BOM证据，不能用临期、呆滞或安全库存缺口替代。")
        return " ".join(text) + " " + "；".join(human_warning(w) for w in result.get("warnings", []))
    if kind == "inventory_fifo":
        if result["status"] != "ok":
            return "FIFO分析数据不足：" + "；".join(human_warning(w) for w in result.get("warnings", []))
        text = ["FIFO按明确入库时间先入先出，不是按有效期排序的FEFO；这是模拟领用，不扣减真实库存。"]
        for row in data.get("allocations", []):
            text.append(f"批次 {row['batch_no']}，入库时间 {row['received_at']}，分配 {row['allocated_qty']}；原因：按已有入库顺序。")
        return " ".join(text) + f" 未满足需求：{known(data.get('shortage_qty'))}。"
    if kind == "inventory_batches":
        answer = _answer_from_result(kind, result).split(" 数据来源：", 1)[0]
        if result["status"] == "ok":
            for row in data.get("batches", [])[:10]:
                answer += f" 批次 {known(row.get('batch_no'))}：有效期 {known(row.get('expiry_date'))}，入库日期 {known(row.get('received_at'))}。"
            answer += " 批次列表本身不是FIFO推荐；推荐需要明确领用量与日期事实。"
            if result.get("warnings"):
                answer += " 提示：" + "；".join(human_warning(w) for w in result["warnings"]) + "。"
        return answer
    from services.persistent_agent_service import _answer
    return _answer(kind, result)


def format_purchase_email(kind: str, result: dict) -> str:
    data = result.get("data") or {}
    if kind != "purchase_planning" or not data.get("recommendations"):
        return "当前没有足够采购证据生成邮件草稿，请先完成采购分析。"
    lines = ["主题：采购需求确认（草稿）", "采购及供应商业务负责人：", "请审核以下基于已发布事实的采购需求："]
    for row in data["recommendations"][:10]:
        lines.append(f"物料 {row['material']}，供应商 {known(row.get('supplier'))}；缺口 {known(row.get('shortage'))}，"
                     f"建议采购 {known(row.get('suggested_purchase_qty'))} {row.get('unit') or ''}；"
                     f"成本 {known(row.get('purchase_cost'))} {row.get('currency') or ''}；紧急程度 {_RISK.get(row.get('risk_level'), '未知')}。")
        lines.append("原因：" + "；".join(row.get("reason", [])))
        if row.get("missing_fields"):
            lines.append("待确认：" + "、".join(_FIELDS.get(key, "相应权威业务字段") for key in row["missing_fields"]))
    lines += ["期望交付时间：待业务人员确认；不将模拟交期作为供应商承诺。", "请确认供应能力、报价及交付安排。", "仅供人工审核与发送，不执行下单或邮件发送操作。"]
    return "\n".join(lines)
