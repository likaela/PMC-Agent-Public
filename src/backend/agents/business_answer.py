"""Evidence-to-language projections. No quantity, stock or cost calculations."""

from __future__ import annotations

import re


WARNING_LABELS = {
    "parent_unit_missing": "成品单位待确认",
    "unit_missing": "物料单位待确认",
    "price_missing": "物料价格未提供",
    "effective_date_missing": "BOM生效日期待确认",
    "available_qty_missing": "可用库存未提供",
    "inventory_unknown": "库存数据未匹配",
    "data_unavailable": "本次核算数据不完整",
}


def human_warning(value: str) -> str:
    if "当前没有明确的已发布业务时点" in value:
        return "请指定本次分析日期"
    for key, label in WARNING_LABELS.items():
        if key in value:
            return label
    # Technical diagnostics remain in the stored Evidence.
    if re.search(r"[A-Za-z]+_[A-Za-z_]+|[a-fA-F0-9]{64}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}|[A-Za-z]:[\\/]", value):
        return "数据记录待核实"
    return value


def visible_warnings(warnings: list[str], *, price_relevant: bool) -> list[str]:
    """User-facing relevance only; the original Evidence remains unmodified."""
    return [value for value in warnings if price_relevant or not (
        value.startswith("price_missing") or value.startswith("部分物料价格尚未确认"))]


def material_categories(data: dict) -> list[str]:
    # Labels must occur literally in published names, not be inferred from codes.
    names = [str((line.get("child_item") or {}).get("name") or "")
             for line in data.get("lines", [])]
    return [label for label in ("PCB", "电容", "电阻", "LED", "二极管", "磁珠", "MOS", "芯片", "晶振", "连接器")
            if any(label.lower() in name.lower() for name in names)]


def bom_overview(data: dict, completeness: dict) -> str:
    product = (data.get("parent_item") or {}).get("material_code") or "该产品"
    details = [f"{product} BOM"]
    if data.get("revision"):
        details.append(f"版本 {data['revision']}")
    if data.get("line_count") is not None:
        details.append(f"{data['line_count']}项物料")
    text = "，".join(details) + "。"
    if data.get("effective_from") is not None:
        text += f"生效日期：{data['effective_from']}。"
    categories = material_categories(data)
    if categories:
        text += "物料组成：" + "、".join(categories) + "。"
    if completeness.get("unit") is True:
        units = list(dict.fromkeys(line["unit"] for line in data.get("lines", []) if line.get("unit") is not None))
        if units:
            text += "BOM子料单位：" + "、".join(units) + "。"
    elif completeness.get("unit") is False:
        text += "物料单位待确认，数量核算待完成。"
    if completeness.get("parent_unit") is False:
        text += "成品单位待确认。"
    elif completeness.get("parent_unit") is True and data.get("parent_item", {}).get("unit"):
        text += "成品单位：" + data["parent_item"]["unit"] + "。"
    return text + "物料与单耗见明细。"


def business_decision(kind: str, result: dict) -> dict:
    """Presentation permission is NOT a replacement for a production verdict."""
    data = result.get("data") or {}
    if kind != "production_capacity":
        return {"status": "INFORMATIONAL", "reason": None}
    if result.get("status") != "ok":
        return {"status": "NOT_APPROVED", "reason": "NOT_APPROVED_DUE_TO_INSUFFICIENT_EVIDENCE"}
    verdict = data.get("inventory_sufficient_for_requested")
    if verdict is False:
        return {"status": "NOT_APPROVED", "reason": "CANNOT_PRODUCE_DUE_TO_SHORTAGE"}
    # Stock sufficiency alone is not an enterprise approval or arrival commitment.
    return {"status": "INFORMATIONAL", "reason": "INVENTORY_SUFFICIENT" if verdict is True else None}


def incomplete_production(result: dict) -> str:
    query, data = result.get("query", {}), result.get("data") or {}
    product = query.get("product") or query.get("material_code") or "该产品"
    quantity = query.get("quantity")
    if quantity is None:
        quantity = query.get("requested_quantity")
    structure = data.get("bom_structure") or {}
    bom = structure.get("data") or {}
    unit = (bom.get("parent_item") or {}).get("unit") or ""
    task = f"{product}生产{quantity}{unit}" if quantity is not None else f"{product}生产需求"
    text = f"{task}：核算数据待补齐。"
    facts = []
    if bom.get("revision"):
        facts.append(f"BOM {bom['revision']}")
    if bom.get("line_count") is not None:
        facts.append(f"{bom['line_count']}项物料")
    if facts:
        text += "，".join(facts) + "。"
    complete = structure.get("completeness") or {}
    states = []
    if complete.get("unit") is False:
        states.append("物料单位待确认")
    if complete.get("parent_unit") is False:
        states.append("成品单位待确认")
    if not states:
        states = [human_warning(w) for w in visible_warnings(result.get("warnings", []), price_relevant=False)]
    states = list(dict.fromkeys(states))
    return text + ("；".join(states[:3]) + "。" if states else "库存数据未匹配。")


def source_overview(kind: str, result: dict) -> str:
    data = result.get("data") or {}
    structure = data.get("bom_structure")
    if structure:
        return source_overview("bom", structure)
    query = result.get("query", {})
    product = (data.get("parent_item") or {}).get("material_code") or data.get("product") or query.get("material_code") or query.get("product") or "当前查询对象"
    revision = data.get("revision") or query.get("revision")
    if kind == "bom" or revision:
        return f"来自企业已发布的 {product} {revision or ''} BOM及本次分析所引用的事实。详细文件、行级审计及数据完整性信息保留在来源详情中。"
    return f"来自企业已发布的 {product} 查询事实。数据时点及文件审计信息保留在来源详情中；未使用未发布数据。"


__all__ = ["human_warning", "bom_overview", "business_decision", "incomplete_production", "source_overview"]
