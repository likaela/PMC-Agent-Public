"""Read-only prepublication data checks, not production or cost calculations.

Consumes normalized Python rows; never reads Excel, calls an Agent, opens a
database, publishes facts or derives available quantities from on-hand stock.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


def _text(value: object) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _nonnegative_number(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        number = Decimal(str(value))
        return number.is_finite() and number >= 0
    except (InvalidOperation, ValueError):
        return False


def _coverage(known: set[str], expected: set[str]) -> dict:
    total = len(expected)
    ratio = Decimal(len(known)) / Decimal(total) if total else None
    return {
        "known_material_count": len(known), "total_material_count": total,
        "unknown_material_count": len(expected - known),
        "ratio": str(ratio) if ratio is not None else None,
        "percentage": f"{ratio * 100:.2f}%" if ratio is not None else None,
        "complete": bool(total) and known == expected,
        "unknown_material_codes": sorted(expected - known),
    }


def _effective_date(lines: list[dict], source: str | None) -> dict:
    values = [row.get("effective_from") for row in lines]
    known = {str(value) for value in values if value is not None}
    valid = True
    for value in known:
        try:
            date.fromisoformat(value)
        except ValueError:
            valid = False
    if not valid:
        status = "invalid"
    elif len(known) > 1:
        status = "conflicting"
    elif not lines or any(value is None for value in values):
        status = "pending_confirmation"
    else:
        status = "confirmed" if source == "business_confirmation" else "source_provided"
    return {"status": status, "value": next(iter(known)) if len(known) == 1 and valid else None,
            "complete": status in {"confirmed", "source_provided"}, "source": source,
            "known_dates": sorted(known)}


def _snapshot(value: str | None) -> dict:
    if value is None:
        return {"status": "pending_confirmation", "value": None, "complete": False}
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        valid = parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        valid = False
    return {"status": "confirmed" if valid else "invalid", "value": value, "complete": valid}


def build_enterprise_readiness_report(*, product: str, revision: str, bom_lines: list[dict],
        parent_unit: str | None, inventory_rows: list[dict], snapshot_at: str | None,
        effective_date_source: str | None = None, available_field_present: bool = False,
        validation_errors: list[dict] | None = None, provenance: dict | None = None) -> dict:
    """Check prerequisites only; ready data never implies production is feasible."""
    errors = validation_errors or []
    expected = {code for row in bom_lines if (code := _text(row.get("material_code")))}
    by_material = defaultdict(list)
    for row in bom_lines:
        if (code := _text(row.get("material_code"))):
            by_material[code].append(row)
    unit_known = {code for code, rows in by_material.items() if all(_text(row.get("unit")) for row in rows)}
    units = _coverage(unit_known, expected)
    units.update(parent_unit=parent_unit, parent_unit_known=_text(parent_unit) is not None)
    units["complete"] = units["complete"] and units["parent_unit_known"]
    effective = _effective_date(bom_lines, effective_date_source)
    snapshot = _snapshot(snapshot_at)
    valid_count = sum(1 for row in bom_lines if row.get("validation_status") == "valid"
                      and _text(row.get("material_code")) and _nonnegative_number(row.get("quantity"))
                      and Decimal(str(row["quantity"])) > 0)
    relations_complete = bool(bom_lines) and valid_count == len(bom_lines) and bool(product and revision)
    inventory = defaultdict(list)
    for row in inventory_rows:
        if (code := _text(row.get("material_code"))) in expected:
            inventory[code].append(row)
    on_hand_known = {code for code, rows in inventory.items()
                     if all(_nonnegative_number(row.get("on_hand_qty")) for row in rows)}
    available_known = {code for code, rows in inventory.items()
                       if available_field_present and all(_nonnegative_number(row.get("available_qty")) for row in rows)}
    on_hand = _coverage(on_hand_known, expected)
    available = _coverage(available_known, expected)
    available["field_status"] = ("not_provided" if not available_field_present else
                                 "complete" if available["complete"] else "incomplete")
    priced_count = sum(1 for row in bom_lines if _nonnegative_number(row.get("unit_price")))
    currencies = {_text(row.get("currency")) for row in bom_lines}
    currency_complete = bool(bom_lines) and None not in currencies
    price_complete = bool(bom_lines) and priced_count == len(bom_lines)
    cost_reasons = []
    if not price_complete:
        cost_reasons.append("缺少完整、有效的价格事实")
    if not currency_complete:
        cost_reasons.append("价格币种不完整")
    elif len(currencies) != 1:
        cost_reasons.append("存在多个币种，不能在缺少明确换算依据时合计成本")
    if not units["complete"]:
        cost_reasons.append("计量单位尚未完整确认")
    if not effective["complete"]:
        cost_reasons.append("BOM/价格生效日期尚未完整确认")
    if not relations_complete or errors:
        cost_reasons.append("来源数据未通过完整校验")
    reasons = []
    if not relations_complete:
        reasons.append("BOM 数量关系或产品版本数据不完整")
    if not effective["complete"]:
        reasons.append("BOM 生效日期未完整确认或存在冲突")
    if not units["complete"]:
        reasons.append("成品及 BOM 子料计量单位未完整确认")
    if not snapshot["complete"]:
        reasons.append("库存快照时间未确认或缺少明确时区")
    if not on_hand["complete"]:
        reasons.append("部分 BOM 物料在库数量未知或无效，不能按零处理")
    if not available["complete"]:
        reasons.append("缺少完整的权威库存可用性确认，不能把在库数量当作可用库存")
    if errors:
        reasons.append("来源数据存在校验错误")
    return {
        "report_type": "enterprise_prepublication_readiness", "product": product, "revision": revision,
        "bom": {"line_count": len(bom_lines), "valid_line_count": valid_count,
                "material_count": len(expected), "relations_complete": relations_complete,
                "effective_date": effective, "unit_completeness": units},
        "inventory": {"snapshot_at": snapshot, "on_hand_coverage": on_hand,
                      "available_coverage": available},
        "price": {"priced_line_count": priced_count, "total_line_count": len(bom_lines),
                  "price_complete": price_complete, "currency_complete": currency_complete,
                  "can_calculate_cost": not cost_reasons, "reasons": cost_reasons},
        "production_analysis": {"data_ready": not reasons,
            "can_generate_deterministic_conclusion": not reasons, "can_produce": None,
            "summary": "满足生产分析的数据前置条件" if not reasons else "不可生成确定性生产结论",
            "reasons": reasons},
        "provenance": provenance or {}, "validation_errors": errors,
        "publication_executed": False,
        "limitations": ["本报告仅检查发布前数据条件，不执行发布或计算能否生产",
                        "通过检查不等于库存齐套或允许开工，仍须由已有计算服务分析已发布事实",
                        "价格缺失影响成本计算，不单独阻止库存齐套分析"],
    }


def render_enterprise_readiness_report(report: dict) -> str:
    """User-facing text from the same structured checks, never inferred facts."""
    bom, inventory, price = report["bom"], report["inventory"], report["price"]
    units = bom["unit_completeness"]
    effective = bom["effective_date"]
    snapshot = inventory["snapshot_at"]
    on_hand, available = inventory["on_hand_coverage"], inventory["available_coverage"]
    lines = [f"# {report['product']} / {report['revision']} 企业数据发布前检查", "", "## BOM", "",
        f"{'✓' if bom['relations_complete'] else '⚠'} 已校验 {bom['valid_line_count']}/{bom['line_count']} 条数量关系（{bom['material_count']} 个料号）"]
    if effective["complete"]:
        label = "业务确认" if effective["status"] == "confirmed" else "来源明确提供"
        lines.append(f"✓ 生效日期：{effective['value']}（{label}）")
    else:
        labels = {"pending_confirmation": "待确认", "conflicting": "存在冲突", "invalid": "无效"}
        lines.append(f"⚠ 生效日期{labels[effective['status']]}")
    lines.append(f"{'✓' if units['parent_unit_known'] else '⚠'} 成品单位：{units['parent_unit'] if units['parent_unit_known'] else '待确认'}")
    lines.append(f"{'✓' if units['complete'] else '⚠'} 子料单位完整性：{units['known_material_count']}/{units['total_material_count']}")
    lines.extend(["", "## 库存", ""])
    lines.append(f"✓ 库存快照时间：{snapshot['value']}" if snapshot["complete"] else "⚠ 库存快照时间待确认或无有效时区")
    lines.append(f"{'✓' if on_hand['known_material_count'] else '⚠'} {on_hand['known_material_count']} 项有有效在库数量；覆盖率 {on_hand['known_material_count']}/{on_hand['total_material_count']}（{on_hand['percentage'] or '未知'}）")
    if on_hand["unknown_material_count"]:
        lines.append(f"⚠ {on_hand['unknown_material_count']} 项在库数量未知或无效，不补零")
    if available["field_status"] == "not_provided":
        lines.append("⚠ 未提供权威可用库存字段，不使用在库数量替代")
    lines.append(f"{'✓' if available['complete'] else '⚠'} 可用库存覆盖率：{available['known_material_count']}/{available['total_material_count']}（{available['percentage'] or '未知'}）")
    lines.extend(["", "## 价格", "",
        f"{'✓' if price['can_calculate_cost'] else '⚠'} {'具备成本计算的数据条件' if price['can_calculate_cost'] else '不可生成确定成本'}（价格覆盖 {price['priced_line_count']}/{price['total_line_count']}）"])
    if price["reasons"]:
        lines.extend(["", *[f"- {reason}" for reason in price["reasons"]]])
    lines.extend(["", "## 生产分析", "", report["production_analysis"]["summary"]])
    if report["production_analysis"]["reasons"]:
        lines.extend(["", "原因：", "", *[f"- {reason}" for reason in report["production_analysis"]["reasons"]]])
    lines.extend(["", "## 范围与限制", "", *[f"- {value}" for value in report["limitations"]],
                  "- 本检查不执行暂存、迁移或发布；实际发布状态以导入批次审计为准。", "", "## 数据来源", ""])
    for kind, source in report["provenance"].items():
        label = {"bom": "BOM", "inventory": "库存"}.get(kind, kind)
        lines.append(f"- {label}：{source.get('filename', '未知')}；SHA-256：{source.get('sha256', '未知')}")
    return "\n".join(lines) + "\n"
