"""Partial enterprise analysis, not formal MRP or production approval.

Published BOM line units define demands. An Excel stock unit must independently
match before subtraction. Missing records and quantities are never zero-filled.
Only first-level published structures without subassembly BOMs are supported here;
multilevel decisions remain with the existing strict planning service.

单位口径（统一走 services.unit_system）：
- 同维度单位先规范化再比较（重量克/公斤/斤，长度毫米/厘米/米，计数片/块/个/件）；
- 计数维度内“套/台/只/箱/包”必须与需求单位完全一致，没有物料专属规则时拒绝换算；
- 跨维度（公斤 vs 片、米 vs 个）返回不可核算，不允许相减、不生成确定缺口与采购量；
- 单位无法核验时按“未知”处理，绝不把库存静默改成零。
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import logging

from services import unit_system

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
        # 中间组件判定改为一次性取回已发布父件集合，避免每行 BOM 各开一次数据库连接。
        published_parents = None
        loader = getattr(self.facts.repository, "published_bom_parent_codes", None)
        if callable(loader):
            try:
                published_parents = loader()
            except Exception:  # 批量口径不可用时回退到逐行查询
                logger.warning("Batch published-BOM lookup failed; falling back to per-row query", exc_info=True)
                published_parents = None
        for line in bom["data"]["lines"]:
            child = line["child_item"]; code = child["material_code"]
            has_children = (code in published_parents if published_parents is not None
                            else bool(self.facts.repository.list_published_bom_versions(code)))
            if has_children:
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
        # 单位口径统一解析：显式单位优先；缺失时计数成品按“片”，重量/长度成品沿用
        # 成品自身单位，绝不把重量或长度成品强制当成片。
        requested_unit, requested_unit_source = self._request_unit(requested_unit, parent_unit)
        query.update(requested_unit=requested_unit, requested_unit_source=requested_unit_source)
        if parent_unit is not None and requested_unit is not None:
            reason = unit_system.conflict_reason(requested_unit, parent_unit)
            if reason is not None:
                return self._result("invalid_query", query, None, {}, {"bom": bom["provenance"]},
                    [f"生产单位{requested_unit}与成品单位{parent_unit}无法直接比较：{reason}；未执行需求计算"])
        demand_allowed = bool(parent_unit) and (requested_unit is None
            or unit_system.compatible(requested_unit, parent_unit))
        demand_quantity = None
        if quantity is not None and demand_allowed:
            demand_quantity = Decimal(quantity)
            if (requested_unit is not None and parent_unit is not None
                    and requested_unit != parent_unit):
                converted = unit_system.convert(quantity, requested_unit, parent_unit)
                if converted is None:
                    demand_allowed = False
                    demand_quantity = None
                else:
                    demand_quantity = converted
        # 同一物料的“已核验重复观测”依据只来自来源级数量口径人工确认；
        # 未经核验的重复记录不取最大值、不求和、不任意选行。
        duplicate_basis = "source_quantity_confirmed" if review else "unverified"
        rows = []
        for code, per_product in usage.items():
            row = self._stock(code, source, verified_duplicates=review)
            # 需求按 BOM 单耗直接乘生产数量，不再要求单耗单位存在。
            row.update(name=names[code] or row["name"], unit=units[code], qty_per=format(per_product, "f"),
                required_qty=format(per_product * demand_quantity, "f") if demand_quantity is not None else None,
                bom_line_sources=paths[code])
            row["duplicate_basis"] = ("none" if row["duplicate_free"] else duplicate_basis)
            row["duplicate_verified"] = bool(not row["duplicate_free"] and review)
            # 库存口径必须同时满足：有可核验数量、有可核验单位、单位与BOM同维度可换算。
            stock_unit, stock_unit_basis = self._stock_unit(row, source)
            row["stock_unit"] = stock_unit; row["stock_unit_basis"] = stock_unit_basis
            status, reason = self._comparison_status(row, units[code], stock_unit, stock_unit_basis)
            row["comparison_status"] = status
            row["uncomputable_reason"] = reason
            row["book_difference"] = None; row["book_shortage_qty"] = None; row["suggested_purchase_qty"] = None
            row["stock_qty_in_bom_unit"] = None
            difference = None
            if status == "comparable" and row["required_qty"] is not None:
                comparable_stock = (Decimal(row["recorded_stock_qty"]) if stock_unit == units[code]
                                    else unit_system.convert(row["recorded_stock_qty"], stock_unit, units[code]))
                if comparable_stock is None:
                    # 理论上不会发生：单位检查已通过。保守退回不可核算，不猜测数量。
                    row["comparison_status"] = status = "unit_conflict"
                    row["uncomputable_reason"] = reason = unit_system.conflict_reason(stock_unit, units[code]) or "单位换算不可用"
                else:
                    row["stock_qty_in_bom_unit"] = format(comparable_stock, "f")
                    difference = comparable_stock - Decimal(row["required_qty"])
                    row["book_difference"] = format(difference, "f")
                    row["book_shortage_qty"] = format(max(-difference, Decimal("0")), "f")
                    # This is a replenishment need, not an MOQ-constrained procurement plan.
                    row["suggested_purchase_qty"] = row["book_shortage_qty"] if difference < 0 else None
            elif status == "no_stock":
                # No stock observation is not evidence of zero stock or a purchase gap.
                row["missing_stock"] = True
                row["suggested_purchase_qty"] = None
            row["actual_stock_qty"] = row["recorded_stock_qty"] if review else None
            row["shortage_qty"] = row["book_shortage_qty"] if review else None
            row["quantity_status"] = ("shortage" if review and difference is not None and difference < 0 else
                "sufficient" if review and difference is not None else "unknown")
            if stock_error:
                row["comparison_status"] = "inventory_read_failed"
            rows.append(row)
        uncomputable = [r for r in rows if r["comparison_status"] not in {"comparable", "no_stock"}]
        data = dict(calculation_scope="excel_book_observation", product=bom["data"]["parent_item"]["material_code"],
            revision=revision, requested_quantity=quantity, requested_unit=requested_unit,
            requested_unit_source=requested_unit_source, product_unit=parent_unit,
            demand_quantity=None if demand_quantity is None else format(demand_quantity, "f"),
            materials=rows,
            matched_material_count=sum(r["record_found"] for r in rows), total_material_count=len(rows),
            no_record_materials=[r["material_code"] for r in rows if not r["record_found"]],
            comparable_material_count=sum(r["comparison_status"] == "comparable" for r in rows),
            missing_stock_materials=[r["material_code"] for r in rows if r["comparison_status"] == "no_stock"],
            missing_stock_material_count=sum(r["comparison_status"] == "no_stock" for r in rows),
            unit_conflict_materials=[r["material_code"] for r in rows if r["comparison_status"] == "unit_conflict"],
            unit_unknown_materials=[r["material_code"] for r in rows if r["comparison_status"] == "unit_unknown"],
            duplicate_unverified_materials=[r["material_code"] for r in rows
                if r["comparison_status"] == "duplicate_unverified"],
            uncomputable_materials=[dict(material_code=r["material_code"], name=r.get("name"),
                status=r["comparison_status"], reason=r.get("uncomputable_reason"),
                unit=r.get("unit"), stock_unit=r.get("stock_unit"),
                required_qty=r.get("required_qty"), recorded_stock_qty=r.get("recorded_stock_qty"))
                for r in uncomputable],
            uncomputable_material_count=len(uncomputable),
            purchase_material_count=sum(r["suggested_purchase_qty"] is not None
                and Decimal(r["suggested_purchase_qty"] or "0") > 0 for r in rows),
            book_shortages=[r for r in rows if r["book_shortage_qty"] is not None and Decimal(r["book_shortage_qty"]) > 0],
            recommendations=[dict(material=r["material_code"], unit=r["unit"], suggested_purchase_qty=r["suggested_purchase_qty"],
                shortage_qty=r["book_shortage_qty"], supplier=None, purchase_cost=None, lead_time_days=None,
                reason=["按账面数量与BOM需求比对得出的补货数量，不是正式采购计划"],
                missing_fields=["supplier", "MOQ", "order_multiple", "lead_time_days", "price"]) for r in rows if r["suggested_purchase_qty"] is not None],
            inventory_sufficient_for_requested=None, theoretical_max_quantity=None, can_start_production=None,
            formal_production_approved=False, source_summary=self._summary(source))
        data["quantity_review"] = review
        data["stock_read_failed"] = stock_error
        data["partial_comparison"] = bool(uncomputable) and any(r["comparison_status"] == "comparable" for r in rows)
        if stock_error:
            data["matched_material_count"] = None
            data["no_record_materials"] = []
            for row in rows:
                row["record_found"] = None
                row["inventory_status"] = "unknown"
        if review and quantity is not None:
            # 有缺口或库存未查询到即不齐套；单位/重复记录无法核验时只给“待核验”，
            # 既不说满足，也不按需求数量虚构缺口。
            if data["book_shortages"] or data["missing_stock_material_count"]:
                sufficient, check_status = False, "insufficient"
            elif uncomputable:
                sufficient, check_status = None, "unverified"
            else:
                sufficient, check_status = True, "sufficient"
            data.update(inventory_sufficient_for_requested=sufficient,
                quantity_check_status=check_status,
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
    def _request_unit(requested, parent_unit):
        """统一“用户输入单位”的解析口径，并保留来源标记以便追溯。"""
        if requested is not None:
            parsed = unit_system.spec(requested)
            return (parsed.raw if parsed else requested), "explicit"
        if parent_unit is None:
            return None, "missing"
        if unit_system.dimension(parent_unit) == unit_system.COUNT:
            return unit_system.DEFAULT_COUNT_UNIT, "default_piece"
        return parent_unit, "inherited_product_unit"

    @staticmethod
    def _stock_unit(row, source):
        """库存计量单位及其核验来源。

        行内单位一致 → recorded；行内单位互相冲突 → conflicting（不猜测）；
        行内全部缺失时才回退到行级/来源级人工单位确认，绝不把已明确标记为重量
        或长度的记录改写成计数单位。
        """
        recorded = {detail.get("inventory_unit") for detail in (row.get("details") or [])
                    if detail.get("inventory_unit")}
        if len(recorded) == 1:
            return next(iter(recorded)), "recorded"
        if len(recorded) > 1:
            return None, "conflicting"
        for detail in row.get("details") or []:
            confirmation = ((detail.get("provenance") or {}).get("unit_confirmation") or {})
            if confirmation.get("unit"):
                return confirmation["unit"], "source_confirmed"
        confirmation = source.get("unit_confirmation") or {}
        if confirmation.get("unit"):
            return confirmation["unit"], "source_confirmed"
        return None, "missing"

    @staticmethod
    def _comparison_status(row, bom_unit, stock_unit, stock_unit_basis="missing"):
        """返回 (状态, 不可核算原因)；只有 comparable 才允许确定性缺口/采购。"""
        if not row["record_found"]:
            return "no_stock", None
        if not row["duplicate_free"] and not row.get("duplicate_verified"):
            return "duplicate_unverified", "同一料号存在未核验的重复库存记录，未取最大值、未求和"
        if row["recorded_stock_qty"] is None:
            return "unverified_quantity", "库存记录缺少可核验数量"
        if stock_unit is None:
            reason = ("库存记录内计量单位不一致" if stock_unit_basis == "conflicting"
                      else "库存记录缺少可核验的计量单位")
            return "unit_unknown", reason
        reason = unit_system.conflict_reason(stock_unit, bom_unit)
        if reason is not None:
            return "unit_conflict", reason
        return "comparable", None

    @staticmethod
    def _stock(code, source, *, verified_duplicates=False):
        """单个料号的账面库存。

        同一批次+库位可能有多行（重复导出或同批冲销）。只有来源级数量口径已
        人工确认（verified_duplicates）时，才按该组最大值合并；否则重复记录保持
        “待核验”，不取最大值、不求和、不任意选行，也不把库存记成零。
        """
        details = source["records"].get(code, [])
        valid = bool(details) and all(r["recorded_stock_qty"] is not None for r in details)
        # 重复观测包含两种情况：导入已标记重复，或同批次+库位出现多行。
        # 两者在未取得数量口径确认前都属于“待核验重复”，不做合并。
        keys = [(r.get("batch_no"), r.get("location")) for r in details]
        repeated_keys = {key for key in keys if keys.count(key) > 1}
        duplicate_free = (not any(r["duplicate_observation"] for r in details) and not repeated_keys)
        total = None
        if valid and (duplicate_free or verified_duplicates):
            groups: dict[tuple, Decimal] = {}
            for r in details:
                key = (r.get("batch_no"), r.get("location"))
                qty = Decimal(str(r["recorded_stock_qty"]))
                if key not in groups or qty > groups[key]:
                    groups[key] = qty
            total = sum(groups.values(), Decimal("0"))
        known_units = {r["inventory_unit"] for r in details if r.get("inventory_unit")}
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
            return ["按当前文件的人工确认实际库存口径核对；无记录保持未知，"
                    "已确认口径下同批次多条记录按最大值合并。"]
        warnings = ["仅展示主库存表账面记录，不代表当前权威可用库存；未合并其他工作表。",
            "同一批次多条记录在未取得数量口径确认时保持待核验，不取最大值、不求和；无记录不等于零库存。",
            "精确库存快照、预留及可用数量缺少权威来源，不能批准生产。"]
        if any(row.get("inventory_unit") is None for rows in source["records"].values() for row in rows):
            warnings.append("部分库存记录未自带计量单位；已按来源级单位确认或按未知单位处理，"
                            "单位不一致的物料不参与确定性缺口计算，原始记录仍可查看。")
        return warnings

    @staticmethod
    def _result(status, query, data, completeness, provenance, warnings):
        return dict(status=status, query=query, data=data, completeness=completeness, provenance=provenance, warnings=warnings)
