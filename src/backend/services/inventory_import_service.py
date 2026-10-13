"""库存时点事实的受控导入服务。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from fact_layer.models import (
    ImportBatch,
    ImportBatchStatus,
    InventoryImportResult,
    ItemMatchStatus,
    RowValidationError,
    SourceKind,
    StagedInventoryRow,
)
from repositories.fact_repository import FactRepository
from repositories.inventory_fact_repository import InventoryFactRepository
from repositories.inventory_import_repository import (
    EXCLUDED_INVENTORY_SOURCES,
    INVENTORY_SHEET_NAME,
    InventoryWorkbookRepository,
)
from repositories.source_file_storage import ImmutableSourceFileStorage, prepare_source_file


INVENTORY_MAPPING_NAME = "confirmed_main_inventory_sheet_v1"
UNAVAILABLE_INVENTORY_FIELDS = (
    "warehouse",
    "available_qty",
    "reserved_qty",
    "quality_status",
    "received_at",
    "expiry_date",
)


def _new_id() -> str:
    return str(uuid4())


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


def _quantity(value: object) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise InvalidOperation
    try:
        quantity = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise InvalidOperation from error
    if not quantity.is_finite():
        raise InvalidOperation
    return quantity


def _aware_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class InventoryImportService:
    """读取唯一获准库存表，校验后暂存；发布前要求时点和 item 关联完整。"""

    def __init__(
        self,
        fact_repository: FactRepository,
        workbook_repository: InventoryWorkbookRepository | None = None,
    ) -> None:
        self.fact_repository = fact_repository
        self.inventory_repository = InventoryFactRepository(fact_repository)
        self.workbook_repository = workbook_repository or InventoryWorkbookRepository()

    def stage_inventory(
        self,
        file_path: str | Path,
        *,
        snapshot_at: datetime | None = None,
        observed_at: datetime | None = None,
        source_kind: SourceKind | str = SourceKind.LOCAL,
        source_uri: str | None = None,
        immutable_storage: ImmutableSourceFileStorage | None = None,
        material_scope: set[str] | None = None,
    ) -> InventoryImportResult:
        if material_scope is not None and not material_scope:
            raise ValueError("库存导入范围不能为空")
        observed = _aware_datetime(observed_at) or datetime.now(UTC)
        snapshot = _aware_datetime(snapshot_at)
        prepared = prepare_source_file(
            file_path,
            source_kind,
            source_uri=source_uri,
            immutable_storage=immutable_storage,
        )
        batch, source_file = self.fact_repository.create_staging_batch(
            mapping_name=INVENTORY_MAPPING_NAME,
            original_filename=prepared.original_filename,
            content_sha256=prepared.content_sha256,
            byte_size=prepared.byte_size,
            source_kind=prepared.source_kind,
            storage_key=prepared.storage_key,
            source_uri=prepared.source_uri,
            immutable_copy=prepared.immutable_copy,
        )
        try:
            mapped = self.workbook_repository.map_workbook(prepared.read_path)
        except Exception as error:
            validation_error = self._error(
                batch.id,
                source_file.id,
                None,
                None,
                "SOURCE_FILE_READ_FAILED",
                None,
                "库存源工作簿无法读取",
                type(error).__name__,
            )
            quality = self._quality_report(
                0, [], [validation_error], set(), set(), set(), set(), 0
            )
            self.inventory_repository.save_staging_result(
                batch.id,
                source_file.id,
                INVENTORY_SHEET_NAME,
                observed,
                snapshot,
                EXCLUDED_INVENTORY_SOURCES,
                0,
                0,
                [],
                [validation_error],
                ImportBatchStatus.INVALID,
                (),
                quality,
                {},
            )
            return InventoryImportResult(
                batch.id,
                ImportBatchStatus.INVALID,
                0,
                0,
                1,
                0,
                0,
                0,
                errors=(validation_error,),
            )

        errors: list[RowValidationError] = []
        if mapped.detected_sheet is None:
            errors.append(
                self._error(
                    batch.id,
                    source_file.id,
                    None,
                    None,
                    "INVENTORY_SHEET_NOT_FOUND",
                    None,
                    "未找到已确认的一期库存事实工作表",
                    INVENTORY_SHEET_NAME,
                )
            )

        selected_rows = tuple(row for row in mapped.rows if material_scope is None
            or _text(row.values.get("material_code")) is None
            or _text(row.values.get("material_code")) in material_scope)
        cleaned_codes = {
            code
            for row in selected_rows
            if (code := _text(row.values.get("material_code"))) is not None
        }
        item_ids = self.inventory_repository.resolve_item_ids(cleaned_codes)
        staged_rows: list[StagedInventoryRow] = []
        raw_payloads: dict[str, dict[str, object]] = {}
        exact_keys: set[tuple[object, ...]] = set()
        duplicate_count = 0
        unique_codes: set[str] = set()
        matched_codes: set[str] = set()
        unmatched_codes: set[str] = set()
        batches: set[str] = set()
        locations: set[str] = set()

        for mapped_row in selected_rows:
            row_errors: list[RowValidationError] = []
            material_code = _text(mapped_row.values.get("material_code"))
            if not material_code:
                row_errors.append(
                    self._error(
                        batch.id,
                        source_file.id,
                        mapped_row.sheet_name,
                        mapped_row.source_row_number,
                        "MATERIAL_CODE_REQUIRED",
                        "material_code",
                        "库存明细缺少料号",
                        None,
                    )
                )
            on_hand_qty: Decimal | None = None
            try:
                on_hand_qty = _quantity(mapped_row.values.get("on_hand_qty"))
                if on_hand_qty is None or on_hand_qty < 0:
                    raise InvalidOperation
            except InvalidOperation:
                row_errors.append(
                    self._error(
                        batch.id,
                        source_file.id,
                        mapped_row.sheet_name,
                        mapped_row.source_row_number,
                        "ON_HAND_QTY_INVALID",
                        "on_hand_qty",
                        "库存数量必须是非负确定性数值",
                        _text(mapped_row.values.get("on_hand_qty")),
                    )
                )
                on_hand_qty = None

            optional_quantities = {}
            for field in ("available_qty", "reserved_qty"):
                try:
                    value = _quantity(mapped_row.values.get(field))
                    if value is not None and value < 0:
                        raise InvalidOperation
                    optional_quantities[field] = value
                except InvalidOperation:
                    optional_quantities[field] = None
                    row_errors.append(self._error(batch.id, source_file.id,
                        mapped_row.sheet_name, mapped_row.source_row_number,
                        f"{field.upper()}_INVALID", field,
                        "明确提供的库存数量必须是非负确定性数值",
                        _text(mapped_row.values.get(field))))

            site = _text(mapped_row.values.get("site"))
            location = _text(mapped_row.values.get("location"))
            batch_no = _text(mapped_row.values.get("batch_no"))
            item_id = item_ids.get(material_code or "")
            match_status = (
                ItemMatchStatus.MATCHED if item_id else ItemMatchStatus.UNMATCHED
            )
            exact_key = (material_code, site, location, batch_no, on_hand_qty)
            is_duplicate = exact_key in exact_keys
            if is_duplicate:
                duplicate_count += 1
            else:
                exact_keys.add(exact_key)
            row_id = _new_id()
            staged_rows.append(
                StagedInventoryRow(
                    id=row_id,
                    import_batch_id=batch.id,
                    source_file_id=source_file.id,
                    sheet_name=mapped_row.sheet_name,
                    source_row_number=mapped_row.source_row_number,
                    raw_material_code=material_code,
                    item_id=item_id,
                    match_status=match_status,
                    site=site,
                    warehouse=None,
                    location=location,
                    batch_no=batch_no,
                    on_hand_qty=on_hand_qty,
                    available_qty=optional_quantities["available_qty"],
                    reserved_qty=optional_quantities["reserved_qty"],
                    quality_status=None,
                    received_at=None,
                    expiry_date=None,
                    validation_status="invalid" if row_errors else "valid",
                    is_duplicate=is_duplicate,
                )
            )
            raw_payloads[row_id] = dict(mapped_row.values)
            errors.extend(row_errors)
            if material_code:
                unique_codes.add(material_code)
                (matched_codes if item_id else unmatched_codes).add(material_code)
            if batch_no:
                batches.add(batch_no)
            if location:
                locations.add(location)

        pending: list[str] = []
        if snapshot is None:
            pending.append("snapshot_at")
        if unmatched_codes:
            pending.append("item_mapping")
        if errors:
            status = ImportBatchStatus.INVALID
        elif pending:
            status = ImportBatchStatus.PENDING_CONFIRMATION
        else:
            status = ImportBatchStatus.READY_TO_PUBLISH
        valid_count = sum(row.validation_status == "valid" for row in staged_rows)
        quality = self._quality_report(
            mapped.source_row_count,
            staged_rows,
            errors,
            unique_codes,
            matched_codes,
            unmatched_codes,
            batches,
            len(locations),
        )
        if material_scope is not None:
            quality["scope"] = {
                "material_codes": sorted(material_scope),
                "expected_material_count": len(material_scope),
                "source_matched_material_count": len(material_scope & unique_codes),
                "missing_material_codes": sorted(material_scope - unique_codes),
                "selected_row_count": len(selected_rows),
                "excluded_out_of_scope_row_count": len(mapped.rows) - len(selected_rows),
                "is_full_workbook_import": False,
            }
        self.inventory_repository.save_staging_result(
            batch.id,
            source_file.id,
            mapped.detected_sheet or INVENTORY_SHEET_NAME,
            observed,
            snapshot,
            mapped.excluded_sources,
            mapped.source_row_count,
            duplicate_count,
            staged_rows,
            errors,
            status,
            tuple(pending),
            quality,
            raw_payloads,
        )
        return InventoryImportResult(
            import_batch_id=batch.id,
            status=status,
            staged_row_count=len(staged_rows),
            valid_row_count=valid_count,
            error_count=len(errors),
            matched_item_count=len(matched_codes),
            unmatched_item_count=len(unmatched_codes),
            duplicate_row_count=duplicate_count,
            pending_confirmation_fields=tuple(pending),
            errors=tuple(errors),
        )

    def confirm_snapshot_at(self, batch_id: str, snapshot_at: datetime) -> ImportBatch:
        return self.inventory_repository.set_snapshot_at(batch_id, _aware_datetime(snapshot_at))

    def refresh_item_matches(self, batch_id: str) -> ImportBatch:
        return self.inventory_repository.refresh_item_matches(batch_id)

    def publish(self, batch_id: str) -> dict[str, object]:
        return self.inventory_repository.publish(batch_id)

    def get_inventory_as_of(
        self, material_code: str, as_of: datetime
    ) -> dict[str, object] | None:
        return self.inventory_repository.get_inventory_as_of(
            material_code, _aware_datetime(as_of)
        )

    def get_quality_report(self, batch_id: str) -> dict[str, object] | None:
        return self.inventory_repository.get_quality_report(batch_id)

    @staticmethod
    def _quality_report(
        source_row_count: int,
        rows: list[StagedInventoryRow],
        errors: list[RowValidationError],
        unique_codes: set[str],
        matched_codes: set[str],
        unmatched_codes: set[str],
        batches: set[str],
        location_count: int,
    ) -> dict[str, object]:
        return {
            "fact_type": "inventory_snapshot",
            "source_sheet": INVENTORY_SHEET_NAME,
            "raw_row_count": source_row_count,
            "valid_inventory_fact_count": sum(
                row.validation_status == "valid" for row in rows
            ),
            "unique_material_count": len(unique_codes),
            "batch_count": len(batches),
            "location_count": location_count,
            "item_master_matched_count": len(matched_codes),
            "unmatched_material_count": len(unmatched_codes),
            "duplicate_row_count": sum(row.is_duplicate for row in rows),
            "invalid_row_count": len(
                {error.source_row_number for error in errors if error.source_row_number is not None}
            ),
            "excluded_data_sources": list(EXCLUDED_INVENTORY_SOURCES),
            "unavailable_fields": [field for field in UNAVAILABLE_INVENTORY_FIELDS
                if not rows or any(getattr(row, field) is None for row in rows)],
            "inbound_facts_available": False,
            "assumptions": [
                "库存表是一期唯一库存事实来源",
                "未提供的可用量和预留量保持 unknown，不从其他列推断",
                "重复明细只标记并保留，不在导入阶段聚合或覆盖",
            ],
        }

    @staticmethod
    def _error(
        batch_id: str,
        source_file_id: str,
        sheet_name: str | None,
        source_row_number: int | None,
        error_code: str,
        field_name: str | None,
        message: str,
        raw_value: str | None,
    ) -> RowValidationError:
        return RowValidationError(
            id=_new_id(),
            import_batch_id=batch_id,
            source_file_id=source_file_id,
            sheet_name=sheet_name,
            source_row_number=source_row_number,
            error_code=error_code,
            field_name=field_name,
            message=message,
            raw_value=raw_value,
        )


__all__ = [
    "INVENTORY_MAPPING_NAME",
    "UNAVAILABLE_INVENTORY_FIELDS",
    "InventoryImportService",
]
