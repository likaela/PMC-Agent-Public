"""已发布事实的只读查询 Repository。

本模块只访问数据库中的 published facts，不读取 Excel、raw 目录或旧固定文件
Repository，也不包含 PMC 生产决策算法。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from repositories.fact_repository import FactRepository


def _cursor_dicts(cursor: Any) -> list[dict[str, Any]]:
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


class FactQueryRepository:
    """Item/Alias、库存、BOM 和在途 published facts 的查询入口。"""

    def __init__(self, fact_repository: FactRepository) -> None:
        self.fact_repository = fact_repository
        self.store = fact_repository.store

    def _time_param(self, value: datetime) -> object:
        return value if self.store.dialect == "postgresql" else value.isoformat()

    def bom_confirmation_provenance(self, bom_header_id: str) -> list[dict]:
        from repositories.published_bom_confirmation_repository import PublishedBomConfirmationRepository
        return PublishedBomConfirmationRepository(self.store).provenance_for_bom(bom_header_id)

    def resolve_item(self, material_code_or_alias: str) -> dict[str, object] | None:
        """只做精确 canonical/alias 匹配；别名歧义时明确拒绝。"""
        with self.store.transaction() as connection:
            direct = self.store.execute(
                connection,
                "SELECT id, material_code, name, specification, unit "
                "FROM items WHERE material_code = ?",
                (material_code_or_alias,),
            ).fetchone()
            if direct is not None:
                return {
                    "item_id": str(direct[0]),
                    "material_code": str(direct[1]),
                    "name": str(direct[2]) if direct[2] is not None else None,
                    "specification": str(direct[3]) if direct[3] is not None else None,
                    "unit": str(direct[4]) if direct[4] is not None else None,
                    "matched_by": "canonical_item",
                    "alias": None,
                }
            aliases = self.store.execute(
                connection,
                "SELECT DISTINCT i.id, i.material_code, i.name, i.specification, i.unit, "
                "a.alias_type FROM item_aliases a JOIN items i ON i.id = a.item_id "
                "WHERE a.alias_code = ? ORDER BY i.material_code, a.alias_type",
                (material_code_or_alias,),
            ).fetchall()
        unique_items = {str(row[0]) for row in aliases}
        if not aliases:
            return None
        if len(unique_items) > 1:
            return {
                "ambiguous": True,
                "alias": material_code_or_alias,
                "candidate_count": len(unique_items),
            }
        row = aliases[0]
        return {
            "item_id": str(row[0]),
            "material_code": str(row[1]),
            "name": str(row[2]) if row[2] is not None else None,
            "specification": str(row[3]) if row[3] is not None else None,
            "unit": str(row[4]) if row[4] is not None else None,
            "matched_by": "item_alias",
            "alias": material_code_or_alias,
            "alias_type": str(row[5]),
        }

    def get_inventory_snapshot(
        self,
        item_id: str,
        as_of: datetime | None,
    ) -> dict[str, object] | None:
        params: list[object] = [item_id]
        time_clause = ""
        if as_of is not None:
            time_clause = " AND s.snapshot_at <= ?"
            params.append(self._time_param(as_of))
        with self.store.transaction() as connection:
            snapshot_cursor = self.store.execute(
                connection,
                "SELECT s.id, s.snapshot_at, s.observed_at, s.import_batch_id, "
                "s.source_file_id, s.source_sheet, sf.original_filename, sf.content_sha256 "
                "FROM inventory_snapshots s JOIN inventory_facts f "
                "ON f.inventory_snapshot_id = s.id "
                "JOIN source_files sf ON sf.id = s.source_file_id "
                "WHERE f.item_id = ?" + time_clause + " "
                "GROUP BY s.id, s.snapshot_at, s.observed_at, s.import_batch_id, "
                "s.source_file_id, s.source_sheet, sf.original_filename, sf.content_sha256 "
                "ORDER BY s.snapshot_at DESC LIMIT 1",
                params,
            )
            snapshots = _cursor_dicts(snapshot_cursor)
            if not snapshots:
                return None
            snapshot = snapshots[0]
            facts_cursor = self.store.execute(
                connection,
                "SELECT f.raw_material_code, f.site, f.warehouse, f.location, f.batch_no, "
                "f.on_hand_qty, f.available_qty, f.reserved_qty, f.quality_status, "
                "f.received_at, f.expiry_date, f.source_sheet, f.source_row_number, "
                "f.import_batch_id, f.source_file_id, f.is_duplicate "
                "FROM inventory_facts f WHERE f.inventory_snapshot_id = ? AND f.item_id = ? "
                "ORDER BY f.source_row_number",
                (snapshot["id"], item_id),
            )
            facts = _cursor_dicts(facts_cursor)
        for fact in facts:
            for field_name in ("on_hand_qty", "available_qty", "reserved_qty"):
                fact[field_name] = (
                    Decimal(str(fact[field_name])) if fact[field_name] is not None else None
                )
            fact["is_duplicate"] = bool(fact["is_duplicate"])
        return {"snapshot": snapshot, "facts": facts}

    def get_published_bom(
        self,
        parent_material_code: str,
        revision: str,
    ) -> dict[str, object] | None:
        with self.store.transaction() as connection:
            header_cursor = self.store.execute(
                connection,
                "SELECT h.id, h.parent_material_code, h.version, h.effective_from, "
                "h.import_batch_id, h.source_file_id, h.source_sheet, sf.original_filename, "
                "sf.content_sha256, CASE WHEN ctx.publish_parent_item = 1 THEN COALESCE(parent.unit, ctx.parent_unit) "
                "ELSE parent.unit END AS parent_unit FROM bom_headers h "
                "JOIN source_files sf ON sf.id = h.source_file_id "
                "LEFT JOIN staged_bom_headers ctx ON ctx.import_batch_id = h.import_batch_id "
                "LEFT JOIN items parent ON parent.material_code = h.parent_material_code "
                "WHERE h.parent_material_code = ? AND h.version = ?",
                (parent_material_code, revision),
            )
            headers = _cursor_dicts(header_cursor)
            if not headers:
                return None
            header = headers[0]
            line_cursor = self.store.execute(
                connection,
                "SELECT bl.id AS bom_line_id, i.id AS item_id, i.material_code, "
                "ir.name, ir.specification, i.unit AS item_unit, bl.quantity, bl.unit, "
                "pr.tax_included_unit_price, pr.currency, pr.effective_from, "
                "bl.import_batch_id, bl.source_file_id, bl.source_sheet, "
                "bl.source_row_number, sf.original_filename AS source_file, "
                "sf.content_sha256 FROM bom_lines bl "
                "JOIN items i ON i.id = bl.item_id "
                "LEFT JOIN item_revisions ir ON ir.item_id = bl.item_id "
                "AND ir.import_batch_id = bl.import_batch_id "
                "LEFT JOIN price_records pr ON pr.item_id = bl.item_id "
                "AND pr.import_batch_id = bl.import_batch_id "
                "AND pr.source_row_number = bl.source_row_number "
                "JOIN source_files sf ON sf.id = bl.source_file_id "
                "WHERE bl.bom_header_id = ? ORDER BY bl.source_sheet, bl.source_row_number",
                (header["id"],),
            )
            lines = _cursor_dicts(line_cursor)
        for line in lines:
            line["quantity"] = Decimal(str(line["quantity"]))
            line["tax_included_unit_price"] = (
                Decimal(str(line["tax_included_unit_price"]))
                if line["tax_included_unit_price"] is not None
                else None
            )
        return {"header": header, "lines": lines}

    def list_published_bom_versions(self, parent_material_code: str) -> list[dict[str, object]]:
        """仅列出已发布的精确父件版本；生效区间由上层确定性校验。"""
        with self.store.transaction() as connection:
            cursor = self.store.execute(
                connection,
                "SELECT h.version, h.effective_from, h.effective_to, "
                "h.import_batch_id, h.source_sheet AS sheet_name, "
                "sf.original_filename AS source_file, sf.content_sha256 "
                "FROM bom_headers h JOIN import_batches b ON b.id = h.import_batch_id "
                "JOIN source_files sf ON sf.id = h.source_file_id "
                "WHERE h.parent_material_code = ? AND b.status = 'published' "
                "ORDER BY h.version",
                (parent_material_code,),
            )
            return _cursor_dicts(cursor)

    def list_unit_conversions(self, item_id: str) -> list[dict[str, object]]:
        """返回已发布的显式换算事实，不从单位名称推导比例。"""
        with self.store.transaction() as connection:
            cursor = self.store.execute(
                connection,
                "SELECT c.from_unit, c.to_unit, c.factor, c.effective_from, c.effective_to, "
                "c.import_batch_id, c.source_sheet AS sheet_name, c.source_row_number AS row_number, "
                "sf.original_filename AS source_file, sf.content_sha256 "
                "FROM unit_conversions c JOIN import_batches b ON b.id = c.import_batch_id "
                "JOIN source_files sf ON sf.id = c.source_file_id "
                "WHERE c.item_id = ? AND b.status = 'published' "
                "ORDER BY c.from_unit, c.to_unit, c.effective_from",
                (item_id,),
            )
            rows = _cursor_dicts(cursor)
        for row in rows:
            row["factor"] = Decimal(str(row["factor"]))
        return rows

    def get_inbound_observation(
        self,
        item_id: str,
        as_of: datetime | None,
    ) -> dict[str, object] | None:
        params: list[object] = [item_id]
        time_clause = ""
        if as_of is not None:
            time_clause = " AND o.observed_at <= ?"
            params.append(self._time_param(as_of))
        with self.store.transaction() as connection:
            observation_cursor = self.store.execute(
                connection,
                "SELECT o.id, o.observed_at, o.import_batch_id, o.source_file_id, "
                "o.source_name, sf.original_filename, sf.content_sha256 "
                "FROM inbound_supply_observations o JOIN inbound_supply_facts f "
                "ON f.inbound_observation_id = o.id "
                "JOIN source_files sf ON sf.id = o.source_file_id "
                "WHERE f.item_id = ?" + time_clause + " "
                "GROUP BY o.id, o.observed_at, o.import_batch_id, o.source_file_id, "
                "o.source_name, sf.original_filename, sf.content_sha256 "
                "ORDER BY o.observed_at DESC LIMIT 1",
                params,
            )
            observations = _cursor_dicts(observation_cursor)
            if not observations:
                return None
            observation = observations[0]
            facts_cursor = self.store.execute(
                connection,
                "SELECT raw_material_code, supplier_code, order_no, ordered_qty, "
                "received_qty, in_transit_qty, expected_arrival_date, committed_date, "
                "supply_status, import_batch_id, source_file_id, source_sheet, "
                "source_row_number FROM inbound_supply_facts "
                "WHERE inbound_observation_id = ? AND item_id = ? "
                "ORDER BY source_sheet, source_row_number",
                (observation["id"], item_id),
            )
            facts = _cursor_dicts(facts_cursor)
        for fact in facts:
            for field_name in ("ordered_qty", "received_qty", "in_transit_qty"):
                fact[field_name] = (
                    Decimal(str(fact[field_name])) if fact[field_name] is not None else None
                )
        return {"observation": observation, "facts": facts}


__all__ = ["FactQueryRepository"]
