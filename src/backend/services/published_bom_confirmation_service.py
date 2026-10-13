"""Human-confirmed published BOM metadata, not inventory or quantity calculations."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]


class BomBusinessConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    product: Identifier
    revision: Identifier
    expected_line_count: int = Field(gt=0)
    # A uniform line unit is an explicit human assertion, never an item/master unit.
    child_unit: Identifier
    # Optional for old confirmations; explicit product unit is not a child/stock unit.
    parent_unit: Identifier | None = None
    effective_from: str
    business_publication_date: str
    analysis_date: str
    business_snapshot_date: str
    business_timezone: Identifier

    @field_validator("effective_from", "business_publication_date", "analysis_date", "business_snapshot_date")
    @classmethod
    def iso_date(cls, value):
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("Dates require YYYY-MM-DD")
        return value

    @field_validator("business_timezone")
    @classmethod
    def timezone(cls, value):
        import re
        if not re.fullmatch(r"UTC[+-](?:0\d|1[0-4]):[0-5]\d", value):
            raise ValueError("An explicit UTC offset is required")
        if value[4:6] == "14" and value[-2:] != "00":
            raise ValueError("UTC offset exceeds 14 hours")
        return value


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmed_by: Identifier
    reason: Identifier
    evidence_uri: Identifier
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    products: list[BomBusinessConfirmation] = Field(min_length=1)

    @field_validator("products")
    @classmethod
    def unique_products(cls, products):
        if len({(p.product, p.revision) for p in products}) != len(products):
            raise ValueError("Repeated product/revision in confirmation")
        return products


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class PublishedBomConfirmationService:
    def __init__(self, repository):
        self.repository = repository

    def precheck(self, request: ConfirmationRequest) -> list[dict]:
        return self.repository.precheck(request)

    def apply(self, request: ConfirmationRequest, expected_manifest: list[dict]) -> list[dict]:
        """All requested BOMs succeed together, or none is updated."""
        return self.repository.apply(request, expected_manifest)
