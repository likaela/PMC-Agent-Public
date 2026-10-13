"""Private, source-bound human confirmations; never a default inventory unit.

Uses the existing confirmation canonicalization/audit fingerprint mechanism.
No published inventory facts or BOMs are updated by this observation metadata.
"""
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.published_bom_confirmation_service import Identifier, digest


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIRMATIONS = ROOT / "data/facts/confirmations/inventory_units.json"
DEFAULT_QUANTITY_CONFIRMATIONS = ROOT / "data/facts/confirmations/inventory_quantity_use.json"


class InventoryUnitConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_uri: Identifier
    source_file: Identifier
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sheet_name: Identifier
    quantity_field: Literal["库存"]
    unit: Identifier
    confirmed_by: Identifier
    reason: Identifier
    recorded_at: str
    user_statement: Identifier
    applies_to_existing_source_only: Literal[True]

    @field_validator("recorded_at")
    @classmethod
    def aware_timestamp(cls, value):
        if datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError("Confirmation audit timestamp requires a timezone")
        return value


class InventoryUnitConfirmations(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmations: list[InventoryUnitConfirmation]


class ReviewProductScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    product: Identifier
    revision: Identifier


class InventoryQuantityConfirmation(InventoryUnitConfirmation):
    """Authorizes a source-bound review comparison, NOT published availability."""
    quantity_role: Literal["actual_stock_for_review"]
    product_scope: list[ReviewProductScope] = Field(min_length=1)


class InventoryQuantityConfirmations(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmations: list[InventoryQuantityConfirmation]


class InventoryUnitConfirmationRepository:
    model = InventoryUnitConfirmations
    def __init__(self, path=DEFAULT_CONFIRMATIONS):
        self.path = Path(path)

    def for_source(self, source_path, content_sha256, sheet_name):
        if not self.path.exists():
            return None
        request = self.model.model_validate_json(self.path.read_text(encoding="utf-8"))
        matches = [event for event in request.confirmations
                   if (ROOT / event.source_uri).resolve() == Path(source_path).resolve()
                   and event.source_file == Path(source_path).name
                   and event.content_sha256 == content_sha256 and event.sheet_name == sheet_name]
        if not matches:
            return None  # New workbook/content/sheet has no inherited authorization.
        if len(matches) != 1:
            raise ValueError("Ambiguous inventory unit confirmations")
        payload = matches[0].model_dump()
        return {**payload, "confirmation_id": digest(payload),
                "source_kind": "human_business_confirmation"}


class InventoryQuantityConfirmationRepository(InventoryUnitConfirmationRepository):
    model = InventoryQuantityConfirmations

    def __init__(self, path=DEFAULT_QUANTITY_CONFIRMATIONS):
        super().__init__(path)
