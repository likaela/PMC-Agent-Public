"""已发布事实的确定性查询与公开结果组装。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fact_layer.query_models import FactQueryResult
from repositories.fact_query_repository import FactQueryRepository


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _iso_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _empty_result(
    status: str,
    query: dict[str, Any],
    warning: str,
    *,
    reason: str | None = None,
) -> FactQueryResult:
    return {
        "status": status,
        "query": query,
        "data": {"reason": reason} if reason else None,
        "completeness": {},
        "provenance": None,
        "warnings": [warning],
    }


class FactQueryService:
    """只基于 published facts 生成结构化结果，不生成业务结论。"""

    def __init__(self, repository: FactQueryRepository) -> None:
        self.repository = repository

    @staticmethod
    def _parse_as_of(as_of: str | datetime | None) -> tuple[datetime | None, str | None]:
        if as_of is None:
            return None, None
        if isinstance(as_of, datetime):
            parsed = as_of
        else:
            try:
                parsed = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return None, "as_of 必须是 ISO 8601 时间"
        if parsed.tzinfo is None:
            return None, "as_of 必须包含明确时区"
        return parsed.astimezone(UTC), None

    def _resolve_item(
        self,
        requested_code: str,
        query: dict[str, Any],
    ) -> tuple[dict[str, object] | None, FactQueryResult | None]:
        normalized = requested_code.strip()
        if not normalized:
            return None, _empty_result(
                "invalid_query", query, "material_code 不能为空"
            )
        resolved = self.repository.resolve_item(normalized)
        if resolved is None:
            return None, _empty_result(
                "item_not_found",
                query,
                "未找到精确匹配的标准 item 或 item alias",
            )
        if resolved.get("ambiguous"):
            return None, _empty_result(
                "item_alias_ambiguous",
                query,
                "该 alias 对应多个标准 item，需要先治理 item_alias",
            )
        return resolved, None

    def query_inventory(
        self,
        material_code: str,
        as_of: str | datetime | None = None,
    ) -> FactQueryResult:
        query = {"material_code": material_code, "as_of": _iso_text(as_of)}
        requested_time, time_error = self._parse_as_of(as_of)
        if time_error:
            return _empty_result("invalid_query", query, time_error)
        item, error_result = self._resolve_item(material_code, query)
        if error_result:
            return error_result
        assert item is not None
        payload = self.repository.get_inventory_snapshot(str(item["item_id"]), requested_time)
        if payload is None:
            reason = (
                "指定时点及之前没有已发布库存快照"
                if requested_time
                else "当前事实层没有该物料的已发布库存快照"
            )
            return _empty_result("data_unavailable", query, reason, reason=reason)

        facts = list(payload["facts"])
        on_hand_total = sum(
            (fact["on_hand_qty"] for fact in facts), Decimal("0")
        )
        available_complete = bool(facts) and all(
            fact["available_qty"] is not None for fact in facts
        )
        reserved_complete = bool(facts) and all(
            fact["reserved_qty"] is not None for fact in facts
        )
        available_total = (
            sum((fact["available_qty"] for fact in facts), Decimal("0"))
            if available_complete
            else None
        )
        reserved_total = (
            sum((fact["reserved_qty"] for fact in facts), Decimal("0"))
            if reserved_complete
            else None
        )
        duplicate_free = not any(fact["is_duplicate"] for fact in facts)
        warnings: list[str] = []
        if not available_complete:
            warnings.append("可用库存字段缺少完整权威来源，返回 unknown")
        if not reserved_complete:
            warnings.append("预留库存字段缺少完整权威来源，返回 unknown")
        if not duplicate_free:
            warnings.append("来源快照包含已标记的重复明细，事实未在导入阶段删除或覆盖")
        snapshot = dict(payload["snapshot"])
        return {
            "status": "ok",
            "query": {
                **query,
                "resolved_material_code": item["material_code"],
                "resolved_by": item["matched_by"],
            },
            "data": {
                "canonical_item": self._public_item(item),
                "query_snapshot_at": _iso_text(snapshot["snapshot_at"]),
                "on_hand_qty": _decimal_text(on_hand_total),
                "available_qty": _decimal_text(available_total),
                "reserved_qty": _decimal_text(reserved_total),
                "batch_count": len(
                    {str(fact["batch_no"]) for fact in facts if fact["batch_no"] is not None}
                ),
                "location_count": len(
                    {str(fact["location"]) for fact in facts if fact["location"] is not None}
                ),
                "fact_row_count": len(facts),
                "data_complete": available_complete and reserved_complete and duplicate_free,
            },
            "completeness": {
                "on_hand_qty": True,
                "available_qty": available_complete,
                "reserved_qty": reserved_complete,
                "batch_no": all(fact["batch_no"] is not None for fact in facts),
                "location": all(fact["location"] is not None for fact in facts),
                "duplicate_free": duplicate_free,
            },
            "provenance": self._inventory_provenance(snapshot),
            "warnings": warnings,
        }

    def query_inventory_batches(
        self,
        material_code: str,
        as_of: str | datetime | None = None,
    ) -> FactQueryResult:
        query = {"material_code": material_code, "as_of": _iso_text(as_of)}
        requested_time, time_error = self._parse_as_of(as_of)
        if time_error:
            return _empty_result("invalid_query", query, time_error)
        item, error_result = self._resolve_item(material_code, query)
        if error_result:
            return error_result
        assert item is not None
        payload = self.repository.get_inventory_snapshot(str(item["item_id"]), requested_time)
        if payload is None:
            reason = (
                "指定时点及之前没有已发布库存快照"
                if requested_time
                else "当前事实层没有该物料的已发布库存快照"
            )
            return _empty_result("data_unavailable", query, reason, reason=reason)
        facts = list(payload["facts"])
        details = [
            {
                "batch_no": fact["batch_no"],
                "location": fact["location"],
                "on_hand_qty": _decimal_text(fact["on_hand_qty"]),
                "received_at": _iso_text(fact["received_at"]),
                "expiry_date": _iso_text(fact["expiry_date"]),
                "quality_status": fact["quality_status"],
                "source_row_number": int(fact["source_row_number"]),
            }
            for fact in facts
        ]
        received_complete = all(fact["received_at"] is not None for fact in facts)
        expiry_complete = all(fact["expiry_date"] is not None for fact in facts)
        quality_complete = all(fact["quality_status"] is not None for fact in facts)
        duplicate_free = not any(fact["is_duplicate"] for fact in facts)
        warnings: list[str] = []
        if not received_complete:
            warnings.append("收货时间缺少权威来源，不能生成 FIFO 建议")
        if not expiry_complete:
            warnings.append("有效期缺少权威来源，不能生成临期或 FIFO 建议")
        if not quality_complete:
            warnings.append("质量状态字段不完整")
        if not duplicate_free:
            warnings.append("来源快照包含已标记的重复明细")
        snapshot = dict(payload["snapshot"])
        return {
            "status": "ok",
            "query": {
                **query,
                "resolved_material_code": item["material_code"],
                "resolved_by": item["matched_by"],
            },
            "data": {
                "canonical_item": self._public_item(item),
                "query_snapshot_at": _iso_text(snapshot["snapshot_at"]),
                "batches": details,
            },
            "completeness": {
                "batch_no": all(fact["batch_no"] is not None for fact in facts),
                "location": all(fact["location"] is not None for fact in facts),
                "on_hand_qty": True,
                "received_at": received_complete,
                "expiry_date": expiry_complete,
                "quality_status": quality_complete,
                "duplicate_free": duplicate_free,
            },
            "provenance": self._inventory_provenance(snapshot),
            "warnings": warnings,
        }

    def query_bom(self, parent_material_code: str, revision: str) -> FactQueryResult:
        query = {
            "parent_material_code": parent_material_code,
            "revision": revision,
        }
        requested_parent = parent_material_code.strip()
        requested_revision = revision.strip()
        if not requested_parent or not requested_revision:
            return _empty_result(
                "invalid_query", query, "parent_material_code 和 revision 均不能为空"
            )
        item = self.repository.resolve_item(requested_parent)
        if item is not None and item.get("ambiguous"):
            return _empty_result(
                "item_alias_ambiguous",
                query,
                "该 alias 对应多个标准 item，需要先治理 item_alias",
            )
        if item is None:
            return _empty_result(
                "item_not_found",
                query,
                "未找到精确匹配的标准 item 或 item alias",
            )
        canonical_parent = str(item["material_code"])
        warnings: list[str] = []
        payload = self.repository.get_published_bom(canonical_parent, requested_revision)
        if payload is None:
            reason = "该父件与版本没有已发布 BOM 事实"
            return _empty_result("data_unavailable", query, reason, reason=reason)
        header = dict(payload["header"])
        lines: list[dict[str, Any]] = []
        prices_complete = True
        units_complete = bool(payload["lines"])
        for source_line in payload["lines"]:
            line = dict(source_line)
            price_available = line["tax_included_unit_price"] is not None
            prices_complete = prices_complete and price_available
            units_complete = units_complete and line["unit"] is not None
            lines.append(
                {
                    "bom_line_id": str(line["bom_line_id"]),
                    "child_item": {
                        "item_id": str(line["item_id"]),
                        "material_code": str(line["material_code"]),
                        "name": line["name"],
                        "specification": line["specification"],
                        "unit": line.get("item_unit"),
                    },
                    "qty_per": _decimal_text(line["quantity"]),
                    "unit": line["unit"],
                    "completeness": {"unit": line["unit"] is not None, "price": price_available},
                    "price": (
                        {
                            "tax_included_unit_price": _decimal_text(
                                line["tax_included_unit_price"]
                            ),
                            "currency": line["currency"],
                            "effective_from": _iso_text(line["effective_from"]),
                        }
                        if price_available
                        else None
                    ),
                    "provenance": {
                        "import_batch_id": str(line["import_batch_id"]),
                        "source_file_id": str(line["source_file_id"]),
                        "source_file": str(line["source_file"]),
                        "content_sha256": str(line["content_sha256"]),
                        "sheet_name": str(line["source_sheet"]),
                        "row_number": int(line["source_row_number"]),
                    },
                }
            )
        if not prices_complete:
            warnings.append("price_missing：部分 BOM 行没有已发布价格事实，不能据此虚构成本")
        if header["effective_from"] is None:
            warnings.append("effective_date_missing：BOM生效日期未知，仅可查询结构，不能确认当前有效或用于生产计算")
        parent_unit = header.get("parent_unit", item.get("unit"))
        confirmation_provenance = self.repository.bom_confirmation_provenance(str(header["id"]))
        if not units_complete:
            warnings.append("unit_missing：BOM数量关系已发布，但计量单位尚未由业务方确认，因此当前BOM仅可用于结构查询，不能用于确定性数量计算")
        if parent_unit is None:
            warnings.append("parent_unit_missing：成品计量单位尚未确认，不能进行确定性生产数量计算")
        return {
            "status": "ok",
            "query": {
                **query,
                "resolved_parent_material_code": canonical_parent,
                "resolved_by": item["matched_by"],
            },
            "data": {
                "parent_item": {**self._public_item(item), "unit": parent_unit},
                "revision": str(header["version"]),
                "effective_from": _iso_text(header["effective_from"]),
                "line_count": len(lines),
                "lines": lines,
            },
            "completeness": {
                "bom_lines": bool(lines),
                "qty_per": all(line["qty_per"] is not None for line in lines),
                "price": prices_complete,
                "unit": units_complete,
                "parent_unit": parent_unit is not None,
                "effective_from": header["effective_from"] is not None,
            },
            "provenance": {
                "bom_version": str(header["version"]),
                "import_batch_id": str(header["import_batch_id"]),
                "source_file_id": str(header["source_file_id"]),
                "source_file": str(header["original_filename"]),
                "content_sha256": str(header["content_sha256"]),
                "sheet_name": str(header["source_sheet"]),
                **({"business_confirmations": confirmation_provenance} if confirmation_provenance else {}),
            },
            "warnings": warnings,
        }

    def query_inbound(
        self,
        material_code: str,
        as_of: str | datetime | None = None,
    ) -> FactQueryResult:
        query = {"material_code": material_code, "as_of": _iso_text(as_of)}
        requested_time, time_error = self._parse_as_of(as_of)
        if time_error:
            return _empty_result("invalid_query", query, time_error)
        item, error_result = self._resolve_item(material_code, query)
        if error_result:
            return error_result
        assert item is not None
        payload = self.repository.get_inbound_observation(str(item["item_id"]), requested_time)
        if payload is None:
            reason = "当前事实层没有已发布的权威在途采购数据"
            return _empty_result("data_unavailable", query, reason, reason=reason)
        facts = list(payload["facts"])
        received_complete = bool(facts) and all(
            fact["received_qty"] is not None for fact in facts
        )
        in_transit_complete = bool(facts) and all(
            fact["in_transit_qty"] is not None for fact in facts
        )
        ordered_total = sum((fact["ordered_qty"] for fact in facts), Decimal("0"))
        received_total = (
            sum((fact["received_qty"] for fact in facts), Decimal("0"))
            if received_complete
            else None
        )
        in_transit_total = (
            sum((fact["in_transit_qty"] for fact in facts), Decimal("0"))
            if in_transit_complete
            else None
        )
        warnings: list[str] = []
        if not received_complete:
            warnings.append("已收数量字段不完整，返回 unknown")
        if not in_transit_complete:
            warnings.append("在途数量缺少完整权威事实，返回 unknown")
        observation = dict(payload["observation"])
        return {
            "status": "ok",
            "query": {
                **query,
                "resolved_material_code": item["material_code"],
                "resolved_by": item["matched_by"],
            },
            "data": {
                "canonical_item": self._public_item(item),
                "ordered_qty": _decimal_text(ordered_total),
                "received_qty": _decimal_text(received_total),
                "in_transit_qty": _decimal_text(in_transit_total),
                "lines": [self._public_inbound_line(fact) for fact in facts],
            },
            "completeness": {
                "ordered_qty": True,
                "received_qty": received_complete,
                "in_transit_qty": in_transit_complete,
            },
            "provenance": {
                "observation_id": str(observation["id"]),
                "observed_at": _iso_text(observation["observed_at"]),
                "import_batch_id": str(observation["import_batch_id"]),
                "source_file_id": str(observation["source_file_id"]),
                "source_file": str(observation["original_filename"]),
                "content_sha256": str(observation["content_sha256"]),
                "source_name": str(observation["source_name"]),
            },
            "warnings": warnings,
        }

    @staticmethod
    def _public_item(item: dict[str, object]) -> dict[str, object]:
        return {
            "item_id": item.get("item_id"),
            "material_code": item.get("material_code"),
            "name": item.get("name"),
            "specification": item.get("specification"),
            "unit": item.get("unit"),
        }

    @staticmethod
    def _inventory_provenance(snapshot: dict[str, object]) -> dict[str, object]:
        return {
            "snapshot_id": str(snapshot["id"]),
            "snapshot_at": _iso_text(snapshot["snapshot_at"]),
            "observed_at": _iso_text(snapshot["observed_at"]),
            "import_batch_id": str(snapshot["import_batch_id"]),
            "source_file_id": str(snapshot["source_file_id"]),
            "source_file": str(snapshot["original_filename"]),
            "content_sha256": str(snapshot["content_sha256"]),
            "sheet_name": str(snapshot["source_sheet"]),
        }

    @staticmethod
    def _public_inbound_line(fact: dict[str, object]) -> dict[str, object]:
        return {
            "supplier_code": fact["supplier_code"],
            "order_no": fact["order_no"],
            "ordered_qty": _decimal_text(fact["ordered_qty"]),
            "received_qty": _decimal_text(fact["received_qty"]),
            "in_transit_qty": _decimal_text(fact["in_transit_qty"]),
            "expected_arrival_date": _iso_text(fact["expected_arrival_date"]),
            "committed_date": _iso_text(fact["committed_date"]),
            "status": fact["supply_status"],
            "provenance": {
                "import_batch_id": str(fact["import_batch_id"]),
                "source_file_id": str(fact["source_file_id"]),
                "sheet_name": str(fact["source_sheet"]),
                "row_number": int(fact["source_row_number"]),
            },
        }


__all__ = ["FactQueryService"]
