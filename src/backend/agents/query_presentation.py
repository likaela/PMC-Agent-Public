"""Intent-specific projections; original Tool evidence stays unchanged."""
import copy
import re


def maximum_requested(message):
    return bool(re.search(r"最多|最大(?:可|能)?(?:生产|产能|产量|数量|满足)|生产上限|产能(?:上限)?|数量上限", message))


def present_query(response, message, binding=None):
    value = copy.deepcopy(response)
    structured = value.get("structured_data") or {}
    presentation = structured.setdefault("presentation", {})
    presentation["show_maximum"] = maximum_requested(message)
    if binding:
        structured["product_context"] = copy.deepcopy(binding)
    data = structured.get("data") or (structured.get("reused_result") or {}).get("data") or {}
    kind = structured.get("query_type", value.get("intent"))
    result_type = ("source" if re.search(r"来源|依据|来自哪里", message) else
        "shortage" if structured.get("operation") == "explain" and re.search(r"不足|缺|限制|为什么", message) else
        "maximum" if presentation["show_maximum"] else
        {"product_search": "catalog", "bom": "bom", "inventory": "inventory", "production_capacity": "production",
         "purchase_planning": "purchase"}.get(kind, kind))
    if isinstance(data.get("products"), list):
        result_type = "catalog"
    presentation.update(result_type=result_type, policy_version="business_result_v1")
    # Keep raw warnings with the structured audit/Evidence, not as KPI cards.
    structured.setdefault("audit_warnings", copy.deepcopy(value.get("warnings", [])))
    presentation["user_warnings"] = [] if value["status"] == "ok" else copy.deepcopy(value.get("warnings", []))
    if data.get("calculation_scope") == "excel_book_observation":
        presentation["user_warnings"] = []  # Scope is already expressed beside the result.
    elif kind == "inventory" and value["status"] == "ok" and not any(
            key in data for key in ("expiry_assessments", "stagnant_assessments", "allocations", "safety_stock")):
        item = data.get("canonical_item") or {}
        code = item.get("material_code") or structured.get("query", {}).get("material_code") or "该物料"
        fields = [("在库数量", data.get("on_hand_qty")), ("可用库存", data.get("available_qty")), ("预留数量", data.get("reserved_qty"))]
        known = [f"{label}{number}{item.get('unit') or ''}" for label, number in fields if number is not None]
        value["answer"] = f"{code}：" + ("；".join(known) + "。" if known else "当前没有可展示的库存数量。")
        if re.search(r"能.*用|可用|预留", message) and data.get("available_qty") is None:
            value["answer"] += "可用量未知，现有在库数量不能确定可用量。"
    if kind == "production_capacity" and data and not data.get("slim_review") and data.get("calculation_scope") != "excel_book_observation" and value["status"] == "ok" and result_type != "source":
        n, sufficient = data.get("requested_quantity"), data.get("inventory_sufficient_for_requested")
        if n is not None and not maximum_requested(message) and isinstance(sufficient, bool):
            product = data.get("product") or (binding or {}).get("product_code", "当前产品")
            version = data.get("revision") or data.get("bom_version") or structured.get("query", {}).get("revision") or structured.get("query", {}).get("bom_version")
            value["answer"] = f"已完成{product}{' / ' + version if version else ''}生产{n}{data.get('product_unit') or ''}的物料需求核对。" + ("所列物料满足本次数量需求。" if sufficient else "本次数量存在物料缺口。")
            rows = data.get("materials") or []
            shortages = [r for r in rows if r.get("is_ready") is False]
            if shortages:
                value["answer"] += "不足物料：" + "；".join(f"{r.get('material_code')}缺{r.get('shortage_qty')}" for r in shortages[:5]) + "。"
            if structured.get("reused_from_turn_id"):
                value["answer"] = "依据本会话已有结果（不是最新库存重查）：" + value["answer"]
            value["answer"] += "库存核对不代表正式生产放行。"
    return value
