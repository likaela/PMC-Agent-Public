"""采购在途事实的 staging 与受控发布 Repository。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fact_layer.models import (
    ImportBatchStatus,
    ItemMatchStatus,
    RowValidationError,
    StagedInboundSupplyRow,
)
from repositories.fact_repository import FactRepository
from repositories.inventory_fact_repository import InventoryFactRepository


def _new_id() -> str:
    return str(uuid4())


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


class InboundSupplyFactRepository:
    def __init__(self, fact_repository: FactRepository) -> None:
        self.fact_repository = fact_repository
        self.store = fact_repository.store
        self.item_repository = InventoryFactRepository(fact_repository)

    def save_staging_result(
        self,
        batch_id: str,
        source_file_id: str,
        observed_at: datetime,
        source_name: str,
        rows: list[StagedInboundSupplyRow],
        errors: list[RowValidationError],
        status: ImportBatchStatus,
        pending_fields: tuple[str, ...],
        quality_report: dict[str, object],
    ) -> None:
        with self.store.transaction(write_lock=True) as connection:
            self.store.execute(
                connection,
                "INSERT INTO inbound_import_contexts "
                "(import_batch_id, source_file_id, observed_at, source_name, source_reliability) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    batch_id,
                    source_file_id,
                    observed_at.isoformat(),
                    source_name,
                    "authoritative_purchase_order",
                ),
            )
            for row in rows:
                self.store.execute(
                    connection,
                    "INSERT INTO staged_inbound_supply_rows "
                    "(id, import_batch_id, source_file_id, sheet_name, source_row_number, "
                    "raw_material_code, item_id, match_status, supplier_code, order_no, "
                    "ordered_qty, received_qty, in_transit_qty, expected_arrival_date, "
                    "committed_date, supply_status, validation_status, raw_payload_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row.id,
                        row.import_batch_id,
                        row.source_file_id,
                        row.sheet_name,
                        row.source_row_number,
                        row.raw_material_code,
                        row.item_id,
                        row.match_status.value,
                        row.supplier_code,
                        row.order_no,
                        _decimal_text(row.ordered_qty),
                        _decimal_text(row.received_qty),
                        _decimal_text(row.in_transit_qty),
                        row.expected_arrival_date.isoformat()
                        if row.expected_arrival_date
                        else None,
                        row.committed_date.isoformat() if row.committed_date else None,
                        row.supply_status,
                        row.validation_status,
                        "{}",
                    ),
                )
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
            self.store.execute(
                connection,
                "INSERT INTO import_quality_reports "
                "(id, import_batch_id, fact_type, report_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    _new_id(),
                    batch_id,
                    "inbound_supply",
                    json.dumps(quality_report, ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                ),
            )
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

    def publish(self, batch_id: str) -> dict[str, object]:
        observation_id = _new_id()
        with self.store.transaction(write_lock=True) as connection:
            query = "SELECT status, error_count FROM import_batches WHERE id = ?"
            if self.store.dialect == "postgresql":
                query += " FOR UPDATE"
            batch = self.store.execute(connection, query, (batch_id,)).fetchone()
            if batch is None:
                raise KeyError("导入批次不存在")
            if str(batch[0]) != ImportBatchStatus.READY_TO_PUBLISH.value or int(batch[1]) != 0:
                raise ValueError("在途批次尚未通过校验和 item 关联，不能发布")
            context = self.store.execute(
                connection,
                "SELECT c.source_file_id, c.observed_at, c.source_name, sf.content_sha256 "
                "FROM inbound_import_contexts c JOIN source_files sf ON sf.id = c.source_file_id "
                "WHERE c.import_batch_id = ?",
                (batch_id,),
            ).fetchone()
            if context is None:
                raise ValueError("在途导入上下文不存在")
            if self.store.dialect == "postgresql":
                digest = hashlib.sha256(
                    f"{context[3]}\0{context[2]}\0{context[1]}".encode("utf-8")
                ).digest()
                self.store.execute(
                    connection,
                    "SELECT pg_advisory_xact_lock(?)",
                    (int.from_bytes(digest[:8], "big", signed=True),),
                )
            duplicate = self.store.execute(
                connection,
                "SELECT id FROM inbound_supply_observations WHERE source_content_sha256 = ? "
                "AND source_name = ? AND observed_at = ?",
                (context[3], context[2], context[1]),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("同一来源和观测时点的在途事实已发布，禁止静默覆盖")
            rows = self.store.execute(
                connection,
                "SELECT source_file_id, sheet_name, source_row_number, raw_material_code, "
                "item_id, supplier_code, order_no, ordered_qty, received_qty, in_transit_qty, "
                "expected_arrival_date, committed_date, supply_status, validation_status, match_status "
                "FROM staged_inbound_supply_rows WHERE import_batch_id = ? "
                "ORDER BY sheet_name, source_row_number",
                (batch_id,),
            ).fetchall()
            if not rows or any(row[13] != "valid" or row[14] != "matched" for row in rows):
                raise ValueError("在途 staging 仍包含无效或未匹配 item 的记录")
            published_at = datetime.now(UTC).isoformat()
            self.store.execute(
                connection,
                "INSERT INTO inbound_supply_observations "
                "(id, observed_at, import_batch_id, source_file_id, source_content_sha256, "
                "source_name, published_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    observation_id,
                    context[1],
                    batch_id,
                    context[0],
                    context[3],
                    context[2],
                    published_at,
                ),
            )
            for row in rows:
                self.store.execute(
                    connection,
                    "INSERT INTO inbound_supply_facts "
                    "(id, inbound_observation_id, item_id, raw_material_code, supplier_code, "
                    "order_no, ordered_qty, received_qty, in_transit_qty, expected_arrival_date, "
                    "committed_date, supply_status, import_batch_id, source_file_id, source_sheet, "
                    "source_row_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _new_id(),
                        observation_id,
                        row[4],
                        row[3],
                        row[5],
                        row[6],
                        row[7],
                        row[8],
                        row[9],
                        row[10],
                        row[11],
                        row[12],
                        batch_id,
                        row[0],
                        row[1],
                        row[2],
                    ),
                )
            self.store.execute(
                connection,
                "UPDATE import_batches SET status = ?, published_at = ? WHERE id = ?",
                (ImportBatchStatus.PUBLISHED.value, published_at, batch_id),
            )
        return {
            "import_batch_id": batch_id,
            "inbound_observation_id": observation_id,
            "status": ImportBatchStatus.PUBLISHED.value,
            "fact_count": len(rows),
        }

    def refresh_item_matches(self, batch_id: str) -> ImportBatchStatus:
        """在 item/alias 经人工治理后重新关联，不创建或猜测物料主数据。"""
        with self.store.transaction(write_lock=True) as connection:
            query = "SELECT status, error_count FROM import_batches WHERE id = ?"
            if self.store.dialect == "postgresql":
                query += " FOR UPDATE"
            batch = self.store.execute(connection, query, (batch_id,)).fetchone()
            if batch is None:
                raise KeyError("导入批次不存在")
            if str(batch[0]) == ImportBatchStatus.PUBLISHED.value:
                raise ValueError("已发布在途批次不能刷新料号关联")
            rows = self.store.execute(
                connection,
                "SELECT id, raw_material_code FROM staged_inbound_supply_rows "
                "WHERE import_batch_id = ? AND validation_status = 'valid'",
                (batch_id,),
            ).fetchall()
            unmatched = 0
            matched_codes: set[str] = set()
            unmatched_codes: set[str] = set()
            for row_id, material_code in rows:
                item_id = self.item_repository.resolve_item_id(str(material_code), connection)
                if item_id is None:
                    unmatched += 1
                    unmatched_codes.add(str(material_code))
                else:
                    matched_codes.add(str(material_code))
                self.store.execute(
                    connection,
                    "UPDATE staged_inbound_supply_rows SET item_id = ?, match_status = ? "
                    "WHERE id = ?",
                    (
                        item_id,
                        "matched" if item_id else "unmatched",
                        row_id,
                    ),
                )
            report_row = self.store.execute(
                connection,
                "SELECT report_json FROM import_quality_reports WHERE import_batch_id = ?",
                (batch_id,),
            ).fetchone()
            if report_row is not None:
                report = json.loads(str(report_row[0]))
                report["item_master_matched_count"] = len(matched_codes)
                report["unmatched_material_count"] = len(unmatched_codes)
                self.store.execute(
                    connection,
                    "UPDATE import_quality_reports SET report_json = ? WHERE import_batch_id = ?",
                    (json.dumps(report, ensure_ascii=False), batch_id),
                )
            if int(batch[1]) > 0:
                status = ImportBatchStatus.INVALID
                pending: list[str] = []
            elif unmatched:
                status = ImportBatchStatus.PENDING_CONFIRMATION
                pending = ["item_mapping"]
            else:
                status = ImportBatchStatus.READY_TO_PUBLISH
                pending = []
            self.store.execute(
                connection,
                "UPDATE import_batches SET status = ?, pending_confirmation_fields_json = ? "
                "WHERE id = ?",
                (status.value, json.dumps(pending, ensure_ascii=False), batch_id),
            )
        return status

    def get_quality_report(self, batch_id: str) -> dict[str, object] | None:
        with self.store.transaction() as connection:
            row = self.store.execute(
                connection,
                "SELECT report_json FROM import_quality_reports WHERE import_batch_id = ?",
                (batch_id,),
            ).fetchone()
        return json.loads(str(row[0])) if row else None

    def get_staged_rows(self, batch_id: str) -> list[StagedInboundSupplyRow]:
        with self.store.transaction() as connection:
            cursor = self.store.execute(
                connection,
                "SELECT id, import_batch_id, source_file_id, sheet_name, source_row_number, "
                "raw_material_code, item_id, match_status, supplier_code, order_no, ordered_qty, "
                "received_qty, in_transit_qty, expected_arrival_date, committed_date, "
                "supply_status, validation_status FROM staged_inbound_supply_rows "
                "WHERE import_batch_id = ? ORDER BY source_row_number",
                (batch_id,),
            )
            columns = [description[0] for description in cursor.description]
            records = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        return [
            StagedInboundSupplyRow(
                id=str(row["id"]),
                import_batch_id=str(row["import_batch_id"]),
                source_file_id=str(row["source_file_id"]),
                sheet_name=str(row["sheet_name"]),
                source_row_number=int(row["source_row_number"]),
                raw_material_code=(
                    str(row["raw_material_code"])
                    if row["raw_material_code"] is not None
                    else None
                ),
                item_id=str(row["item_id"]) if row["item_id"] is not None else None,
                match_status=ItemMatchStatus(str(row["match_status"])),
                supplier_code=(
                    str(row["supplier_code"]) if row["supplier_code"] is not None else None
                ),
                order_no=str(row["order_no"]) if row["order_no"] is not None else None,
                ordered_qty=(
                    Decimal(str(row["ordered_qty"]))
                    if row["ordered_qty"] is not None
                    else None
                ),
                received_qty=(
                    Decimal(str(row["received_qty"]))
                    if row["received_qty"] is not None
                    else None
                ),
                in_transit_qty=(
                    Decimal(str(row["in_transit_qty"]))
                    if row["in_transit_qty"] is not None
                    else None
                ),
                expected_arrival_date=(
                    date.fromisoformat(str(row["expected_arrival_date"]))
                    if row["expected_arrival_date"] is not None
                    else None
                ),
                committed_date=(
                    date.fromisoformat(str(row["committed_date"]))
                    if row["committed_date"] is not None
                    else None
                ),
                supply_status=(
                    str(row["supply_status"]) if row["supply_status"] is not None else None
                ),
                validation_status=str(row["validation_status"]),
            )
            for row in records
        ]

    def count_published_facts(self) -> int:
        with self.store.transaction() as connection:
            return int(
                self.store.execute(connection, "SELECT COUNT(*) FROM inbound_supply_facts").fetchone()[0]
            )


__all__ = ["InboundSupplyFactRepository"]
