"""已发布事实的库存理论产能查询；不修改或替代 PMC 生产决策。"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from services.fact_query_service import FactQueryService


def _source(value: object, *fields: str) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {field: value[field] for field in fields if value.get(field) is not None}


def _positive_decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result > 0 else None


def _nonnegative_decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result >= 0 else None


class ProductionFeasibilityQueryService:
    """仅计算一级已发布 BOM 直接子项的库存理论上限。

    不把在途计入当前可用量；不执行单位换算、排程、采购或开工判断。
    各子项必须具有权威 available_qty、可比单位和同批库存快照。
    """

    def __init__(self, facts: FactQueryService) -> None:
        self.facts = facts

    def query(
        self,
        product: str,
        bom_version: str,
        *,
        requested_quantity: int | None = None,
        requested_unit: str | None = None,
        as_of: str | None = None,
        effective_date: str | None = None,
    ) -> dict[str, Any]:
        if effective_date is not None:
            from services.published_planning_service import PublishedPlanningService

            return PublishedPlanningService(self.facts).query(
                product, bom_version, effective_date,
                requested_quantity=requested_quantity,
                requested_unit=requested_unit,
                as_of=as_of,
            )
        query = {
            "product": product,
            "bom_version": bom_version,
            "requested_quantity": requested_quantity,
            "requested_unit": requested_unit,
            "as_of": as_of,
        }
        empty_completeness = {
            "bom": False, "inventory": False, "units": False, "snapshot_coherent": False,
        }
        if not product.strip() or not bom_version.strip() or (
            requested_quantity is not None
            and (isinstance(requested_quantity, bool) or not isinstance(requested_quantity, int) or requested_quantity <= 0)
        ):
            return self._result("invalid_query", query, None, empty_completeness, None, ["请提供产品、准确 BOM 版本及有效生产数量"])
        if as_of is not None:
            try:
                parsed_as_of = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
                if parsed_as_of.tzinfo is None:
                    raise ValueError("timezone required")
            except ValueError:
                return self._result("invalid_query", query, None, empty_completeness, None, ["查询时点必须为带时区的 ISO 8601 时间"])
            query["as_of"] = parsed_as_of.astimezone(UTC).isoformat()
        else:
            # “当前”只允许取截至本次查询时点的已发布快照，不读取未来数据。
            query["as_of"] = datetime.now(UTC).isoformat()

        bom = self.facts.query_bom(product.strip(), bom_version.strip())
        if bom["status"] != "ok" or not isinstance(bom.get("data"), dict):
            status = (
                "invalid_query" if bom["status"] == "invalid_query"
                else "query_failed" if bom["status"] == "query_failed"
                else "data_unavailable"
            )
            warning = (
                "BOM 事实查询暂不可用，未生成推断值"
                if status == "query_failed" else "当前事实库没有对应的已发布 BOM；无法计算生产能力"
            )
            return self._result(status, query, None, empty_completeness, None, [warning])
        raw_lines = bom["data"].get("lines")
        parent_item = bom["data"].get("parent_item")
        product_unit = parent_item.get("unit") if isinstance(parent_item, dict) else None
        if not isinstance(product_unit, str) or not product_unit.strip() or (
            requested_unit and product_unit.strip() != requested_unit.strip()
        ):
            return self._result(
                "data_unavailable", query, None,
                {**empty_completeness, "bom": True},
                {"bom": _source(bom["provenance"], "source_file", "import_batch_id", "bom_version", "sheet_name")},
                ["产品计量单位缺失或与问题中的单位不一致，不能自行换算"],
            )
        if not isinstance(raw_lines, list) or not raw_lines:
            return self._result("data_unavailable", query, None, empty_completeness, {"bom": _source(bom["provenance"], "source_file", "import_batch_id", "bom_version", "sheet_name")}, ["BOM 明细为空，无法计算生产能力"])

        # 同一直接子项的多行用量先汇总；单位不同不能相加。
        usage: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        units: dict[str, str] = {}
        line_sources: dict[str, list[dict[str, object]]] = defaultdict(list)
        bad_lines: list[str] = []
        for line in raw_lines:
            if not isinstance(line, dict):
                bad_lines.append("BOM 行结构不完整")
                continue
            child = line.get("child_item")
            code = child.get("material_code") if isinstance(child, dict) else None
            unit = line.get("unit")
            qty = _positive_decimal(line.get("qty_per"))
            if not isinstance(code, str) or not code.strip() or not isinstance(unit, str) or not unit.strip() or qty is None:
                bad_lines.append(str(code or "未知物料"))
                continue
            unit = unit.strip()
            if code in units and units[code] != unit:
                bad_lines.append(code)
                continue
            usage[code] += qty
            units[code] = unit
            source = _source(line.get("provenance"), "source_file", "import_batch_id", "sheet_name", "row_number")
            if source:
                line_sources[code].append(source)
        bom_source = _source(bom["provenance"], "source_file", "import_batch_id", "bom_version", "sheet_name")
        if bad_lines:
            return self._result(
                "data_unavailable", query,
                {"max_producible_quantity": None, "materials": [], "limiting_materials": []},
                {**empty_completeness, "bom": True}, {"bom": bom_source},
                [f"BOM 用量或单位不完整，涉及：{'、'.join(sorted(set(bad_lines)))}"],
            )

        materials: list[dict[str, object]] = []
        inventory_sources: list[dict[str, object]] = []
        inbound_sources: list[dict[str, object]] = []
        warnings: list[str] = []
        incomplete: list[str] = []
        unit_complete = True
        snapshot_keys: set[tuple[object, object]] = set()
        inbound_complete = True
        for code in sorted(usage):
            item: dict[str, object] = {
                "material_code": code,
                "qty_per": format(usage[code], "f"),
                "unit": units[code],
                "available_qty": None,
                "material_capacity": None,
                "required_qty": format(usage[code] * requested_quantity, "f") if requested_quantity is not None else None,
                "shortage_qty": None,
                "is_short": None,
                "inbound_qty": None,
                "inbound_status": "data_unavailable",
                "bom_line_sources": line_sources[code],
            }
            inventory = self.facts.query_inventory(code, query["as_of"])
            if inventory["status"] != "ok" or not isinstance(inventory.get("data"), dict):
                incomplete.append(code)
                warnings.append(
                    f"{code} 的库存查询暂不可用" if inventory["status"] == "query_failed"
                    else f"{code} 没有可用的已发布库存快照"
                )
            else:
                inv_data = inventory["data"]
                inv_source = _source(inventory["provenance"], "source_file", "import_batch_id", "snapshot_at", "sheet_name")
                if inv_source:
                    inventory_sources.append({"material_code": code, **inv_source})
                    if inv_source.get("snapshot_at") and inv_source.get("import_batch_id"):
                        snapshot_keys.add((inv_source["snapshot_at"], inv_source["import_batch_id"]))
                    else:
                        incomplete.append(code)
                        warnings.append(f"{code} 的库存快照缺少时点或导入批次来源")
                else:
                    incomplete.append(code)
                    warnings.append(f"{code} 的库存快照缺少来源信息")
                item_unit = inv_data.get("canonical_item", {}).get("unit") if isinstance(inv_data.get("canonical_item"), dict) else None
                if not isinstance(item_unit, str) or not item_unit.strip() or item_unit.strip() != units[code]:
                    unit_complete = False
                    incomplete.append(code)
                    warnings.append(f"{code} 的 BOM 与库存计量单位缺失或不一致，未进行单位换算")
                available = _nonnegative_decimal(inv_data.get("available_qty"))
                if not inventory.get("completeness", {}).get("available_qty") or available is None:
                    incomplete.append(code)
                    warnings.append(f"{code} 缺少权威可用库存数量，不能以在库数量替代")
                if inventory.get("completeness", {}).get("duplicate_free") is False:
                    incomplete.append(code)
                    warnings.append(f"{code} 的库存快照存在重复明细，不能给出确定产能")
                if available is not None and item_unit == units[code] and inventory.get("completeness", {}).get("available_qty"):
                    item["available_qty"] = format(available, "f")
                    item["material_capacity"] = int(available // usage[code])
                    if requested_quantity is not None:
                        shortage = max(usage[code] * requested_quantity - available, Decimal("0"))
                        item["shortage_qty"] = format(shortage, "f")
                        item["is_short"] = shortage > 0

            try:
                inbound = self.facts.query_inbound(code, query["as_of"])
            except Exception:
                inbound = {"status": "query_failed", "data": None}
            item["inbound_status"] = inbound["status"]
            if inbound["status"] == "ok" and isinstance(inbound.get("data"), dict):
                if inbound.get("completeness", {}).get("in_transit_qty"):
                    inbound_qty = _nonnegative_decimal(inbound["data"].get("in_transit_qty"))
                    item["inbound_qty"] = format(inbound_qty, "f") if inbound_qty is not None else None
                inbound_source = _source(inbound["provenance"], "source_file", "import_batch_id", "observed_at")
                if inbound_source:
                    inbound_sources.append({"material_code": code, **inbound_source})
                if item["inbound_qty"] is None:
                    inbound_complete = False
            else:
                inbound_complete = False
            materials.append(item)

        coherent = len(snapshot_keys) == 1 and len(inventory_sources) == len(usage)
        if not coherent:
            warnings.append("物料库存未来自同一批次、同一时点的快照，不能汇总为确定产能")
        if not inbound_complete:
            warnings.append("部分物料没有完整的已发布在途事实；在途未知，不影响当前库存产能计算")
        complete = not incomplete and unit_complete and coherent
        maximum = min(int(item["material_capacity"]) for item in materials) if complete else None
        limiting = [item for item in materials if item["material_capacity"] == maximum] if maximum is not None else []
        data = {
            "calculation_scope": "published_first_level_bom_current_available_inventory",
            "max_producible_quantity": maximum,
            "requested_quantity": requested_quantity,
            "product_unit": product_unit.strip(),
            "inventory_sufficient_for_requested": (maximum >= requested_quantity) if maximum is not None and requested_quantity is not None else None,
            "materials": materials,
            "limiting_materials": limiting,
            "unavailable_materials": sorted(set(incomplete)),
        }
        return self._result(
            "ok" if complete else "data_unavailable", query, data,
            {"bom": True, "inventory": not incomplete, "units": unit_complete, "snapshot_coherent": coherent, "inbound": inbound_complete},
            {"bom": bom_source, "inventory": inventory_sources, "inbound": inbound_sources},
            warnings + ["在途事实仅作单独列示，未计入当前可用库存；结果不等同于开工决策"],
        )

    @staticmethod
    def _result(
        status: str,
        query: dict[str, object],
        data: dict[str, object] | None,
        completeness: dict[str, bool],
        provenance: dict[str, object] | None,
        warnings: list[str],
    ) -> dict[str, object]:
        return {
            "status": status,
            "query": query,
            "data": data,
            "completeness": completeness,
            "provenance": provenance,
            "warnings": warnings,
        }


__all__ = ["ProductionFeasibilityQueryService"]
