"""可靠采购订单来源的在途事实导入服务。"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from fact_layer.models import (
    ImportBatchStatus,
    InboundImportResult,
    InboundSupplyInputRow,
    ItemMatchStatus,
    RowValidationError,
    SourceKind,
    StagedInboundSupplyRow,
)
from repositories.fact_repository import FactRepository
from repositories.inbound_supply_fact_repository import InboundSupplyFactRepository
from repositories.inventory_fact_repository import InventoryFactRepository
from repositories.source_file_storage import ImmutableSourceFileStorage, prepare_source_file


INBOUND_MAPPING_NAME = "authoritative_inbound_supply_v1"


def _new_id() -> str:
    return str(uuid4())


def _text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _decimal(value: object, *, required: bool) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise InvalidOperation
        return None
    if isinstance(value, bool):
        raise InvalidOperation
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise InvalidOperation from error
    if not parsed.is_finite():
        raise InvalidOperation
    return parsed


def _date(value: object) -> date | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


class InboundSupplyImportService:
    """仅接受明确的标准化采购订单行，不读取现有库存扩展列。"""

    def __init__(self, fact_repository: FactRepository) -> None:
        self.fact_repository = fact_repository
        self.repository = InboundSupplyFactRepository(fact_repository)
        self.item_repository = InventoryFactRepository(fact_repository)

    def stage_rows(
        self,
        source_path: str | Path,
        source_name: str,
        rows: Iterable[InboundSupplyInputRow],
        *,
        observed_at: datetime | None = None,
        source_kind: SourceKind | str = SourceKind.LOCAL,
        source_uri: str | None = None,
        immutable_storage: ImmutableSourceFileStorage | None = None,
    ) -> InboundImportResult:
        observed = observed_at or datetime.now(UTC)
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        prepared = prepare_source_file(
            source_path,
            source_kind,
            source_uri=source_uri,
            immutable_storage=immutable_storage,
        )
        batch, source_file = self.fact_repository.create_staging_batch(
            mapping_name=INBOUND_MAPPING_NAME,
            original_filename=prepared.original_filename,
            content_sha256=prepared.content_sha256,
            byte_size=prepared.byte_size,
            source_kind=prepared.source_kind,
            storage_key=prepared.storage_key,
            source_uri=prepared.source_uri,
            immutable_copy=prepared.immutable_copy,
        )
        input_rows = list(rows)
        codes = {
            code
            for row in input_rows
            if (code := _text(row.material_code)) is not None
        }
        item_ids = self.item_repository.resolve_item_ids(codes)
        staged_rows: list[StagedInboundSupplyRow] = []
        errors: list[RowValidationError] = []
        matched: set[str] = set()
        unmatched: set[str] = set()

        for source_row in input_rows:
            row_errors: list[RowValidationError] = []
            material_code = _text(source_row.material_code)
            if not material_code:
                row_errors.append(
                    self._error(
                        batch.id,
                        source_file.id,
                        source_row,
                        "MATERIAL_CODE_REQUIRED",
                        "material_code",
                        "在途明细缺少料号",
                        None,
                    )
                )
            ordered_qty: Decimal | None = None
            try:
                ordered_qty = _decimal(source_row.ordered_qty, required=True)
                if ordered_qty is None or ordered_qty <= 0:
                    raise InvalidOperation
            except InvalidOperation:
                row_errors.append(
                    self._error(
                        batch.id,
                        source_file.id,
                        source_row,
                        "ORDERED_QTY_INVALID",
                        "ordered_qty",
                        "订购数量必须是大于 0 的确定性数值",
                        _text(source_row.ordered_qty),
                    )
                )
                ordered_qty = None
            received_qty: Decimal | None = None
            try:
                received_qty = _decimal(source_row.received_qty, required=False)
                if received_qty is not None and received_qty < 0:
                    raise InvalidOperation
                if (
                    received_qty is not None
                    and ordered_qty is not None
                    and received_qty > ordered_qty
                ):
                    raise InvalidOperation
            except InvalidOperation:
                row_errors.append(
                    self._error(
                        batch.id,
                        source_file.id,
                        source_row,
                        "RECEIVED_QTY_INVALID",
                        "received_qty",
                        "已收数量必须是非负数且不能大于订购数量",
                        _text(source_row.received_qty),
                    )
                )
                received_qty = None
            in_transit_qty = (
                ordered_qty - received_qty
                if ordered_qty is not None and received_qty is not None
                else None
            )
            dates: dict[str, date | None] = {}
            for field_name, raw_value in (
                ("expected_arrival_date", source_row.expected_arrival_date),
                ("committed_date", source_row.committed_date),
            ):
                try:
                    dates[field_name] = _date(raw_value)
                except ValueError:
                    dates[field_name] = None
                    row_errors.append(
                        self._error(
                            batch.id,
                            source_file.id,
                            source_row,
                            "DATE_INVALID",
                            field_name,
                            "在途日期字段无法识别",
                            _text(raw_value),
                        )
                    )
            item_id = item_ids.get(material_code or "")
            if material_code:
                (matched if item_id else unmatched).add(material_code)
            staged_rows.append(
                StagedInboundSupplyRow(
                    id=_new_id(),
                    import_batch_id=batch.id,
                    source_file_id=source_file.id,
                    sheet_name=source_row.sheet_name,
                    source_row_number=source_row.source_row_number,
                    raw_material_code=material_code,
                    item_id=item_id,
                    match_status=(
                        ItemMatchStatus.MATCHED if item_id else ItemMatchStatus.UNMATCHED
                    ),
                    supplier_code=_text(source_row.supplier_code),
                    order_no=_text(source_row.order_no),
                    ordered_qty=ordered_qty,
                    received_qty=received_qty,
                    in_transit_qty=in_transit_qty,
                    expected_arrival_date=dates["expected_arrival_date"],
                    committed_date=dates["committed_date"],
                    supply_status=_text(source_row.status),
                    validation_status="invalid" if row_errors else "valid",
                )
            )
            errors.extend(row_errors)

        pending = ("item_mapping",) if unmatched else ()
        if errors:
            status = ImportBatchStatus.INVALID
        elif pending:
            status = ImportBatchStatus.PENDING_CONFIRMATION
        else:
            status = ImportBatchStatus.READY_TO_PUBLISH
        quality_report = {
            "fact_type": "inbound_supply",
            "raw_row_count": len(input_rows),
            "valid_fact_count": sum(row.validation_status == "valid" for row in staged_rows),
            "item_master_matched_count": len(matched),
            "unmatched_material_count": len(unmatched),
            "invalid_row_count": len(
                {error.source_row_number for error in errors if error.source_row_number is not None}
            ),
            "real_enterprise_source_available": False,
            "assumptions": [
                "in_transit_qty 仅在 ordered_qty 与 received_qty 均明确时计算",
                "received_qty 缺失时保持 unknown，不默认成 0",
            ],
        }
        self.repository.save_staging_result(
            batch.id,
            source_file.id,
            observed,
            source_name,
            staged_rows,
            errors,
            status,
            pending,
            quality_report,
        )
        return InboundImportResult(
            import_batch_id=batch.id,
            status=status,
            staged_row_count=len(staged_rows),
            valid_row_count=sum(row.validation_status == "valid" for row in staged_rows),
            error_count=len(errors),
            matched_item_count=len(matched),
            unmatched_item_count=len(unmatched),
            pending_confirmation_fields=pending,
            errors=tuple(errors),
        )

    def publish(self, batch_id: str) -> dict[str, object]:
        return self.repository.publish(batch_id)

    def refresh_item_matches(self, batch_id: str) -> ImportBatchStatus:
        return self.repository.refresh_item_matches(batch_id)

    def get_quality_report(self, batch_id: str) -> dict[str, object] | None:
        return self.repository.get_quality_report(batch_id)

    @staticmethod
    def _error(
        batch_id: str,
        source_file_id: str,
        source_row: InboundSupplyInputRow,
        code: str,
        field: str,
        message: str,
        raw_value: str | None,
    ) -> RowValidationError:
        return RowValidationError(
            id=_new_id(),
            import_batch_id=batch_id,
            source_file_id=source_file_id,
            sheet_name=source_row.sheet_name,
            source_row_number=source_row.source_row_number,
            error_code=code,
            field_name=field,
            message=message,
            raw_value=raw_value,
        )


__all__ = ["INBOUND_MAPPING_NAME", "InboundSupplyImportService"]
