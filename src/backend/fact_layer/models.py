"""企业事实层的最小领域模型。

这些模型只描述导入、暂存和发布事实，不参与现有 PMC 业务计算。
金额和数量使用 :class:`decimal.Decimal`，避免浮点数破坏审计精度。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class ImportBatchStatus(StrEnum):
    """导入批次生命周期。"""

    STAGING = "staging"
    INVALID = "invalid"
    PENDING_CONFIRMATION = "pending_confirmation"
    READY_TO_PUBLISH = "ready_to_publish"
    PUBLISHED = "published"


class SourceKind(StrEnum):
    """源文件进入事实层的受控方式。"""

    UPLOAD = "upload"
    LOCAL = "local"
    ERP = "erp"


@dataclass(frozen=True)
class ImportBatch:
    id: str
    mapping_name: str
    status: ImportBatchStatus
    created_at: datetime
    published_at: datetime | None = None
    staged_row_count: int = 0
    error_count: int = 0
    pending_confirmation_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceFile:
    id: str
    import_batch_id: str
    original_filename: str
    content_sha256: str
    byte_size: int
    source_kind: SourceKind
    storage_key: str | None = None
    source_uri: str | None = None
    immutable_copy: bool = False


@dataclass(frozen=True)
class RowValidationError:
    id: str
    import_batch_id: str
    source_file_id: str
    sheet_name: str | None
    source_row_number: int | None
    error_code: str
    field_name: str | None
    message: str
    raw_value: str | None = None


@dataclass(frozen=True)
class StagedBomRow:
    id: str
    import_batch_id: str
    source_file_id: str
    sheet_name: str
    source_row_number: int
    parent_material_code: str | None
    version: str | None
    material_code: str | None
    material_name: str | None
    specification: str | None
    quantity: Decimal | None
    tax_included_unit_price: Decimal | None
    source_total_price: Decimal | None
    unit: str | None = None
    currency: str | None = None
    effective_from: date | None = None
    validation_status: str = "valid"
    pending_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class StagedBomHeader:
    """Import policy and parent metadata, persisted before controlled publication."""

    import_batch_id: str
    source_file_id: str
    sheet_name: str
    source_row_number: int
    parent_material_code: str
    version: str
    price_policy: str = "required"
    publish_parent_item: bool = False
    parent_unit: str | None = None
    confirmations: tuple[dict[str, object], ...] = ()
    unit_policy: str = "required"
    publication_scope: str = "full"


@dataclass(frozen=True)
class Item:
    id: str
    material_code: str
    name: str | None
    specification: str | None
    unit: str | None
    import_batch_id: str
    source_file_id: str
    source_sheet: str
    source_row_number: int


@dataclass(frozen=True)
class ItemRevision:
    """同一物料在一次发布批次中的不可变主数据快照。"""

    id: str
    item_id: str
    name: str | None
    specification: str | None
    unit: str | None
    import_batch_id: str
    source_file_id: str
    source_sheet: str
    source_row_number: int


@dataclass(frozen=True)
class BomHeader:
    id: str
    parent_material_code: str
    version: str
    effective_from: date | None
    import_batch_id: str
    source_file_id: str
    source_sheet: str


@dataclass(frozen=True)
class BomLine:
    id: str
    bom_header_id: str
    item_id: str
    quantity: Decimal
    unit: str | None
    import_batch_id: str
    source_file_id: str
    source_sheet: str
    source_row_number: int


@dataclass(frozen=True)
class PriceRecord:
    id: str
    item_id: str
    tax_included_unit_price: Decimal
    currency: str
    effective_from: date
    import_batch_id: str
    source_file_id: str
    source_sheet: str
    source_row_number: int


@dataclass(frozen=True)
class ImportResult:
    import_batch_id: str
    status: ImportBatchStatus
    staged_row_count: int
    error_count: int
    pending_confirmation_fields: tuple[str, ...] = ()
    errors: tuple[RowValidationError, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = ()
    completeness: dict[str, bool] = field(default_factory=dict)


class ItemMatchStatus(StrEnum):
    """外部料号与受控 item 主数据的关联状态。"""

    MATCHED = "matched"
    UNMATCHED = "unmatched"


@dataclass(frozen=True)
class StagedInventoryRow:
    """库存时点事实的行级暂存模型；未知字段始终保留为 ``None``。"""

    id: str
    import_batch_id: str
    source_file_id: str
    sheet_name: str
    source_row_number: int
    raw_material_code: str | None
    item_id: str | None
    match_status: ItemMatchStatus
    site: str | None
    warehouse: str | None
    location: str | None
    batch_no: str | None
    on_hand_qty: Decimal | None
    available_qty: Decimal | None = None
    reserved_qty: Decimal | None = None
    quality_status: str | None = None
    received_at: datetime | None = None
    expiry_date: date | None = None
    validation_status: str = "valid"
    is_duplicate: bool = False


@dataclass(frozen=True)
class InventoryImportResult:
    import_batch_id: str
    status: ImportBatchStatus
    staged_row_count: int
    valid_row_count: int
    error_count: int
    matched_item_count: int
    unmatched_item_count: int
    duplicate_row_count: int
    pending_confirmation_fields: tuple[str, ...] = ()
    errors: tuple[RowValidationError, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class InboundSupplyInputRow:
    """在途导入器合同，由明确采购订单来源适配器提供。"""

    sheet_name: str
    source_row_number: int
    material_code: object
    ordered_qty: object
    received_qty: object | None = None
    supplier_code: object | None = None
    order_no: object | None = None
    expected_arrival_date: object | None = None
    committed_date: object | None = None
    status: object | None = None


@dataclass(frozen=True)
class StagedInboundSupplyRow:
    id: str
    import_batch_id: str
    source_file_id: str
    sheet_name: str
    source_row_number: int
    raw_material_code: str | None
    item_id: str | None
    match_status: ItemMatchStatus
    supplier_code: str | None
    order_no: str | None
    ordered_qty: Decimal | None
    received_qty: Decimal | None
    in_transit_qty: Decimal | None
    expected_arrival_date: date | None
    committed_date: date | None
    supply_status: str | None
    validation_status: str = "valid"


@dataclass(frozen=True)
class InboundImportResult:
    import_batch_id: str
    status: ImportBatchStatus
    staged_row_count: int
    valid_row_count: int
    error_count: int
    matched_item_count: int
    unmatched_item_count: int
    pending_confirmation_fields: tuple[str, ...] = ()
    errors: tuple[RowValidationError, ...] = field(default_factory=tuple)
