"""受控事实层 Repository。

Repository 只负责持久化、事务和查询；Excel 字段识别与业务校验由上层服务完成。
"""

from __future__ import annotations

import json
import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fact_layer.models import (
    ImportBatch,
    ImportBatchStatus,
    RowValidationError,
    SourceFile,
    SourceKind,
    StagedBomHeader,
    StagedBomRow,
)
from repositories.fact_store import PersistentFactStore


def _new_id() -> str:
    return str(uuid4())


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _date_text(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _cursor_dicts(cursor: Any) -> list[dict[str, Any]]:
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _batch_from_row(row: dict[str, Any]) -> ImportBatch:
    return ImportBatch(
        id=str(row["id"]),
        mapping_name=str(row["mapping_name"]),
        status=ImportBatchStatus(str(row["status"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        published_at=(
            datetime.fromisoformat(str(row["published_at"])) if row["published_at"] else None
        ),
        staged_row_count=int(row["staged_row_count"]),
        error_count=int(row["error_count"]),
        pending_confirmation_fields=tuple(json.loads(str(row["pending_confirmation_fields_json"]))),
    )


def _staged_rows_from_dicts(rows: list[dict[str, Any]]) -> list[StagedBomRow]:
    return [
        StagedBomRow(
            id=str(row["id"]),
            import_batch_id=str(row["import_batch_id"]),
            source_file_id=str(row["source_file_id"]),
            sheet_name=str(row["sheet_name"]),
            source_row_number=int(row["source_row_number"]),
            parent_material_code=(
                str(row["parent_material_code"])
                if row["parent_material_code"] is not None
                else None
            ),
            version=str(row["version"]) if row["version"] is not None else None,
            material_code=(
                str(row["material_code"]) if row["material_code"] is not None else None
            ),
            material_name=(
                str(row["material_name"]) if row["material_name"] is not None else None
            ),
            specification=(
                str(row["specification"]) if row["specification"] is not None else None
            ),
            quantity=Decimal(str(row["quantity"])) if row["quantity"] is not None else None,
            tax_included_unit_price=(
                Decimal(str(row["tax_included_unit_price"]))
                if row["tax_included_unit_price"] is not None
                else None
            ),
            source_total_price=(
                Decimal(str(row["source_total_price"]))
                if row["source_total_price"] is not None
                else None
            ),
            unit=str(row["unit"]) if row["unit"] is not None else None,
            currency=str(row["currency"]) if row["currency"] is not None else None,
            effective_from=(
                date.fromisoformat(str(row["effective_from"]))
                if row["effective_from"] is not None
                else None
            ),
            validation_status=str(row["validation_status"]),
            pending_fields=tuple(json.loads(str(row["pending_fields_json"]))),
        )
        for row in rows
    ]


class FactRepository:
    """Staging 与 published facts 的唯一持久化入口。"""

    def __init__(self, store: PersistentFactStore) -> None:
        self.store = store

    def migrate(self) -> list[str]:
        return self.store.migrate()

    def create_staging_batch(
        self,
        mapping_name: str,
        original_filename: str,
        content_sha256: str,
        byte_size: int,
        source_kind: SourceKind,
        storage_key: str | None,
        source_uri: str | None,
        immutable_copy: bool,
    ) -> tuple[ImportBatch, SourceFile]:
        if not storage_key and not source_uri:
            raise ValueError("源文件必须提供 storage_key 或 source_uri")
        if source_kind == SourceKind.UPLOAD and (not storage_key or not immutable_copy):
            raise ValueError("上传型源文件必须保存不可变私有副本")
        batch_id = _new_id()
        source_file_id = _new_id()
        created_at = datetime.now(UTC)
        with self.store.transaction() as connection:
            self.store.execute(
                connection,
                "INSERT INTO import_batches "
                "(id, mapping_name, status, created_at, pending_confirmation_fields_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    batch_id,
                    mapping_name,
                    ImportBatchStatus.STAGING.value,
                    created_at.isoformat(),
                    "[]",
                ),
            )
            self.store.execute(
                connection,
                "INSERT INTO source_files "
                "(id, import_batch_id, original_filename, content_sha256, byte_size, source_kind, "
                "storage_key, source_uri, immutable_copy) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source_file_id,
                    batch_id,
                    original_filename,
                    content_sha256,
                    byte_size,
                    source_kind.value,
                    storage_key,
                    source_uri,
                    immutable_copy,
                ),
            )
        return (
            ImportBatch(
                id=batch_id,
                mapping_name=mapping_name,
                status=ImportBatchStatus.STAGING,
                created_at=created_at,
            ),
            SourceFile(
                id=source_file_id,
                import_batch_id=batch_id,
                original_filename=original_filename,
                content_sha256=content_sha256,
                byte_size=byte_size,
                source_kind=source_kind,
                storage_key=storage_key,
                source_uri=source_uri,
                immutable_copy=immutable_copy,
            ),
        )

    def get_source_file(self, source_file_id: str) -> SourceFile | None:
        with self.store.transaction() as connection:
            cursor = self.store.execute(
                connection,
                "SELECT id, import_batch_id, original_filename, content_sha256, byte_size, "
                "source_kind, storage_key, source_uri, immutable_copy "
                "FROM source_files WHERE id = ?",
                (source_file_id,),
            )
            rows = _cursor_dicts(cursor)
        if not rows:
            return None
        row = rows[0]
        return SourceFile(
            id=str(row["id"]),
            import_batch_id=str(row["import_batch_id"]),
            original_filename=str(row["original_filename"]),
            content_sha256=str(row["content_sha256"]),
            byte_size=int(row["byte_size"]),
            source_kind=SourceKind(str(row["source_kind"])),
            storage_key=str(row["storage_key"]) if row["storage_key"] is not None else None,
            source_uri=str(row["source_uri"]) if row["source_uri"] is not None else None,
            immutable_copy=bool(row["immutable_copy"]),
        )

    def save_staging_result(
        self,
        batch_id: str,
        rows: list[StagedBomRow],
        errors: list[RowValidationError],
        status: ImportBatchStatus,
        pending_confirmation_fields: tuple[str, ...],
        raw_payloads: dict[str, dict[str, object]] | None = None,
        header: StagedBomHeader | None = None,
    ) -> None:
        raw_payloads = raw_payloads or {}
        with self.store.transaction() as connection:
            if header is not None:
                self.store.execute(connection,
                    "INSERT INTO staged_bom_headers "
                    "(import_batch_id, source_file_id, sheet_name, source_row_number, "
                    "parent_material_code, version, price_policy, publish_parent_item, parent_unit, confirmations_json, unit_policy, publication_scope) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (header.import_batch_id, header.source_file_id, header.sheet_name,
                     header.source_row_number, header.parent_material_code, header.version,
                     header.price_policy, int(header.publish_parent_item), header.parent_unit,
                     json.dumps(header.confirmations, ensure_ascii=False), header.unit_policy, header.publication_scope))
            for row in rows:
                self.store.execute(
                    connection,
                    "INSERT INTO staged_bom_rows "
                    "(id, import_batch_id, source_file_id, sheet_name, source_row_number, "
                    "parent_material_code, version, material_code, material_name, specification, "
                    "quantity, tax_included_unit_price, source_total_price, unit, currency, "
                    "effective_from, validation_status, pending_fields_json, raw_payload_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row.id,
                        row.import_batch_id,
                        row.source_file_id,
                        row.sheet_name,
                        row.source_row_number,
                        row.parent_material_code,
                        row.version,
                        row.material_code,
                        row.material_name,
                        row.specification,
                        _decimal_text(row.quantity),
                        _decimal_text(row.tax_included_unit_price),
                        _decimal_text(row.source_total_price),
                        row.unit,
                        row.currency,
                        _date_text(row.effective_from),
                        row.validation_status,
                        json.dumps(list(row.pending_fields), ensure_ascii=False),
                        json.dumps(raw_payloads.get(row.id, {}), ensure_ascii=False, default=str),
                    ),
                )
            for error in errors:
                self.store.execute(
                    connection,
                    "INSERT INTO row_validation_errors "
                    "(id, import_batch_id, source_file_id, sheet_name, source_row_number, "
                    "error_code, field_name, message, raw_value) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                "UPDATE import_batches SET status = ?, staged_row_count = ?, error_count = ?, "
                "pending_confirmation_fields_json = ? WHERE id = ?",
                (
                    status.value,
                    len(rows),
                    len(errors),
                    json.dumps(list(pending_confirmation_fields), ensure_ascii=False),
                    batch_id,
                ),
            )
            completeness, warnings, capabilities = self._bom_quality(rows, header)
            self.store.execute(connection,
                "INSERT INTO import_quality_reports (id, import_batch_id, fact_type, report_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (_new_id(), batch_id, "bom", json.dumps({
                    "line_count": len(rows), "error_count": len(errors),
                    "completeness": completeness, "warnings": warnings,
                    "capabilities": capabilities,
                    "pending_confirmation_fields": list(pending_confirmation_fields),
                }, ensure_ascii=False), datetime.now(UTC).isoformat()))

    def get_staged_header(self, batch_id: str, connection: Any = None) -> StagedBomHeader | None:
        if connection is None:
            with self.store.transaction() as current:
                return self.get_staged_header(batch_id, current)
        cursor = self.store.execute(connection,
            "SELECT import_batch_id, source_file_id, sheet_name, source_row_number, "
            "parent_material_code, version, price_policy, publish_parent_item, parent_unit, confirmations_json, unit_policy "
            ", publication_scope FROM staged_bom_headers WHERE import_batch_id = ?", (batch_id,))
        rows = _cursor_dicts(cursor)
        if not rows:
            return None
        value = rows[0]
        return StagedBomHeader(
            import_batch_id=str(value["import_batch_id"]), source_file_id=str(value["source_file_id"]),
            sheet_name=str(value["sheet_name"]), source_row_number=int(value["source_row_number"]),
            parent_material_code=str(value["parent_material_code"]), version=str(value["version"]),
            price_policy=str(value["price_policy"]), publish_parent_item=bool(value["publish_parent_item"]),
            parent_unit=value["parent_unit"], confirmations=tuple(json.loads(value["confirmations_json"])),
            unit_policy=str(value["unit_policy"]), publication_scope=str(value["publication_scope"]))

    @staticmethod
    def _has_publication_confirmation(header: StagedBomHeader, effective_from: date | None = None) -> bool:
        for audit in header.confirmations:
            if not isinstance(audit, dict) or not all(
                isinstance(audit.get(field), str) and audit[field].strip()
                for field in ("confirmed_by", "reason", "confirmed_at")
            ):
                continue
            fields = audit.get("fields")
            if not isinstance(fields, dict):
                continue
            if header.publication_scope == "structure":
                if fields.get("publication_scope") == "structure":
                    return True
                continue
            try:
                confirmed_date = date.fromisoformat(str(fields.get("effective_from")))
            except ValueError:
                continue
            if effective_from is None or confirmed_date == effective_from:
                return True
        return False

    @staticmethod
    def _bom_quality(rows: list[StagedBomRow], header: StagedBomHeader | None, *, published: bool = False):
        completeness = {
            "unit": bool(rows) and all(bool(row.unit) for row in rows),
            "parent_unit": bool(header and header.parent_unit),
            "price": bool(rows) and not (header and header.publication_scope == "structure") and all(row.tax_included_unit_price is not None for row in rows),
            "effective_from": bool(rows) and all(row.effective_from is not None for row in rows),
        }
        warnings = []
        if not completeness["unit"] or (header and header.publish_parent_item and not completeness["parent_unit"]):
            warnings.append("unit_missing：BOM数量关系已保留，但计量单位尚未由业务方确认，仅可用于结构查询，不能用于确定性数量计算")
        if not completeness["price"]:
            warnings.append("price_missing：BOM价格未知，不生成零价格或虚构成本")
        if header and header.publication_scope == "structure" and any(row.tax_included_unit_price is not None for row in rows):
            warnings.append("price_unpublished：原始金额保留在暂存审计，未确认币种/价格有效期，不发布为可计算价格事实")
        if not completeness["effective_from"]:
            warnings.append("effective_date_missing：BOM生效日期未确认，仅发布结构，不声明当前有效或用于生产计算")
        capabilities = {
            "product_discovery": published and bool(header and header.publish_parent_item),
            "bom_structure_query": published and bool(rows),
            "price_analysis": published and completeness["price"] and completeness["unit"],
            # BOM publication alone never establishes inventory/production readiness.
            "inventory_feasibility": False,
            "deterministic_production_analysis": False,
        }
        return completeness, warnings, capabilities

    @staticmethod
    def _row_pending(row: StagedBomRow, price_policy: str, unit_policy: str = "required", publication_scope: str = "full") -> list[str]:
        if publication_scope == "structure":
            return []
        pending = []
        if not row.unit and unit_policy == "required":
            pending.append("unit")
        if row.effective_from is None:
            pending.append("effective_from")
        if (price_policy == "required" or row.tax_included_unit_price is not None) and not row.currency:
            pending.append("currency")
        return pending

    def _refresh_bom_pending(self, connection: Any, batch_id: str) -> None:
        header = self.get_staged_header(batch_id, connection)
        policy = header.price_policy if header else "required"
        unit_policy = header.unit_policy if header else "required"
        scope = header.publication_scope if header else "full"
        cursor = self.store.execute(connection,
            "SELECT id, import_batch_id, source_file_id, sheet_name, source_row_number, "
            "parent_material_code, version, material_code, material_name, specification, quantity, "
            "tax_included_unit_price, source_total_price, unit, currency, effective_from, "
            "validation_status, pending_fields_json FROM staged_bom_rows WHERE import_batch_id = ?", (batch_id,))
        pending = set()
        rows = _staged_rows_from_dicts(_cursor_dicts(cursor))
        for row in rows:
            fields = self._row_pending(row, policy, unit_policy, scope)
            pending.update(fields)
            self.store.execute(connection, "UPDATE staged_bom_rows SET pending_fields_json = ? WHERE id = ?",
                (json.dumps(fields), row.id))
        if header and header.publish_parent_item and not header.parent_unit and unit_policy == "required":
            pending.add("parent_unit")
        if header and header.publish_parent_item and unit_policy == "optional_for_structure" and not self._has_publication_confirmation(header):
            pending.add("publication_confirmation")
        status = ImportBatchStatus.PENDING_CONFIRMATION if pending else ImportBatchStatus.READY_TO_PUBLISH
        self.store.execute(connection,
            "UPDATE import_batches SET status = ?, pending_confirmation_fields_json = ? WHERE id = ? AND error_count = 0",
            (status.value, json.dumps(sorted(pending)), batch_id))
        report_row = self.store.execute(connection,
            "SELECT report_json FROM import_quality_reports WHERE import_batch_id = ?", (batch_id,)).fetchone()
        if report_row:
            report = json.loads(report_row[0])
            completeness, warnings, capabilities = self._bom_quality(rows, header)
            report.update(status=status.value, pending_confirmation_fields=sorted(pending),
                          confirmations=list(header.confirmations) if header else [],
                          completeness=completeness, warnings=warnings, capabilities=capabilities)
            self.store.execute(connection,
                "UPDATE import_quality_reports SET report_json = ? WHERE import_batch_id = ?",
                (json.dumps(report, ensure_ascii=False), batch_id))

    def confirm_parent_unit(self, batch_id: str, unit: str, *, confirmed_by: str, reason: str) -> ImportBatch:
        if not all(isinstance(value, str) and value.strip() for value in (unit, confirmed_by, reason)):
            raise ValueError("父项单位、确认人和确认依据必须明确提供")
        with self.store.transaction(write_lock=True) as connection:
            query = "SELECT status, error_count FROM import_batches WHERE id = ?"
            if self.store.dialect == "postgresql":
                query += " FOR UPDATE"
            state = self.store.execute(connection, query, (batch_id,)).fetchone()
            if not state:
                raise KeyError("导入批次不存在")
            if state[0] not in {"pending_confirmation", "ready_to_publish"} or state[1]:
                raise ValueError("当前批次不允许人工确认")
            header = self.get_staged_header(batch_id, connection)
            if header is None or not header.publish_parent_item:
                raise ValueError("该批次不支持父项发布")
            confirmations = [*header.confirmations, {"field": "parent_unit", "value": unit.strip(),
                "confirmed_by": confirmed_by.strip(), "reason": reason.strip(),
                "confirmed_at": datetime.now(UTC).isoformat()}]
            self.store.execute(connection,
                "UPDATE staged_bom_headers SET parent_unit = ?, confirmations_json = ? WHERE import_batch_id = ?",
                (unit.strip(), json.dumps(confirmations, ensure_ascii=False), batch_id))
            self._refresh_bom_pending(connection, batch_id)
        return self.get_import_batch(batch_id)

    def get_import_batch(self, batch_id: str) -> ImportBatch | None:
        with self.store.transaction() as connection:
            cursor = self.store.execute(
                connection,
                "SELECT id, mapping_name, status, created_at, published_at, staged_row_count, "
                "error_count, pending_confirmation_fields_json FROM import_batches WHERE id = ?",
                (batch_id,),
            )
            rows = _cursor_dicts(cursor)
        if not rows:
            return None
        return _batch_from_row(rows[0])

    def get_validation_errors(self, batch_id: str) -> list[RowValidationError]:
        with self.store.transaction() as connection:
            cursor = self.store.execute(
                connection,
                "SELECT id, import_batch_id, source_file_id, sheet_name, source_row_number, "
                "error_code, field_name, message, raw_value FROM row_validation_errors "
                "WHERE import_batch_id = ? ORDER BY source_row_number, error_code",
                (batch_id,),
            )
            rows = _cursor_dicts(cursor)
        return [
            RowValidationError(
                id=str(row["id"]),
                import_batch_id=str(row["import_batch_id"]),
                source_file_id=str(row["source_file_id"]),
                sheet_name=str(row["sheet_name"]) if row["sheet_name"] is not None else None,
                source_row_number=(
                    int(row["source_row_number"]) if row["source_row_number"] is not None else None
                ),
                error_code=str(row["error_code"]),
                field_name=str(row["field_name"]) if row["field_name"] is not None else None,
                message=str(row["message"]),
                raw_value=str(row["raw_value"]) if row["raw_value"] is not None else None,
            )
            for row in rows
        ]

    def get_staged_rows(self, batch_id: str) -> list[StagedBomRow]:
        with self.store.transaction() as connection:
            cursor = self.store.execute(
                connection,
                "SELECT id, import_batch_id, source_file_id, sheet_name, source_row_number, "
                "parent_material_code, version, material_code, material_name, specification, "
                "quantity, tax_included_unit_price, source_total_price, unit, currency, "
                "effective_from, validation_status, pending_fields_json "
                "FROM staged_bom_rows WHERE import_batch_id = ? "
                "ORDER BY sheet_name, source_row_number",
                (batch_id,),
            )
            rows = _cursor_dicts(cursor)
        return _staged_rows_from_dicts(rows)

    def apply_confirmations(
        self,
        batch_id: str,
        confirmations: dict[str, object],
        material_code: str | None = None,
        *,
        confirmed_by: str | None = None,
        reason: str | None = None,
    ) -> ImportBatch:
        """把业务人员明确提供的值写入 staging，不生成任何默认值。"""
        allowed = {"unit", "currency", "effective_from"}
        unknown_fields = set(confirmations) - allowed
        if unknown_fields:
            raise ValueError(f"不支持的确认字段：{', '.join(sorted(unknown_fields))}")

        assignments: list[str] = []
        params: list[object] = []
        for field_name in ("unit", "currency", "effective_from"):
            if field_name not in confirmations:
                continue
            value = confirmations[field_name]
            if field_name == "effective_from":
                if isinstance(value, date):
                    value = value.isoformat()
                else:
                    value = date.fromisoformat(str(value)).isoformat()
            elif not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} 必须由业务人员提供非空值")
            assignments.append(f"{field_name} = ?")
            params.append(value.strip() if isinstance(value, str) else value)

        if not assignments:
            batch = self.get_import_batch(batch_id)
            if batch is None:
                raise KeyError("导入批次不存在")
            return batch

        where = "import_batch_id = ? AND validation_status = 'valid'"
        params.append(batch_id)
        if material_code is not None:
            where += " AND material_code = ?"
            params.append(material_code)

        with self.store.transaction(write_lock=True) as connection:
            batch_query = "SELECT status, error_count FROM import_batches WHERE id = ?"
            if self.store.dialect == "postgresql":
                batch_query += " FOR UPDATE"
            batch_cursor = self.store.execute(connection, batch_query, (batch_id,))
            batch_state = batch_cursor.fetchone()
            if batch_state is None:
                raise KeyError("导入批次不存在")
            if str(batch_state[0]) not in {
                ImportBatchStatus.PENDING_CONFIRMATION.value,
                ImportBatchStatus.READY_TO_PUBLISH.value,
            }:
                raise ValueError("当前导入批次状态不允许修改人工确认值")
            if int(batch_state[1]) != 0:
                raise ValueError("存在校验错误的导入批次不能确认并发布")
            header = self.get_staged_header(batch_id, connection)
            if header and header.publish_parent_item and not all(
                isinstance(value, str) and value.strip() for value in (confirmed_by, reason)
            ):
                raise ValueError("企业 BOM 确认须提供确认人和业务依据")
            cursor = self.store.execute(
                connection,
                f"UPDATE staged_bom_rows SET {', '.join(assignments)} WHERE {where}",
                params,
            )
            if cursor.rowcount == 0:
                raise KeyError("未找到可确认的暂存 BOM 行")
            if header and header.publish_parent_item:
                audit = [*header.confirmations, {
                    "fields": {key: str(value) for key, value in confirmations.items()},
                    "material_code": material_code, "confirmed_by": confirmed_by.strip(),
                    "reason": reason.strip(), "confirmed_at": datetime.now(UTC).isoformat(),
                }]
                self.store.execute(connection,
                    "UPDATE staged_bom_headers SET confirmations_json = ? WHERE import_batch_id = ?",
                    (json.dumps(audit, ensure_ascii=False), batch_id))
            self._refresh_bom_pending(connection, batch_id)

        batch = self.get_import_batch(batch_id)
        if batch is None:
            raise KeyError("导入批次不存在")
        return batch

    def confirm_structure_publication(self, batch_id: str, *, confirmed_by: str, reason: str) -> ImportBatch:
        """Authorize structure-only publication; never confirm missing business values."""
        if not all(isinstance(value, str) and value.strip() for value in (confirmed_by, reason)):
            raise ValueError("结构发布须提供授权人及依据")
        with self.store.transaction(write_lock=True) as connection:
            query = "SELECT status, error_count FROM import_batches WHERE id = ?"
            if self.store.dialect == "postgresql":
                query += " FOR UPDATE"
            batch = self.store.execute(connection, query, (batch_id,)).fetchone()
            header = self.get_staged_header(batch_id, connection)
            if batch is None:
                raise KeyError("导入批次不存在")
            if not header or header.publication_scope != "structure":
                raise ValueError("当前映射不是结构发布策略")
            if str(batch[0]) not in {"pending_confirmation", "ready_to_publish"} or int(batch[1]) != 0:
                raise ValueError("批次校验未通过或已经发布")
            audit = [*header.confirmations, dict(fields={"publication_scope": "structure"},
                confirmed_by=confirmed_by.strip(), reason=reason.strip(), confirmed_at=datetime.now(UTC).isoformat())]
            self.store.execute(connection, "UPDATE staged_bom_headers SET confirmations_json = ? WHERE import_batch_id = ?",
                (json.dumps(audit, ensure_ascii=False), batch_id))
            self._refresh_bom_pending(connection, batch_id)
        return self.get_import_batch(batch_id)

    def publish_batch(self, batch_id: str) -> dict[str, object]:
        """在同一加锁事务内复核 staging 并发布；重复版本明确拒绝。"""
        header_id = _new_id()
        rows: list[StagedBomRow]
        parent_material_code: str
        version: str
        with self.store.transaction(write_lock=True) as connection:
            batch_query = (
                "SELECT id, mapping_name, status, created_at, published_at, staged_row_count, "
                "error_count, pending_confirmation_fields_json FROM import_batches WHERE id = ?"
            )
            if self.store.dialect == "postgresql":
                batch_query += " FOR UPDATE"
            batch_cursor = self.store.execute(connection, batch_query, (batch_id,))
            batch_rows = _cursor_dicts(batch_cursor)
            if not batch_rows:
                raise KeyError("导入批次不存在")
            batch = _batch_from_row(batch_rows[0])
            if batch.status != ImportBatchStatus.READY_TO_PUBLISH:
                raise ValueError("导入批次尚未通过校验和人工确认，不能发布")
            if batch.error_count != 0 or batch.pending_confirmation_fields:
                raise ValueError("导入批次仍有校验错误或待确认字段，不能发布")

            staged_cursor = self.store.execute(
                connection,
                "SELECT id, import_batch_id, source_file_id, sheet_name, source_row_number, "
                "parent_material_code, version, material_code, material_name, specification, "
                "quantity, tax_included_unit_price, source_total_price, unit, currency, "
                "effective_from, validation_status, pending_fields_json "
                "FROM staged_bom_rows WHERE import_batch_id = ? "
                "ORDER BY sheet_name, source_row_number",
                (batch_id,),
            )
            rows = _staged_rows_from_dicts(_cursor_dicts(staged_cursor))
            if (
                not rows
                or len(rows) != batch.staged_row_count
                or any(row.validation_status != "valid" for row in rows)
            ):
                raise ValueError("导入批次没有可发布的完整有效 BOM 明细")

            parent_versions = {(row.parent_material_code, row.version) for row in rows}
            if len(parent_versions) != 1:
                raise ValueError("一个导入批次只能发布一个父件和版本")
            parent_value, version_value = next(iter(parent_versions))
            if not parent_value or not version_value:
                raise ValueError("父件或版本未确认")
            parent_material_code = parent_value
            version = version_value
            context = self.get_staged_header(batch_id, connection)
            price_policy = context.price_policy if context else "required"
            unit_policy = context.unit_policy if context else "required"
            scope = context.publication_scope if context else "full"
            if price_policy not in {"required", "optional"} or unit_policy not in {"required", "optional_for_structure"}:
                raise ValueError("暂存发布策略不合法")
            if scope not in {"full", "structure"} or (scope == "structure" and (not context.publish_parent_item or price_policy != "optional" or unit_policy != "optional_for_structure")):
                raise ValueError("结构发布策略必须显式配置，不能放宽核价校验")
            if context and (context.parent_material_code, context.version) != (parent_material_code, version):
                raise ValueError("父项元数据与暂存明细不一致")
            for row in rows:
                source = self.store.execute(connection,
                    "SELECT import_batch_id, original_filename, content_sha256, byte_size, storage_key, source_uri "
                    "FROM source_files WHERE id = ?", (row.source_file_id,)).fetchone()
                if (not source or source[0] != batch_id or not source[1] or
                        len(str(source[2])) != 64 or not all(ch in "0123456789abcdefABCDEF" for ch in str(source[2])) or
                        int(source[3]) <= 0 or not (source[4] or source[5]) or
                        not row.sheet_name or row.source_row_number <= 0):
                    raise ValueError("暂存 BOM 来源审计不完整，不能发布")
            if context and (context.source_file_id != rows[0].source_file_id or
                            context.sheet_name != rows[0].sheet_name or context.source_row_number <= 0):
                raise ValueError("父项来源审计与 BOM 明细不一致")
            if context and context.publish_parent_item:
                if (unit_policy == "required" and not context.parent_unit) or len({row.effective_from for row in rows}) != 1:
                    raise ValueError("父项单位未确认或 BOM 生效日期不一致")
                if unit_policy == "optional_for_structure" and not self._has_publication_confirmation(context, rows[0].effective_from):
                    raise ValueError("结构事实发布须明确授权，并保留确认人与业务依据")
                existing_parent = self.store.execute(connection,
                    "SELECT unit FROM items WHERE material_code = ?", (parent_material_code,)).fetchone()
                if existing_parent and existing_parent[0] is not None and context.parent_unit is not None and existing_parent[0] != context.parent_unit:
                    raise ValueError("父项单位与已发布主数据冲突，不能覆盖")

            if self.store.dialect == "postgresql":
                lock_digest = hashlib.sha256(
                    f"{parent_material_code}\0{version}".encode("utf-8")
                ).digest()
                lock_key = int.from_bytes(lock_digest[:8], byteorder="big", signed=True)
                self.store.execute(
                    connection,
                    "SELECT pg_advisory_xact_lock(?)",
                    (lock_key,),
                )
            existing_cursor = self.store.execute(
                connection,
                "SELECT id FROM bom_headers WHERE parent_material_code = ? AND version = ?",
                (parent_material_code, version),
            )
            if existing_cursor.fetchone() is not None:
                raise ValueError("该父件和版本已发布，不能覆盖受控事实")

            first = rows[0]
            if first.effective_from is None and scope != "structure":
                raise ValueError("生效日期尚未确认")
            if context and context.publish_parent_item:
                self.store.execute(connection,
                    "INSERT INTO items (id, material_code, name, specification, unit, import_batch_id, "
                    "source_file_id, source_sheet, source_row_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(material_code) DO NOTHING",
                    (_new_id(), parent_material_code, None, None, context.parent_unit, batch_id,
                     context.source_file_id, context.sheet_name, context.source_row_number))
                parent_id = self.store.execute(connection,
                    "SELECT id FROM items WHERE material_code = ?", (parent_material_code,)).fetchone()[0]
                self.store.execute(connection,
                    "INSERT INTO item_revisions (id, item_id, name, specification, unit, import_batch_id, "
                    "source_file_id, source_sheet, source_row_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_new_id(), parent_id, None, None, context.parent_unit, batch_id,
                     context.source_file_id, context.sheet_name, context.source_row_number))
            self.store.execute(
                connection,
                "INSERT INTO bom_headers "
                "(id, parent_material_code, version, effective_from, import_batch_id, "
                "source_file_id, source_sheet) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    header_id,
                    parent_material_code,
                    version,
                    _date_text(first.effective_from),
                    batch_id,
                    first.source_file_id,
                    first.sheet_name,
                ),
            )

            for row in rows:
                if (
                    row.material_code is None
                    or row.quantity is None
                    or not row.quantity.is_finite() or row.quantity <= 0
                    or self._row_pending(row, price_policy, unit_policy, scope)
                    or (price_policy == "required" and row.tax_included_unit_price is None)
                    or (row.tax_included_unit_price is not None and
                        (not row.tax_included_unit_price.is_finite() or row.tax_included_unit_price < 0))
                ):
                    raise ValueError("暂存行仍存在未确认字段")
                if context and context.publish_parent_item:
                    previous = self.store.execute(connection,
                        "SELECT unit FROM items WHERE material_code = ?", (row.material_code,)).fetchone()
                    if previous and previous[0] is not None and row.unit is not None and previous[0] != row.unit:
                        raise ValueError("子料单位与已发布主数据冲突，不能静默覆盖")
                proposed_item_id = _new_id()
                self.store.execute(
                    connection,
                    "INSERT INTO items "
                    "(id, material_code, name, specification, unit, import_batch_id, source_file_id, "
                    "source_sheet, source_row_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(material_code) DO NOTHING",
                    (
                        proposed_item_id,
                        row.material_code,
                        row.material_name,
                        row.specification,
                        row.unit,
                        batch_id,
                        row.source_file_id,
                        row.sheet_name,
                        row.source_row_number,
                    ),
                )
                item_cursor = self.store.execute(
                    connection,
                    "SELECT id FROM items WHERE material_code = ?",
                    (row.material_code,),
                )
                item_id = str(item_cursor.fetchone()[0])
                self.store.execute(
                    connection,
                    "INSERT INTO item_revisions "
                    "(id, item_id, name, specification, unit, import_batch_id, source_file_id, "
                    "source_sheet, source_row_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _new_id(),
                        item_id,
                        row.material_name,
                        row.specification,
                        row.unit,
                        batch_id,
                        row.source_file_id,
                        row.sheet_name,
                        row.source_row_number,
                    ),
                )
                self.store.execute(
                    connection,
                    "INSERT INTO bom_lines "
                    "(id, bom_header_id, item_id, quantity, unit, import_batch_id, source_file_id, "
                    "source_sheet, source_row_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _new_id(),
                        header_id,
                        item_id,
                        _decimal_text(row.quantity),
                        row.unit,
                        batch_id,
                        row.source_file_id,
                        row.sheet_name,
                        row.source_row_number,
                    ),
                )
                if row.tax_included_unit_price is not None and scope != "structure":
                    self.store.execute(
                        connection,
                        "INSERT INTO price_records "
                        "(id, item_id, tax_included_unit_price, currency, effective_from, import_batch_id, "
                        "source_file_id, source_sheet, source_row_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (_new_id(), item_id, _decimal_text(row.tax_included_unit_price), row.currency,
                         row.effective_from.isoformat(), batch_id, row.source_file_id,
                         row.sheet_name, row.source_row_number),
                    )

            published_at = datetime.now(UTC).isoformat()
            self.store.execute(
                connection,
                "UPDATE import_batches SET status = ?, published_at = ? WHERE id = ?",
                (ImportBatchStatus.PUBLISHED.value, published_at, batch_id),
            )
            report_row = self.store.execute(connection,
                "SELECT report_json FROM import_quality_reports WHERE import_batch_id = ?", (batch_id,)).fetchone()
            completeness, warnings, capabilities = self._bom_quality(rows, context, published=True)
            publication_status = "published_incomplete" if warnings else "published"
            if report_row:
                quality = json.loads(report_row[0])
                quality.update(status="published", publication_status=publication_status,
                               pending_confirmation_fields=[], completeness=completeness,
                               warnings=warnings, capabilities=capabilities)
                self.store.execute(connection,
                    "UPDATE import_quality_reports SET report_json = ? WHERE import_batch_id = ?",
                    (json.dumps(quality, ensure_ascii=False), batch_id))

        return {
            "import_batch_id": batch_id,
            "bom_header_id": header_id,
            "parent_material_code": parent_material_code,
            "version": version,
            "line_count": len(rows),
            "status": ImportBatchStatus.PUBLISHED.value,
            "publication_status": publication_status,
            "completeness": completeness,
            "capabilities": capabilities,
            "warnings": warnings,
        }

    def get_published_bom(self, parent_material_code: str, version: str) -> dict[str, object] | None:
        with self.store.transaction() as connection:
            header_cursor = self.store.execute(
                connection,
                "SELECT h.id, h.parent_material_code, h.version, h.effective_from, h.import_batch_id, "
                "sf.id AS source_file_id, sf.original_filename, sf.content_sha256, sf.source_kind, "
                "sf.storage_key, sf.source_uri, sf.immutable_copy, h.source_sheet, parent.unit AS confirmed_parent_unit "
                "FROM bom_headers h JOIN source_files sf ON sf.id = h.source_file_id "
                "LEFT JOIN items parent ON parent.material_code=h.parent_material_code "
                "WHERE h.parent_material_code = ? AND h.version = ?",
                (parent_material_code, version),
            )
            headers = _cursor_dicts(header_cursor)
            if not headers:
                return None
            header = headers[0]
            context = self.get_staged_header(str(header["import_batch_id"]), connection)
            parent_unit = header["confirmed_parent_unit"] or (context.parent_unit if context else None)
            line_cursor = self.store.execute(
                connection,
                "SELECT i.material_code, ir.name AS material_name, ir.specification, bl.quantity, "
                "bl.unit, pr.tax_included_unit_price, pr.currency, pr.effective_from, "
                "sf.id AS source_file_id, sf.original_filename AS source_file, "
                "sf.content_sha256, sf.source_kind, sf.storage_key, sf.source_uri, "
                "sf.immutable_copy, bl.source_sheet, bl.source_row_number, bl.import_batch_id "
                "FROM bom_lines bl JOIN items i ON i.id = bl.item_id "
                "JOIN item_revisions ir ON ir.item_id = bl.item_id "
                "AND ir.import_batch_id = bl.import_batch_id "
                "JOIN source_files sf ON sf.id = bl.source_file_id "
                "LEFT JOIN price_records pr ON pr.item_id = bl.item_id "
                "AND pr.import_batch_id = bl.import_batch_id "
                "AND pr.source_row_number = bl.source_row_number "
                "WHERE bl.bom_header_id = ? ORDER BY bl.source_sheet, bl.source_row_number",
                (header["id"],),
            )
            lines = _cursor_dicts(line_cursor)

        for line in lines:
            line["quantity"] = Decimal(str(line["quantity"]))
            line["tax_included_unit_price"] = (Decimal(str(line["tax_included_unit_price"]))
                if line["tax_included_unit_price"] is not None else None)
            line["immutable_copy"] = bool(line["immutable_copy"])
            line["completeness"] = {"unit": line["unit"] is not None,
                                    "price": line["tax_included_unit_price"] is not None}
        completeness = {
            "unit": bool(lines) and all(line["unit"] is not None for line in lines),
            "parent_unit": bool(parent_unit),
            "price": bool(lines) and all(line["tax_included_unit_price"] is not None for line in lines),
        }
        warnings = []
        if not completeness["unit"]:
            warnings.append("unit_missing：BOM数量关系已发布，但计量单位待确认，仅可用于结构查询，不能用于确定性数量计算")
        if context and context.publish_parent_item and not completeness["parent_unit"]:
            warnings.append("parent_unit_missing：成品计量单位待确认，不能进行确定性数量计算")
        if not completeness["price"]:
            warnings.append("price_missing：BOM价格未知，不能计算成本")
        return {
            "header": {
                "id": str(header["id"]),
                "parent_material_code": str(header["parent_material_code"]),
                "version": str(header["version"]),
                "effective_from": _date_text(header["effective_from"]) if isinstance(header["effective_from"], date) else header["effective_from"],
                "import_batch_id": str(header["import_batch_id"]),
                "source_file_id": str(header["source_file_id"]),
                "source_file": str(header["original_filename"]),
                "content_sha256": str(header["content_sha256"]),
                "source_kind": str(header["source_kind"]),
                "storage_key": (
                    str(header["storage_key"]) if header["storage_key"] is not None else None
                ),
                "source_uri": (
                    str(header["source_uri"]) if header["source_uri"] is not None else None
                ),
                "immutable_copy": bool(header["immutable_copy"]),
                "source_sheet": str(header["source_sheet"]),
                "parent_unit": parent_unit,
            },
            "lines": lines,
            "completeness": completeness,
            "warnings": warnings,
            "publication_status": "published_incomplete" if warnings else "published",
        }

    def count_published_bom_headers(self) -> int:
        with self.store.transaction() as connection:
            cursor = self.store.execute(connection, "SELECT COUNT(*) FROM bom_headers")
            return int(cursor.fetchone()[0])

    def get_item_audit_history(self, material_code: str) -> list[dict[str, object]]:
        """返回物料每次发布的不可变主数据快照及其来源。"""
        with self.store.transaction() as connection:
            cursor = self.store.execute(
                connection,
                "SELECT i.material_code, ir.name, ir.specification, ir.unit, ir.import_batch_id, "
                "sf.id AS source_file_id, sf.original_filename AS source_file, sf.content_sha256, "
                "sf.source_kind, sf.storage_key, sf.source_uri, ir.source_sheet, "
                "ir.source_row_number "
                "FROM items i JOIN item_revisions ir ON ir.item_id = i.id "
                "JOIN source_files sf ON sf.id = ir.source_file_id "
                "JOIN import_batches ib ON ib.id = ir.import_batch_id "
                "WHERE i.material_code = ? ORDER BY ib.published_at, ir.id",
                (material_code,),
            )
            return _cursor_dicts(cursor)


__all__ = ["FactRepository"]
