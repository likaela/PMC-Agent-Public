"""库存事实的 staging、关联、发布与时点查询 Repository。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fact_layer.models import (
    ImportBatch,
    ImportBatchStatus,
    ItemMatchStatus,
    RowValidationError,
    StagedInventoryRow,
)
from repositories.fact_repository import FactRepository


def _new_id() -> str:
    return str(uuid4())


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _cursor_dicts(cursor: Any) -> list[dict[str, Any]]:
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _row_from_dict(row: dict[str, Any]) -> StagedInventoryRow:
    from datetime import date

    return StagedInventoryRow(
        id=str(row["id"]),
        import_batch_id=str(row["import_batch_id"]),
        source_file_id=str(row["source_file_id"]),
        sheet_name=str(row["sheet_name"]),
        source_row_number=int(row["source_row_number"]),
        raw_material_code=(
            str(row["raw_material_code"]) if row["raw_material_code"] is not None else None
        ),
        item_id=str(row["item_id"]) if row["item_id"] is not None else None,
        match_status=ItemMatchStatus(str(row["match_status"])),
        site=str(row["site"]) if row["site"] is not None else None,
        warehouse=str(row["warehouse"]) if row["warehouse"] is not None else None,
        location=str(row["location"]) if row["location"] is not None else None,
        batch_no=str(row["batch_no"]) if row["batch_no"] is not None else None,
        on_hand_qty=(
            Decimal(str(row["on_hand_qty"])) if row["on_hand_qty"] is not None else None
        ),
        available_qty=(
            Decimal(str(row["available_qty"])) if row["available_qty"] is not None else None
        ),
        reserved_qty=(
            Decimal(str(row["reserved_qty"])) if row["reserved_qty"] is not None else None
        ),
        quality_status=(
            str(row["quality_status"]) if row["quality_status"] is not None else None
        ),
        received_at=(
            datetime.fromisoformat(str(row["received_at"]))
            if row["received_at"] is not None
            else None
        ),
        expiry_date=(
            date.fromisoformat(str(row["expiry_date"]))
            if row["expiry_date"] is not None
            else None
        ),
        validation_status=str(row["validation_status"]),
        is_duplicate=bool(row["is_duplicate"]),
    )


class InventoryFactRepository:
    """库存事实持久化入口；不承担 Excel 字段解释。"""

    def __init__(self, fact_repository: FactRepository) -> None:
        self.fact_repository = fact_repository
        self.store = fact_repository.store

    def resolve_item_id(self, material_code: str, connection: Any | None = None) -> str | None:
        def resolve(active_connection: Any) -> str | None:
            direct = self.store.execute(
                active_connection,
                "SELECT id FROM items WHERE material_code = ?",
                (material_code,),
            ).fetchone()
            if direct is not None:
                return str(direct[0])
            alias = self.store.execute(
                active_connection,
                "SELECT item_id FROM item_aliases "
                "WHERE alias_type = ? AND alias_code = ?",
                ("inventory_material_code", material_code),
            ).fetchone()
            return str(alias[0]) if alias is not None else None

        if connection is not None:
            return resolve(connection)
        with self.store.transaction() as managed_connection:
            return resolve(managed_connection)

    def add_item_alias(self, material_code: str, alias_code: str) -> str:
        """显式建立库存料号别名；不会创建第二套 item 主数据。"""
        alias_id = _new_id()
        with self.store.transaction(write_lock=True) as connection:
            item = self.store.execute(
                connection,
                "SELECT id FROM items WHERE material_code = ?",
                (material_code,),
            ).fetchone()
            if item is None:
                raise KeyError("标准 item 不存在，不能建立库存料号别名")
            self.store.execute(
                connection,
                "INSERT INTO item_aliases "
                "(id, item_id, alias_type, alias_code, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    alias_id,
                    str(item[0]),
                    "inventory_material_code",
                    alias_code,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return alias_id

    def resolve_item_ids(self, material_codes: set[str]) -> dict[str, str]:
        """在单个只读事务中批量解析 item / inventory alias。"""
        resolved: dict[str, str] = {}
        with self.store.transaction() as connection:
            for material_code in material_codes:
                item_id = self.resolve_item_id(material_code, connection)
                if item_id is not None:
                    resolved[material_code] = item_id
        return resolved

    def save_staging_result(
        self,
        batch_id: str,
        source_file_id: str,
        source_sheet: str,
        observed_at: datetime,
        snapshot_at: datetime | None,
        excluded_sources: tuple[str, ...],
        source_row_count: int,
        duplicate_row_count: int,
        rows: list[StagedInventoryRow],
        errors: list[RowValidationError],
        status: ImportBatchStatus,
        pending_fields: tuple[str, ...],
        quality_report: dict[str, object],
        raw_payloads: dict[str, dict[str, object]],
    ) -> None:
        with self.store.transaction(write_lock=True) as connection:
            self.store.execute(
                connection,
                "INSERT INTO inventory_import_contexts "
                "(import_batch_id, source_file_id, source_sheet, observed_at, snapshot_at, "
                "excluded_sources_json, source_row_count, duplicate_row_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    batch_id,
                    source_file_id,
                    source_sheet,
                    observed_at.isoformat(),
                    snapshot_at.isoformat() if snapshot_at else None,
                    json.dumps(list(excluded_sources), ensure_ascii=False),
                    source_row_count,
                    duplicate_row_count,
                ),
            )
            for row in rows:
                self.store.execute(
                    connection,
                    "INSERT INTO staged_inventory_rows "
                    "(id, import_batch_id, source_file_id, sheet_name, source_row_number, "
                    "raw_material_code, item_id, match_status, site, warehouse, location, "
                    "batch_no, on_hand_qty, available_qty, reserved_qty, quality_status, "
                    "received_at, expiry_date, validation_status, is_duplicate, raw_payload_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row.id,
                        row.import_batch_id,
                        row.source_file_id,
                        row.sheet_name,
                        row.source_row_number,
                        row.raw_material_code,
                        row.item_id,
                        row.match_status.value,
                        row.site,
                        row.warehouse,
                        row.location,
                        row.batch_no,
                        _decimal_text(row.on_hand_qty),
                        _decimal_text(row.available_qty),
                        _decimal_text(row.reserved_qty),
                        row.quality_status,
                        row.received_at.isoformat() if row.received_at else None,
                        row.expiry_date.isoformat() if row.expiry_date else None,
                        row.validation_status,
                        row.is_duplicate,
                        json.dumps(raw_payloads.get(row.id, {}), ensure_ascii=False, default=str),
                    ),
                )
            self._insert_errors(connection, errors)
            self._save_quality_report(connection, batch_id, "inventory", quality_report)
            self.store.execute(
                connection,
                "UPDATE import_batches SET status = ?, staged_row_count = ?, error_count = ?, "
                "pending_confirmation_fields_json = ? WHERE id = ?",
                (
                    status.value,
                    len(rows),
                    len(errors),
                    json.dumps(list(pending_fields), ensure_ascii=False),
                    batch_id,
                ),
            )

    def _insert_errors(self, connection: Any, errors: list[RowValidationError]) -> None:
        for error in errors:
            self.store.execute(
                connection,
                "INSERT INTO row_validation_errors "
                "(id, import_batch_id, source_file_id, sheet_name, source_row_number, "
                "error_code, field_name, message, raw_value) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    error.id,
                    error.import_batch_id,
                    error.source_file_id,
                    error.sheet_name,
                    error.source_row_number,
                    error.error_code,
                    error.field_name,
                    error.message,
                    error.raw_value,
                ),
            )

    def _save_quality_report(
        self,
        connection: Any,
        batch_id: str,
        fact_type: str,
        quality_report: dict[str, object],
    ) -> None:
        self.store.execute(
            connection,
            "INSERT INTO import_quality_reports "
            "(id, import_batch_id, fact_type, report_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                _new_id(),
                batch_id,
                fact_type,
                json.dumps(quality_report, ensure_ascii=False, default=str),
                datetime.now(UTC).isoformat(),
            ),
        )

    def get_staged_rows(self, batch_id: str) -> list[StagedInventoryRow]:
        with self.store.transaction() as connection:
            cursor = self.store.execute(
                connection,
                "SELECT id, import_batch_id, source_file_id, sheet_name, source_row_number, "
                "raw_material_code, item_id, match_status, site, warehouse, location, batch_no, "
                "on_hand_qty, available_qty, reserved_qty, quality_status, received_at, "
                "expiry_date, validation_status, is_duplicate FROM staged_inventory_rows "
                "WHERE import_batch_id = ? ORDER BY source_row_number",
                (batch_id,),
            )
            return [_row_from_dict(row) for row in _cursor_dicts(cursor)]

    def get_quality_report(self, batch_id: str) -> dict[str, object] | None:
        with self.store.transaction() as connection:
            row = self.store.execute(
                connection,
                "SELECT report_json FROM import_quality_reports WHERE import_batch_id = ?",
                (batch_id,),
            ).fetchone()
        return json.loads(str(row[0])) if row else None

    def set_snapshot_at(self, batch_id: str, snapshot_at: datetime) -> ImportBatch:
        with self.store.transaction(write_lock=True) as connection:
            batch = self._locked_batch(connection, batch_id)
            if batch[0] == ImportBatchStatus.PUBLISHED.value:
                raise ValueError("已发布库存批次不能修改库存时点")
            self.store.execute(
                connection,
                "UPDATE inventory_import_contexts SET snapshot_at = ? WHERE import_batch_id = ?",
                (snapshot_at.isoformat(), batch_id),
            )
            self._refresh_batch_status(connection, batch_id)
        result = self.fact_repository.get_import_batch(batch_id)
        if result is None:
            raise KeyError("导入批次不存在")
        return result

    def refresh_item_matches(self, batch_id: str) -> ImportBatch:
        with self.store.transaction(write_lock=True) as connection:
            batch = self._locked_batch(connection, batch_id)
            if batch[0] == ImportBatchStatus.PUBLISHED.value:
                raise ValueError("已发布库存批次不能刷新料号关联")
            cursor = self.store.execute(
                connection,
                "SELECT id, raw_material_code FROM staged_inventory_rows "
                "WHERE import_batch_id = ? AND validation_status = 'valid'",
                (batch_id,),
            )
            for row_id, raw_material_code in cursor.fetchall():
                item_id = self.resolve_item_id(str(raw_material_code), connection)
                self.store.execute(
                    connection,
                    "UPDATE staged_inventory_rows SET item_id = ?, match_status = ? WHERE id = ?",
                    (
                        item_id,
                        (
                            ItemMatchStatus.MATCHED.value
                            if item_id
                            else ItemMatchStatus.UNMATCHED.value
                        ),
                        row_id,
                    ),
                )
            self._refresh_quality_match_counts(connection, batch_id)
            self._refresh_batch_status(connection, batch_id)
        result = self.fact_repository.get_import_batch(batch_id)
        if result is None:
            raise KeyError("导入批次不存在")
        return result

    def _locked_batch(self, connection: Any, batch_id: str) -> tuple[object, ...]:
        query = "SELECT status, error_count FROM import_batches WHERE id = ?"
        if self.store.dialect == "postgresql":
            query += " FOR UPDATE"
        row = self.store.execute(connection, query, (batch_id,)).fetchone()
        if row is None:
            raise KeyError("导入批次不存在")
        return row

    def _refresh_batch_status(self, connection: Any, batch_id: str) -> None:
        batch = self.store.execute(
            connection,
            "SELECT error_count FROM import_batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
        context = self.store.execute(
            connection,
            "SELECT snapshot_at FROM inventory_import_contexts WHERE import_batch_id = ?",
            (batch_id,),
        ).fetchone()
        unmatched = self.store.execute(
            connection,
            "SELECT COUNT(*) FROM staged_inventory_rows WHERE import_batch_id = ? "
            "AND validation_status = 'valid' AND match_status = ?",
            (batch_id, ItemMatchStatus.UNMATCHED.value),
        ).fetchone()[0]
        pending: list[str] = []
        if context is None or context[0] is None:
            pending.append("snapshot_at")
        if int(unmatched) > 0:
            pending.append("item_mapping")
        if batch is not None and int(batch[0]) > 0:
            status = ImportBatchStatus.INVALID
        elif pending:
            status = ImportBatchStatus.PENDING_CONFIRMATION
        else:
            status = ImportBatchStatus.READY_TO_PUBLISH
        self.store.execute(
            connection,
            "UPDATE import_batches SET status = ?, pending_confirmation_fields_json = ? WHERE id = ?",
            (status.value, json.dumps(pending, ensure_ascii=False), batch_id),
        )

    def _refresh_quality_match_counts(self, connection: Any, batch_id: str) -> None:
        row = self.store.execute(
            connection,
            "SELECT report_json FROM import_quality_reports WHERE import_batch_id = ?",
            (batch_id,),
        ).fetchone()
        if row is None:
            return
        report = json.loads(str(row[0]))
        match_rows = self.store.execute(
            connection,
            "SELECT raw_material_code, match_status FROM staged_inventory_rows "
            "WHERE import_batch_id = ? AND validation_status = 'valid'",
            (batch_id,),
        ).fetchall()
        matched = {str(code) for code, status in match_rows if status == "matched"}
        unmatched = {str(code) for code, status in match_rows if status == "unmatched"}
        report["item_master_matched_count"] = len(matched)
        report["unmatched_material_count"] = len(unmatched)
        self.store.execute(
            connection,
            "UPDATE import_quality_reports SET report_json = ? WHERE import_batch_id = ?",
            (json.dumps(report, ensure_ascii=False), batch_id),
        )

    def publish(self, batch_id: str) -> dict[str, object]:
        snapshot_id = _new_id()
        with self.store.transaction(write_lock=True) as connection:
            batch = self._locked_batch(connection, batch_id)
            if str(batch[0]) != ImportBatchStatus.READY_TO_PUBLISH.value or int(batch[1]) != 0:
                raise ValueError("库存批次尚未通过校验、时点确认和 item 关联，不能发布")
            context_cursor = self.store.execute(
                connection,
                "SELECT c.source_file_id, c.source_sheet, c.observed_at, c.snapshot_at, "
                "sf.content_sha256 FROM inventory_import_contexts c "
                "JOIN source_files sf ON sf.id = c.source_file_id WHERE c.import_batch_id = ?",
                (batch_id,),
            )
            contexts = _cursor_dicts(context_cursor)
            if not contexts or contexts[0]["snapshot_at"] is None:
                raise ValueError("库存时点尚未确认")
            context = contexts[0]
            if self.store.dialect == "postgresql":
                digest = hashlib.sha256(
                    (
                        str(context["content_sha256"])
                        + "\0"
                        + str(context["source_sheet"])
                        + "\0"
                        + str(context["snapshot_at"])
                    ).encode("utf-8")
                ).digest()
                self.store.execute(
                    connection,
                    "SELECT pg_advisory_xact_lock(?)",
                    (int.from_bytes(digest[:8], "big", signed=True),),
                )
            duplicate = self.store.execute(
                connection,
                "SELECT id FROM inventory_snapshots WHERE source_content_sha256 = ? "
                "AND source_sheet = ? AND snapshot_at = ?",
                (
                    context["content_sha256"],
                    context["source_sheet"],
                    context["snapshot_at"],
                ),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("同一来源文件、工作表和库存时点已发布，禁止静默覆盖")
            rows = self.store.execute(
                connection,
                "SELECT id, source_file_id, sheet_name, source_row_number, raw_material_code, "
                "item_id, site, warehouse, location, batch_no, on_hand_qty, available_qty, "
                "reserved_qty, quality_status, received_at, expiry_date, validation_status, "
                "match_status, is_duplicate FROM staged_inventory_rows "
                "WHERE import_batch_id = ? ORDER BY source_row_number",
                (batch_id,),
            ).fetchall()
            if not rows or any(row[16] != "valid" or row[17] != "matched" for row in rows):
                raise ValueError("库存 staging 仍包含无效或未匹配 item 的记录")
            published_at = datetime.now(UTC).isoformat()
            self.store.execute(
                connection,
                "INSERT INTO inventory_snapshots "
                "(id, snapshot_at, observed_at, import_batch_id, source_file_id, "
                "source_content_sha256, source_sheet, published_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot_id,
                    context["snapshot_at"],
                    context["observed_at"],
                    batch_id,
                    context["source_file_id"],
                    context["content_sha256"],
                    context["source_sheet"],
                    published_at,
                ),
            )
            for row in rows:
                self.store.execute(
                    connection,
                    "INSERT INTO inventory_facts "
                    "(id, inventory_snapshot_id, item_id, raw_material_code, site, warehouse, "
                    "location, batch_no, on_hand_qty, available_qty, reserved_qty, quality_status, "
                    "received_at, expiry_date, import_batch_id, source_file_id, source_sheet, "
                    "source_row_number, is_duplicate) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _new_id(),
                        snapshot_id,
                        row[5],
                        row[4],
                        row[6],
                        row[7],
                        row[8],
                        row[9],
                        row[10],
                        row[11],
                        row[12],
                        row[13],
                        row[14],
                        row[15],
                        batch_id,
                        row[1],
                        row[2],
                        row[3],
                        row[18],
                    ),
                )
            self.store.execute(
                connection,
                "UPDATE import_batches SET status = ?, published_at = ? WHERE id = ?",
                (ImportBatchStatus.PUBLISHED.value, published_at, batch_id),
            )
        return {
            "import_batch_id": batch_id,
            "inventory_snapshot_id": snapshot_id,
            "status": ImportBatchStatus.PUBLISHED.value,
            "fact_count": len(rows),
        }

    def get_inventory_as_of(
        self,
        material_code: str,
        as_of: datetime,
    ) -> dict[str, object] | None:
        """返回不晚于给定时间的最近库存快照及全部行级事实。"""
        with self.store.transaction() as connection:
            item_id = self.resolve_item_id(material_code, connection)
            if item_id is None:
                return None
            snapshot = self.store.execute(
                connection,
                "SELECT s.id, s.snapshot_at, s.observed_at, s.import_batch_id, s.source_file_id, "
                "s.source_sheet, sf.original_filename, sf.content_sha256 "
                "FROM inventory_snapshots s JOIN inventory_facts f "
                "ON f.inventory_snapshot_id = s.id JOIN source_files sf ON sf.id = s.source_file_id "
                "WHERE f.item_id = ? AND s.snapshot_at <= ? "
                "ORDER BY s.snapshot_at DESC LIMIT 1",
                (item_id, as_of.isoformat()),
            ).fetchone()
            if snapshot is None:
                return None
            rows_cursor = self.store.execute(
                connection,
                "SELECT raw_material_code, site, warehouse, location, batch_no, on_hand_qty, "
                "available_qty, reserved_qty, quality_status, received_at, expiry_date, "
                "source_sheet, source_row_number, import_batch_id, source_file_id, is_duplicate "
                "FROM inventory_facts WHERE inventory_snapshot_id = ? AND item_id = ? "
                "ORDER BY source_row_number",
                (snapshot[0], item_id),
            )
            facts = _cursor_dicts(rows_cursor)
        for fact in facts:
            for field_name in ("on_hand_qty", "available_qty", "reserved_qty"):
                if fact[field_name] is not None:
                    fact[field_name] = Decimal(str(fact[field_name]))
            fact["is_duplicate"] = bool(fact["is_duplicate"])
        total_on_hand_qty = sum(
            (fact["on_hand_qty"] for fact in facts),
            Decimal("0"),
        )
        total_available_qty = (
            sum((fact["available_qty"] for fact in facts), Decimal("0"))
            if facts and all(fact["available_qty"] is not None for fact in facts)
            else None
        )
        total_reserved_qty = (
            sum((fact["reserved_qty"] for fact in facts), Decimal("0"))
            if facts and all(fact["reserved_qty"] is not None for fact in facts)
            else None
        )
        return {
            "snapshot": {
                "id": str(snapshot[0]),
                "snapshot_at": str(snapshot[1]),
                "observed_at": str(snapshot[2]),
                "import_batch_id": str(snapshot[3]),
                "source_file_id": str(snapshot[4]),
                "source_sheet": str(snapshot[5]),
                "source_file": str(snapshot[6]),
                "content_sha256": str(snapshot[7]),
            },
            "totals": {
                "on_hand_qty": total_on_hand_qty,
                "available_qty": total_available_qty,
                "reserved_qty": total_reserved_qty,
            },
            "facts": facts,
        }

    def count_published_facts(self) -> int:
        with self.store.transaction() as connection:
            return int(self.store.execute(connection, "SELECT COUNT(*) FROM inventory_facts").fetchone()[0])


__all__ = ["InventoryFactRepository"]
