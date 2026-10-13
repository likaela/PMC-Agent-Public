"""企业 BOM 导入、暂存确认与受控发布服务。"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from fact_layer.models import (
    ImportBatch,
    ImportBatchStatus,
    ImportResult,
    RowValidationError,
    SourceKind,
    StagedBomHeader,
    StagedBomRow,
)
from repositories.bom_import_repository import (
    BomImportMapping,
    BomWorkbookRepository,
    DEFAULT_3588M_MAPPING,
    MappedBomRow,
    load_bom_import_mapping,
)
from repositories.fact_repository import FactRepository
from repositories.source_file_storage import ImmutableSourceFileStorage, prepare_source_file


def _new_id() -> str:
    return str(uuid4())


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


def _decimal(value: object) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
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


def _effective_date(value: object) -> date | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


class BomImportService:
    """把 Excel 源数据分阶段写入 staging，再由人工确认后原子发布。"""

    def __init__(
        self,
        fact_repository: FactRepository,
        workbook_repository: BomWorkbookRepository | None = None,
    ) -> None:
        self.fact_repository = fact_repository
        self.workbook_repository = workbook_repository or BomWorkbookRepository()

    def stage_bom(
        self,
        file_path: str | Path,
        mapping_path: str | Path = DEFAULT_3588M_MAPPING,
        *,
        source_kind: SourceKind | str = SourceKind.LOCAL,
        source_uri: str | None = None,
        immutable_storage: ImmutableSourceFileStorage | None = None,
    ) -> ImportResult:
        mapping = load_bom_import_mapping(mapping_path)
        prepared_source = prepare_source_file(
            file_path,
            source_kind,
            source_uri=source_uri,
            immutable_storage=immutable_storage,
        )
        batch, source_file = self.fact_repository.create_staging_batch(
            mapping_name=mapping.mapping_name,
            original_filename=prepared_source.original_filename,
            content_sha256=prepared_source.content_sha256,
            byte_size=prepared_source.byte_size,
            source_kind=prepared_source.source_kind,
            storage_key=prepared_source.storage_key,
            source_uri=prepared_source.source_uri,
            immutable_copy=prepared_source.immutable_copy,
        )

        try:
            mapped = self.workbook_repository.map_workbook(prepared_source.read_path, mapping)
        except Exception as error:
            validation_error = self._validation_error(
                batch.id,
                source_file.id,
                None,
                None,
                "SOURCE_FILE_READ_FAILED",
                None,
                "源工作簿无法按所选映射读取",
                type(error).__name__,
            )
            self.fact_repository.save_staging_result(
                batch.id,
                [],
                [validation_error],
                ImportBatchStatus.INVALID,
                (),
            )
            return ImportResult(
                import_batch_id=batch.id,
                status=ImportBatchStatus.INVALID,
                staged_row_count=0,
                error_count=1,
                errors=(validation_error,),
            )

        errors: list[RowValidationError] = []
        if not mapped.detected_sheets:
            errors.append(
                self._validation_error(
                    batch.id,
                    source_file.id,
                    None,
                    None,
                    "MAPPING_HEADER_NOT_FOUND",
                    None,
                    "未找到同时包含映射必需字段的工作表",
                    None,
                )
            )

        for sheet_name in mapped.sheets_without_metadata:
            sheet_rows = [row for row in mapped.rows if row.sheet_name == sheet_name]
            missing_fields: set[str] = set()
            for row in sheet_rows[:1]:
                if not row.parent_material_code:
                    missing_fields.add("parent_material_code")
                if not row.version:
                    missing_fields.add("version")
            if not sheet_rows:
                missing_fields.update(("parent_material_code", "version"))
            for field_name in sorted(missing_fields):
                errors.append(
                    self._validation_error(
                        batch.id,
                        source_file.id,
                        sheet_name,
                        None,
                        "METADATA_REQUIRED",
                        field_name,
                        f"工作表缺少必需元数据：{field_name}",
                        None,
                    )
                )

        staged_rows: list[StagedBomRow] = []
        raw_payloads: dict[str, dict[str, object]] = {}
        duplicate_keys: set[tuple[str | None, str | None, str]] = set()
        all_pending_fields: set[str] = set()
        tolerance = Decimal(mapping.total_price_tolerance)

        for mapped_row in mapped.rows:
            staged_row, row_errors = self._validate_row(
                batch.id,
                source_file.id,
                mapped_row,
                mapping,
                tolerance,
                duplicate_keys,
            )
            if (
                mapped_row.sheet_name in mapped.sheets_without_metadata
                and staged_row.validation_status == "valid"
            ):
                staged_row = replace(staged_row, validation_status="invalid")
            staged_rows.append(staged_row)
            errors.extend(row_errors)
            if staged_row.validation_status == "valid":
                all_pending_fields.update(staged_row.pending_fields)
            raw_payloads[staged_row.id] = {
                key: value for key, value in mapped_row.values.items()
            }

        header = None
        identities = {(row.parent_material_code, row.version) for row in mapped.rows}
        if mapped.rows and len(identities) == 1 and all(next(iter(identities))):
            first = mapped.rows[0]
            header = StagedBomHeader(
                import_batch_id=batch.id, source_file_id=source_file.id,
                sheet_name=first.sheet_name,
                source_row_number=first.metadata_row_number or first.source_row_number,
                parent_material_code=first.parent_material_code, version=first.version,
                price_policy=mapping.price_policy,
                unit_policy=mapping.unit_policy,
                publication_scope=mapping.publication_scope,
                publish_parent_item=mapping.publish_parent_item,
            )
            if mapping.publish_parent_item and mapping.unit_policy == "required":
                all_pending_fields.add("parent_unit")
            if mapping.unit_policy == "optional_for_structure" and mapping.publish_parent_item:
                all_pending_fields.add("publication_confirmation")
        elif mapping.publish_parent_item or mapping.price_policy == "optional":
            errors.append(self._validation_error(
                batch.id, source_file.id, None, None, "HEADER_IDENTITY_REQUIRED",
                "parent_material_code", "父项发布要求唯一、完整的产品和版本元数据", None,
            ))

        if errors:
            status = ImportBatchStatus.INVALID
        elif all_pending_fields:
            status = ImportBatchStatus.PENDING_CONFIRMATION
        else:
            status = ImportBatchStatus.READY_TO_PUBLISH
        pending_fields = tuple(sorted(all_pending_fields))
        self.fact_repository.save_staging_result(
            batch.id,
            staged_rows,
            errors,
            status,
            pending_fields,
            raw_payloads,
            header,
        )
        return ImportResult(
            import_batch_id=batch.id,
            status=status,
            staged_row_count=len(staged_rows),
            error_count=len(errors),
            pending_confirmation_fields=pending_fields,
            errors=tuple(errors),
            warnings=tuple([
                *(["unit_missing：BOM 计量单位待确认，仅支持结构查询，不能用于确定性数量计算"]
                  if any(row.unit is None for row in staged_rows) or
                     (mapping.publish_parent_item and header is not None and header.parent_unit is None) else []),
                *(["price_missing：BOM 部分或全部价格未知，未提供价格的行不会创建价格事实"]
                  if any(row.tax_included_unit_price is None for row in staged_rows) else []),
                *(["price_unpublished：结构发布保留来源金额，但不创建用于成本计算的价格事实"]
                  if mapping.publication_scope == "structure" and any(row.tax_included_unit_price is not None for row in staged_rows) else []),
                *(["effective_date_missing：生效日期未确认，不能声明当前有效"]
                  if any(row.effective_from is None for row in staged_rows) else []),
            ]),
            completeness={"price": mapping.publication_scope != "structure" and bool(staged_rows) and all(
                row.tax_included_unit_price is not None for row in staged_rows),
                "unit": bool(staged_rows) and all(row.unit is not None for row in staged_rows),
                "parent_unit": bool(header and header.parent_unit),
                "effective_from": bool(staged_rows) and all(row.effective_from is not None for row in staged_rows)},
        )

    def confirm_pending_fields(
        self,
        batch_id: str,
        confirmations: dict[str, object],
        material_code: str | None = None,
        *,
        confirmed_by: str | None = None,
        reason: str | None = None,
    ) -> ImportBatch:
        """保存人工确认值；缺失值不会由程序默认或推断。"""
        return self.fact_repository.apply_confirmations(
            batch_id, confirmations, material_code, confirmed_by=confirmed_by, reason=reason,
        )

    def confirm_parent_unit(self, batch_id: str, unit: str, *, confirmed_by: str, reason: str) -> ImportBatch:
        return self.fact_repository.confirm_parent_unit(
            batch_id, unit, confirmed_by=confirmed_by, reason=reason,
        )

    def publish(self, batch_id: str) -> dict[str, object]:
        return self.fact_repository.publish_batch(batch_id)

    def get_published_bom(self, parent_material_code: str, version: str) -> dict[str, object] | None:
        return self.fact_repository.get_published_bom(parent_material_code, version)

    def _validate_row(
        self,
        batch_id: str,
        source_file_id: str,
        mapped_row: MappedBomRow,
        mapping: BomImportMapping,
        total_price_tolerance: Decimal,
        duplicate_keys: set[tuple[str | None, str | None, str]],
    ) -> tuple[StagedBomRow, list[RowValidationError]]:
        values = mapped_row.values
        errors: list[RowValidationError] = []
        material_code = _clean_text(values.get("material_code"))
        material_name = _clean_text(values.get("material_name"))
        specification = _clean_text(values.get("specification"))

        if not material_code:
            errors.append(
                self._validation_error(
                    batch_id,
                    source_file_id,
                    mapped_row.sheet_name,
                    mapped_row.source_row_number,
                    "MATERIAL_CODE_REQUIRED",
                    "material_code",
                    "BOM 明细缺少料号",
                    _clean_text(values.get("material_code")),
                )
            )

        quantity: Decimal | None = None
        try:
            quantity = _decimal(values.get("quantity"))
            if quantity is None or quantity <= 0:
                raise InvalidOperation
        except InvalidOperation:
            errors.append(
                self._validation_error(
                    batch_id,
                    source_file_id,
                    mapped_row.sheet_name,
                    mapped_row.source_row_number,
                    "QUANTITY_MUST_BE_POSITIVE",
                    "quantity",
                    "BOM 数量必须是大于 0 的数值",
                    _clean_text(values.get("quantity")),
                )
            )
            quantity = None

        priced = (mapping.price_policy == "required"
                  or _clean_text(values.get("tax_included_unit_price")) is not None
                  or _clean_text(values.get("total_price")) is not None)
        unit_price: Decimal | None = None
        try:
            unit_price = _decimal(values.get("tax_included_unit_price"))
            if (priced and unit_price is None) or (unit_price is not None and unit_price < 0):
                raise InvalidOperation
        except InvalidOperation:
            errors.append(
                self._validation_error(
                    batch_id,
                    source_file_id,
                    mapped_row.sheet_name,
                    mapped_row.source_row_number,
                    "UNIT_PRICE_INVALID",
                    "tax_included_unit_price",
                    "含税单价必须是非负数值",
                    _clean_text(values.get("tax_included_unit_price")),
                )
            )
            unit_price = None

        total_price: Decimal | None = None
        try:
            total_price = _decimal(values.get("total_price"))
            if (priced and total_price is None) or (total_price is not None and total_price < 0):
                raise InvalidOperation
        except InvalidOperation:
            errors.append(
                self._validation_error(
                    batch_id,
                    source_file_id,
                    mapped_row.sheet_name,
                    mapped_row.source_row_number,
                    "TOTAL_PRICE_INVALID",
                    "total_price",
                    "总价必须是非负数值并且只用于审计校验",
                    _clean_text(values.get("total_price")),
                )
            )
            total_price = None

        if quantity is not None and unit_price is not None and total_price is not None:
            expected_total = quantity * unit_price
            if abs(expected_total - total_price) > total_price_tolerance:
                errors.append(
                    self._validation_error(
                        batch_id,
                        source_file_id,
                        mapped_row.sheet_name,
                        mapped_row.source_row_number,
                        "TOTAL_PRICE_MISMATCH",
                        "total_price",
                        "总价与数量×含税单价不一致",
                        _clean_text(values.get("total_price")),
                    )
                )

        if material_code:
            duplicate_key = (mapped_row.parent_material_code, mapped_row.version, material_code)
            if duplicate_key in duplicate_keys:
                errors.append(
                    self._validation_error(
                        batch_id,
                        source_file_id,
                        mapped_row.sheet_name,
                        mapped_row.source_row_number,
                        "DUPLICATE_BOM_LINE",
                        "material_code",
                        "同一父件和版本中存在重复 BOM 料号",
                        material_code,
                    )
                )
            else:
                duplicate_keys.add(duplicate_key)

        pending_fields: list[str] = []
        unit = _clean_text(values.get("unit"))
        currency = _clean_text(values.get("currency"))
        effective_from: date | None = None
        if not unit and mapping.unit_policy == "required":
            pending_fields.append("unit")
        if priced and not currency and "currency" in mapping.pending_when_absent and mapping.publication_scope != "structure":
            pending_fields.append("currency")
        try:
            effective_from = _effective_date(values.get("effective_from"))
        except ValueError:
            errors.append(
                self._validation_error(
                    batch_id,
                    source_file_id,
                    mapped_row.sheet_name,
                    mapped_row.source_row_number,
                    "EFFECTIVE_DATE_INVALID",
                    "effective_from",
                    "有效期必须是可识别的日期",
                    _clean_text(values.get("effective_from")),
                )
            )
        if effective_from is None and "effective_from" in mapping.pending_when_absent and mapping.publication_scope != "structure":
            pending_fields.append("effective_from")

        staged_row = StagedBomRow(
            id=_new_id(),
            import_batch_id=batch_id,
            source_file_id=source_file_id,
            sheet_name=mapped_row.sheet_name,
            source_row_number=mapped_row.source_row_number,
            parent_material_code=mapped_row.parent_material_code,
            version=mapped_row.version,
            material_code=material_code,
            material_name=material_name,
            specification=specification,
            quantity=quantity,
            tax_included_unit_price=unit_price,
            source_total_price=total_price,
            unit=unit,
            currency=currency,
            effective_from=effective_from,
            validation_status="invalid" if errors else "valid",
            pending_fields=tuple(pending_fields),
        )
        return staged_row, errors

    @staticmethod
    def _validation_error(
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


__all__ = ["BomImportService"]
