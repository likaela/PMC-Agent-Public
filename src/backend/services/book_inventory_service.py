"""Partial enterprise analysis, not formal MRP or production approval.

Published BOM line units define demands. An Excel stock unit must independently
match before subtraction. Missing records and quantities are never zero-filled.
Only first-level published structures without subassembly BOMs are supported here;
multilevel decisions remain with the existing strict planning service.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


class BookInventoryService:
    def __init__(self, facts, observations):
        self.facts = facts
        self.observations = observations

    def inventory(self, code: str) -> dict:
        item = self.facts.repository.resolve_item(code)
        source = self.observations.load()
        # Raw exact codes are observable even before standard Item publication.
        if item and item.get("ambiguous"):
            return self._result("item_not_found", {"material_code": code}, None, {}, None, ["物料身份存在冲突，请先核验"])
        canonical = item["material_code"] if item else code
        if not item and canonical not in source["records"]:
            return self._result("item_not_found", {"material_code": code}, None, {}, None, ["未找到该物料编码，请确认编码是否正确"])
        row = self._stock(canonical, source)
        return self._result("ok", {"material_code": canonical}, dict(calculation_scope="excel_book_observation", materials=[row],
            product=canonical, record_found=row["record_found"], source_summary=self._summary(source)),
            {"recorded_stock_qty": row["recorded_stock_qty"] is not None, "available_qty": False},
            {"inventory": row["stock_sources"]}, self._warnings(source))

    def analyze(self, product: str, revision: str | None = None, quantity: int | None = None, requested_unit: str | None = None) -> dict:
        query = dict(product=product, revision=revision, requested_quantity=quantity, requested_unit=requested_unit)
        if quantity is not None and (isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0):
            return self._result("invalid_query", query, None, {}, None, ["生产数量必须为正整数"])
        identity = self.facts.repository.resolve_item(product)
        if identity is None or identity.get("ambiguous"):
            return self._result("item_not_found", query, None, {}, None, ["未找到精确产品或产品别名，未猜测BOM"])
        product = str(identity["material_code"])
        query["resolved_parent_material_code"] = product
        versions = self.facts.repository.list_published_bom_versions(product)
        matches = [v for v in versions if revision is None or v["version"] == revision]
        if len(matches) != 1:
            return self._result("data_unavailable", query, None, {}, None, ["请确认唯一的已发布BOM版本；未选择最新版本"])
        revision = str(matches[0]["version"])
        bom = self.facts.query_bom(product, revision)
        if bom["status"] != "ok":
            return bom
        units, usage, names, paths = {}, defaultdict(lambda: Decimal("0")), {}, defaultdict(list)
        for line in bom["data"]["lines"]:
            child = line["child_item"]; code = child["material_code"]
            if self.facts.repository.list_published_bom_versions(code):
                return self._result("data_unavailable", query, {"bom_structure": bom["data"]}, {"demand": False},
                    {"bom": bom["provenance"]}, ["存在中间组件，请使用严格多层BOM规划；未把子组件当叶子物料"])
            unit = line.get("unit")
            if code in units and units[code] != unit:
                return self._result("data_unavailable", query, None, {}, {"bom": bom["provenance"]}, ["同物料BOM行单位冲突，未合并数量"])
            units[code] = unit; usage[code] += Decimal(line["qty_per"])
            names[code] = child.get("name"); paths[code].append(line["provenance"])
        parent_unit = bom["data"]["parent_item"].get("unit")
        # Demand is owned by BOM, not by successful Excel I/O. Preserve it even
        # when a missing/corrupt workbook prevents the stock comparison.
        stock_error = False
        try:
            source = self.observations.load()
        except Exception:
            logger.exception("Main inventory read failed after published BOM demand resolution")
            stock_error = True
            source = dict(records={}, source_file=None, content_sha256=None, sheet_name=None,
                record_count=0, unique_material_count=0, duplicate_observation_count=0,
                row_errors=[], excluded_sheets=[], unit_confirmation=None, quantity_confirmation=None)
        confirmation = source.get("quantity_confirmation")
        review = bool(confirmation and any(s["product"] == product and s["revision"] == revision
            for s in confirmation["product_scope"]) and parent_unit == confirmation["unit"])
        # Oral counts are accepted only for a source-scoped, confirmed countable
        # finished product. This is not a kg/pcs/set conversion rule.
        if parent_unit == "片" and requested_unit in ("个", "件"):
            requested_unit = parent_unit
        demand_allowed = bool(parent_unit) and (requested_unit is None or requested_unit == parent_unit)
        rows = []
        for code, per_product in usage.items():
            row = self._stock(code, source)
            row.update(name=names[code] or row["name"], unit=units[code], qty_per=format(per_product, "f"),
                required_qty=format(per_product * quantity, "f") if quantity is not None and units[code] and demand_allowed else None,
                bom_line_sources=paths[code])
            comparable = bool(row["required_qty"] is not None and row["inventory_unit"] == units[code] and
                row["recorded_stock_qty"] is not None and row["duplicate_free"])
            row["comparison_status"] = ("comparable" if comparable else "no_record" if not row["record_found"]
                else "pending_record_verification" if not row["duplicate_free"] or row["recorded_stock_qty"] is None
                else "pending_unit_verification")
            row["book_difference"] = None; row["book_shortage_qty"] = None; row["suggested_purchase_qty"] = None
            if comparable:
                difference = Decimal(row["recorded_stock_qty"]) - Decimal(row["required_qty"])
                row["book_difference"] = format(difference, "f")
                row["book_shortage_qty"] = format(max(-difference, Decimal("0")), "f")
                # This is a replenishment need, not an MOQ-constrained procurement plan.
                row["suggested_purchase_qty"] = row["book_shortage_qty"] if difference < 0 else None
            row["actual_stock_qty"] = row["recorded_stock_qty"] if review else None
            row["shortage_qty"] = row["book_shortage_qty"] if review else None
            row["quantity_status"] = ("shortage" if review and comparable and difference < 0 else
                "sufficient" if review and comparable else "unknown")
            if stock_error:
                row["comparison_status"] = "inventory_read_failed"
            rows.append(row)
        data = dict(calculation_scope="excel_book_observation", product=bom["data"]["parent_item"]["material_code"],
            revision=revision, requested_quantity=quantity, product_unit=parent_unit, materials=rows,
            matched_material_count=sum(r["record_found"] for r in rows), total_material_count=len(rows),
            no_record_materials=[r["material_code"] for r in rows if not r["record_found"]],
            comparable_material_count=sum(r["comparison_status"] == "comparable" for r in rows),
            book_shortages=[r for r in rows if r["book_shortage_qty"] is not None and Decimal(r["book_shortage_qty"]) > 0],
            recommendations=[dict(material=r["material_code"], unit=r["unit"], suggested_purchase_qty=r["suggested_purchase_qty"],
                shortage_qty=r["book_shortage_qty"], supplier=None, purchase_cost=None, lead_time_days=None,
                reason=["经可比单位核验的账面补货需求，不是正式采购计划"],
                missing_fields=["supplier", "MOQ", "order_multiple", "lead_time_days", "price"]) for r in rows if r["suggested_purchase_qty"] is not None],
            inventory_sufficient_for_requested=None, theoretical_max_quantity=None, can_start_production=None,
            formal_production_approved=False, source_summary=self._summary(source))
        data["quantity_review"] = review
        data["stock_read_failed"] = stock_error
        if stock_error:
            data["matched_material_count"] = None
            data["no_record_materials"] = []
            for row in rows:
                row["record_found"] = None
                row["inventory_status"] = "unknown"
        if review and quantity is not None:
            full = bool(rows) and all(r["comparison_status"] == "comparable" for r in rows)
            sufficient = False if data["book_shortages"] else True if full else None
            data.update(inventory_sufficient_for_requested=sufficient,
                quantity_check_status="insufficient" if sufficient is False else "sufficient" if sufficient is True else "incomplete",
                quantity_confirmation=confirmation)
        completeness = dict(demand=quantity is not None and all(r["required_qty"] is not None for r in rows),
            book_comparison=bool(rows) and all(r["comparison_status"] == "comparable" for r in rows),
            available_qty=False, snapshot_coherent=False, formal_production_analysis=False)
        completeness["actual_stock_comparison"] = review and completeness["book_comparison"]
        provenance = dict(bom=bom["provenance"], inventory=[dict(material_code=r["material_code"], rows=r["stock_sources"]) for r in rows if r["record_found"]],
            bom_lines=[dict(material_code=r["material_code"], rows=r["bom_line_sources"]) for r in rows])
        if review:
            provenance["quantity_confirmation"] = confirmation
        warnings = self._warnings(source, review=review)
        if stock_error:
            warnings = ["BOM需求已计算；库存文件本次读取失败，请稍后重试。"]
        return self._result("query_failed" if stock_error else "ok", {**query, "revision": revision}, data, completeness, provenance, warnings)

    @staticmethod
    def _stock(code, source):
        details = source["records"].get(code, [])
        valid = bool(details) and all(r["recorded_stock_qty"] is not None for r in details)
        # Equal observations may be duplicated or represent separate stock. Keep
        # all original rows, but do not present their sum as a verified total.
        duplicate_free = not any(r["duplicate_observation"] for r in details)
        total = sum((Decimal(r["recorded_stock_qty"]) for r in details), Decimal("0")) if valid and duplicate_free else None
        known_units = {r["inventory_unit"] for r in details}
        return dict(material_code=code, name=next((r["name"] for r in details if r["name"]), None),
            record_found=bool(details), inventory_status="recorded" if details else "no_record", record_count=len(details),
            recorded_stock_qty=None if total is None else format(total, "f"), available_qty=None, reserved_qty=None,
            inventory_unit=next(iter(known_units)) if len(known_units) == 1 else None,
            duplicate_free=duplicate_free,
            batch_count=len({r["batch_no"] for r in details if r["batch_no"]}),
            location_count=len({r["location"] for r in details if r["location"]}),
            details=details, stock_sources=[r["provenance"] for r in details])

    @staticmethod
    def _summary(source):
        return {key: source.get(key) for key in ("source_file", "content_sha256", "sheet_name", "record_count", "unique_material_count", "duplicate_observation_count", "row_errors", "excluded_sheets", "unit_confirmation", "quantity_confirmation", "review_scenario")}

    @staticmethod
    def _warnings(source, review=False):
        if review:
            return ["按当前文件的人工确认实际库存口径核对；无记录保持未知，未核验重复记录不求和。"]
        warnings = ["仅展示主库存表账面记录，不代表当前权威可用库存；未合并其他工作表。",
            "重复观察保留原始明细，未核验前不给出确定合计；无记录不等于零库存。",
            "精确库存快照、预留及可用数量缺少权威来源，不能批准生产。"]
        if any(row["inventory_unit"] is None for rows in source["records"].values() for row in rows):
            warnings.append("部分库存记录的计量单位尚未确认；仍可查询原始数量和行级来源。")
        return warnings

    @staticmethod
    def _result(status, query, data, completeness, provenance, warnings):
        return dict(status=status, query=query, data=data, completeness=completeness, provenance=provenance, warnings=warnings)
