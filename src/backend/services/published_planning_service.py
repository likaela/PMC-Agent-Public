"""v1.10 已发布事实的多层 BOM、齐套与理论产能计算。"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from services.fact_query_service import FactQueryService


def _decimal(value: object, *, positive: bool = False) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite() or number < 0 or (positive and number == 0):
        return None
    return number


def _text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


class _Unavailable(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PublishedPlanningService:
    """只读 published facts；不接入 Demo、LangGraph 或采购决策。"""

    def __init__(self, facts: FactQueryService, *, max_depth: int = 16) -> None:
        if max_depth < 1:
            raise ValueError("BOM 最大递归深度必须大于 0")
        self.facts = facts
        self.repository = facts.repository
        self.max_depth = max_depth

    def query(
        self,
        product: str,
        revision: str,
        effective_date: str,
        *,
        requested_quantity: int | None = None,
        requested_unit: str | None = None,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        query: dict[str, object] = {
            "product": product, "revision": revision, "effective_date": effective_date,
            "requested_quantity": requested_quantity, "requested_unit": requested_unit,
            "as_of": as_of,
        }
        target_date = _date(effective_date)
        if (
            not isinstance(product, str) or not product.strip()
            or not isinstance(revision, str) or not revision.strip()
            or not isinstance(effective_date, str) or target_date is None
            or effective_date != target_date.isoformat()
            or requested_quantity is not None and (
                isinstance(requested_quantity, bool)
                or not isinstance(requested_quantity, int)
                or requested_quantity <= 0
            )
        ):
            return self._result("invalid_query", query, None, {}, None, ["请明确产品、BOM 版本、生效日期及有效数量"])
        if as_of is not None:
            try:
                stock_time = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
                if stock_time.tzinfo is None:
                    raise ValueError("timezone required")
            except (AttributeError, ValueError):
                return self._result("invalid_query", query, None, {}, None, ["库存查询时点必须是带时区的 ISO 8601 时间"])
            stock_time = stock_time.astimezone(UTC)
        else:
            stock_time = datetime.now(UTC)
        query["as_of"] = stock_time.isoformat()

        bom_sources: list[dict[str, object]] = []
        conversion_sources: list[dict[str, object]] = []
        line_paths: dict[str, list[list[dict[str, object]]]] = defaultdict(list)
        usage: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        units: dict[str, str] = {}
        item_cache: dict[str, dict[str, object]] = {}
        version_cache: dict[str, list[dict[str, object]]] = {}
        conversion_cache: dict[str, list[dict[str, object]]] = {}

        def item(code: str) -> dict[str, object]:
            if code not in item_cache:
                found = self.repository.resolve_item(code)
                if found is None or found.get("ambiguous"):
                    raise _Unavailable("item_identity_unavailable", f"物料 {code} 无法精确关联标准 item")
                item_cache[code] = found
            return item_cache[code]

        def versions(code: str) -> list[dict[str, object]]:
            if code not in version_cache:
                version_cache[code] = self.repository.list_published_bom_versions(code)
            return version_cache[code]

        def active(row: dict[str, object]) -> bool:
            start = _date(row.get("effective_from"))
            end = _date(row.get("effective_to")) if row.get("effective_to") is not None else None
            if start is None or row.get("effective_to") is not None and end is None or end is not None and end < start:
                raise _Unavailable("invalid_effective_period", "已发布 BOM 或换算关系的生效期无效")
            return start <= target_date and (end is None or target_date <= end)

        def choose_version(code: str, *, root: bool = False) -> str | None:
            candidates = versions(code)
            if not candidates:
                if root:
                    raise _Unavailable("bom_not_found", f"{code} 没有已发布 BOM")
                return None
            matching = [row for row in candidates if active(row)]
            if root:
                matching = [row for row in matching if row["version"] == revision]
            if not matching:
                raise _Unavailable("bom_version_unavailable", f"{code} 在 {target_date} 没有符合条件的已发布 BOM 版本")
            if len(matching) != 1:
                raise _Unavailable("bom_version_conflict", f"{code} 在 {target_date} 有多个有效 BOM 版本，不能自动选择最新版本")
            return str(matching[0]["version"])

        def factor(code: str, from_unit: str, to_unit: str) -> Decimal:
            if from_unit == to_unit:
                return Decimal("1")
            canonical = item(code)
            item_id = str(canonical["item_id"])
            if item_id not in conversion_cache:
                conversion_cache[item_id] = self.repository.list_unit_conversions(item_id)
            matches: list[tuple[dict[str, object], bool]] = []
            for row in conversion_cache[item_id]:
                if not active(row):
                    continue
                if row["from_unit"] == from_unit and row["to_unit"] == to_unit:
                    matches.append((row, False))
                elif row["from_unit"] == to_unit and row["to_unit"] == from_unit:
                    matches.append((row, True))
            if len(matches) != 1:
                reason = "缺少" if not matches else "存在多个冲突的"
                raise _Unavailable("unit_conversion_unavailable", f"{code} 的 {from_unit}→{to_unit} {reason}已发布显式单位换算关系")
            row, reverse = matches[0]
            configured = _decimal(row["factor"], positive=True)
            if configured is None:
                raise _Unavailable("unit_conversion_unavailable", f"{code} 的单位换算系数无效")
            conversion_sources.append({
                "material_code": code, "from_unit": from_unit, "to_unit": to_unit,
                "factor": _text(Decimal("1") / configured if reverse else configured),
                "source_file": row["source_file"], "import_batch_id": row["import_batch_id"],
                "content_sha256": row["content_sha256"],
                "sheet_name": row["sheet_name"], "row_number": row["row_number"],
            })
            return Decimal("1") / configured if reverse else configured

        def walk(code: str, amount: Decimal, depth: int, stack: tuple[str, ...], path: list[dict[str, object]], *, root: bool = False) -> None:
            canonical = str(item(code)["material_code"])
            if canonical in stack:
                raise _Unavailable("bom_cycle", "BOM 环路：" + " → ".join((*stack, canonical)))
            selected = choose_version(canonical, root=root)
            if selected is None:
                leaf_unit = item(canonical).get("unit")
                if not isinstance(leaf_unit, str) or not leaf_unit.strip():
                    raise _Unavailable("unit_unavailable", f"{canonical} 缺少标准计量单位")
                usage[canonical] += amount
                units[canonical] = leaf_unit
                line_paths[canonical].append(path)
                return
            if depth >= self.max_depth:
                raise _Unavailable("bom_depth_exceeded", f"BOM 展开超过最大深度 {self.max_depth}：{canonical}")
            bom = self.facts.query_bom(canonical, selected)
            if bom["status"] != "ok" or not isinstance(bom.get("data"), dict):
                raise _Unavailable("bom_data_unavailable", f"{canonical}/{selected} 已发布 BOM 明细不可用")
            source = bom.get("provenance")
            if not isinstance(source, dict) or not source.get("import_batch_id"):
                raise _Unavailable("bom_provenance_unavailable", f"{canonical}/{selected} 缺少 BOM 来源")
            bom_sources.append({"parent_material_code": canonical, "revision": selected, **{
                key: source[key] for key in ("source_file", "content_sha256", "import_batch_id", "sheet_name", "bom_version") if key in source
            }})
            lines = bom["data"].get("lines")
            if not isinstance(lines, list) or not lines:
                raise _Unavailable("bom_data_unavailable", f"{canonical}/{selected} BOM 明细为空")
            for line in lines:
                if not isinstance(line, dict) or not isinstance(line.get("child_item"), dict):
                    raise _Unavailable("bom_data_unavailable", f"{canonical}/{selected} BOM 行结构不完整")
                raw_child = line["child_item"].get("material_code")
                qty = _decimal(line.get("qty_per"), positive=True)
                line_unit = line.get("unit")
                if not isinstance(raw_child, str) or qty is None or not isinstance(line_unit, str) or not line_unit.strip():
                    raise _Unavailable("bom_data_unavailable", f"{canonical}/{selected} BOM 用量或单位无效")
                child = item(raw_child)
                child_code = str(child["material_code"])
                child_unit = child.get("unit")
                if not isinstance(child_unit, str) or not child_unit.strip():
                    raise _Unavailable("unit_unavailable", f"{child_code} 缺少标准计量单位")
                line_source = line.get("provenance")
                if not isinstance(line_source, dict) or not all(line_source.get(key) is not None for key in ("source_file", "import_batch_id", "sheet_name", "row_number")):
                    raise _Unavailable("bom_provenance_unavailable", f"{canonical}/{selected} BOM 行缺少来源定位")
                multiplier = factor(child_code, line_unit.strip(), child_unit.strip())
                edge = {
                    "parent_material_code": canonical, "revision": selected, "child_material_code": child_code,
                    "qty_per": _text(qty), "line_unit": line_unit.strip(), "child_unit": child_unit.strip(),
                    "conversion_factor": _text(multiplier),
                    "provenance": {key: line_source[key] for key in ("source_file", "content_sha256", "import_batch_id", "sheet_name", "row_number")},
                }
                walk(child_code, amount * qty * multiplier, depth + 1, (*stack, canonical), [*path, edge])

        try:
            root_item = item(product.strip())
            root_code = str(root_item["material_code"])
            root_unit = root_item.get("unit")
            if not isinstance(root_unit, str) or not root_unit.strip():
                raise _Unavailable("unit_unavailable", f"{root_code} 缺少产品计量单位")
            demand = Decimal(requested_quantity) if requested_quantity is not None else None
            if demand is not None and requested_unit and requested_unit.strip() != root_unit:
                demand *= factor(root_code, requested_unit.strip(), root_unit)
            walk(root_code, Decimal("1"), 0, (), [], root=True)
        except _Unavailable as error:
            return self._result("data_unavailable", query, {"issue_code": error.code}, {"bom": False},
                                {"bom": bom_sources, "unit_conversions": conversion_sources}, [str(error)])

        materials: list[dict[str, object]] = []
        inventory_sources: list[dict[str, object]] = []
        missing: list[str] = []
        snapshot_keys: set[tuple[object, object]] = set()
        for code in sorted(usage):
            required = usage[code] * demand if demand is not None else None
            material: dict[str, object] = {
                "material_code": code, "unit": units[code], "required_per_product": _text(usage[code]),
                "required_qty": _text(required), "available_qty": None, "shortage_qty": None,
                "material_capacity": None, "is_ready": None, "bom_paths": line_paths[code],
            }
            try:
                inventory = self.facts.query_inventory(code, query["as_of"])
            except Exception:
                inventory = {"status": "query_failed", "data": None, "completeness": {}, "provenance": None}
            if inventory.get("status") != "ok" or not isinstance(inventory.get("data"), dict):
                missing.append(code)
            else:
                inv_data = inventory["data"]
                inv_unit = inv_data.get("canonical_item", {}).get("unit") if isinstance(inv_data.get("canonical_item"), dict) else None
                available = _decimal(inv_data.get("available_qty"))
                source = inventory.get("provenance")
                complete = inventory.get("completeness", {})
                if isinstance(source, dict) and source.get("snapshot_at") and source.get("import_batch_id"):
                    snapshot_keys.add((source["snapshot_at"], source["import_batch_id"]))
                    inventory_sources.append({"material_code": code, **{
                        key: source[key] for key in ("source_file", "content_sha256", "import_batch_id", "snapshot_at", "sheet_name") if key in source
                    }})
                else:
                    missing.append(code)
                if inv_unit != units[code] or available is None or not complete.get("available_qty") or complete.get("duplicate_free") is False:
                    missing.append(code)
                elif isinstance(source, dict) and source.get("snapshot_at") and source.get("import_batch_id"):
                    material["available_qty"] = _text(available)
                    material["material_capacity"] = int(available // usage[code])
                    if required is not None:
                        shortage = max(required - available, Decimal("0"))
                        material["shortage_qty"] = _text(shortage)
                        material["is_ready"] = shortage == 0
            materials.append(material)

        coherent = len(snapshot_keys) == 1 and len(inventory_sources) == len(materials)
        if not coherent:
            missing.extend(code for code in usage if code not in missing)
        complete = not missing and coherent
        capacities = [int(material["material_capacity"]) for material in materials if material["material_capacity"] is not None]
        maximum = min(capacities) if complete else None
        limiting = [material for material in materials if material["material_capacity"] == maximum] if maximum is not None else []
        ready_count = sum(material["is_ready"] is True for material in materials) if complete and demand is not None else None
        type_rate = Decimal(ready_count) / Decimal(len(materials)) if ready_count is not None else None
        same_unit = len(set(units.values())) == 1
        quantity_rate: Decimal | None = None
        if complete and demand is not None and same_unit:
            total_required = sum((usage[str(material["material_code"])] * demand for material in materials), Decimal("0"))
            total_satisfied = sum((min(usage[str(material["material_code"])] * demand, Decimal(str(material["available_qty"]))) for material in materials), Decimal("0"))
            quantity_rate = total_satisfied / total_required if total_required else None
        warnings: list[str] = []
        if missing:
            warnings.append("缺少完整权威可用库存、可比单位或同批同一时点库存：" + "、".join(sorted(set(missing))))
        if complete and demand is not None and not same_unit:
            warnings.append("叶子物料计量单位不同，不能跨单位相加计算数量满足率")
        data = {
            "calculation_scope": "published_multilevel_bom_available_inventory",
            "product": root_code, "revision": revision, "product_unit": root_unit,
            "requested_quantity": requested_quantity, "requested_quantity_in_product_unit": _text(demand),
            "theoretical_max_quantity": maximum, "max_producible_quantity": maximum,
            "inventory_sufficient_for_requested": maximum >= demand if maximum is not None and demand is not None else None,
            "material_type_ready_rate": _text(type_rate), "quantity_fulfillment_rate": _text(quantity_rate),
            "ready_material_count": ready_count, "total_material_count": len(materials),
            "materials": materials, "limiting_materials": limiting,
            "unavailable_materials": sorted(set(missing)),
        }
        return self._result(
            "ok" if complete else "data_unavailable", query, data,
            {"bom": True, "inventory": complete, "units": True, "snapshot_coherent": coherent,
             "material_type_ready_rate": type_rate is not None, "quantity_fulfillment_rate": quantity_rate is not None},
            {"bom": bom_sources, "inventory": inventory_sources, "unit_conversions": conversion_sources}, warnings,
        )

    @staticmethod
    def _result(status: str, query: dict[str, object], data: dict[str, object] | None,
                completeness: dict[str, bool], provenance: dict[str, object] | None,
                warnings: list[str]) -> dict[str, Any]:
        return {"status": status, "query": query, "data": data,
                "completeness": completeness, "provenance": provenance, "warnings": warnings}


__all__ = ["PublishedPlanningService"]
