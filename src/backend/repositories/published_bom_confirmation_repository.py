"""Audited, atomic, idempotent metadata confirmation of existing published BOMs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from repositories.fact_query_repository import _cursor_dicts
from services.published_bom_confirmation_service import canonical_json, digest


class PublishedBomConfirmationRepository:
    def __init__(self, store):
        self.store = store

    def _state(self, connection, product, revision, *, lock=False):
        suffix = " FOR UPDATE OF h" if lock and self.store.dialect == "postgresql" else ""
        headers = _cursor_dicts(self.store.execute(connection,
            "SELECT h.*, sf.original_filename, sf.content_sha256, sf.storage_key, sf.source_uri "
            "FROM bom_headers h JOIN import_batches b ON b.id=h.import_batch_id "
            "JOIN source_files sf ON sf.id=h.source_file_id "
            "WHERE h.parent_material_code=? AND h.version=? AND b.status='published'" + suffix,
            (product, revision)))
        if len(headers) != 1:
            raise ValueError("Exactly one published BOM must exist")
        header = {key: str(value) if value is not None else None for key, value in headers[0].items()}
        parent = self.store.execute(connection,
            "SELECT unit FROM items WHERE material_code=?", (product,)).fetchone()
        if parent is None:
            raise ValueError("Published parent item is missing")
        # Keep legacy NULL state fingerprints compatible; audit new explicit values.
        if parent[0] is not None:
            header["parent_unit"] = str(parent[0])
        rows = _cursor_dicts(self.store.execute(connection,
            "SELECT l.*, i.material_code, sf.content_sha256, sf.original_filename "
            "FROM bom_lines l JOIN items i ON i.id=l.item_id JOIN source_files sf ON sf.id=l.source_file_id "
            "WHERE l.bom_header_id=? ORDER BY l.source_sheet,l.source_row_number,l.id", (header["id"],)))
        lines = [{key: str(value) if value is not None else None for key, value in row.items()} for row in rows]
        if not lines or any(not row["source_sheet"] or not row["source_row_number"] or
                            len(row["content_sha256"]) != 64 for row in lines):
            raise ValueError("Published BOM provenance is incomplete")
        if not (header["storage_key"] or header["source_uri"]):
            raise ValueError("Published source lacks a controlled locator")
        return {"header": header, "lines": lines}

    @staticmethod
    def _validate(state, confirmation):
        if len(state["lines"]) != confirmation.expected_line_count:
            raise ValueError("Published BOM line count differs from human confirmation")
        existing_date = state["header"]["effective_from"]
        if existing_date is not None and existing_date != confirmation.effective_from:
            raise ValueError("Cannot replace an already confirmed effective date")
        if any(row["unit"] not in (None, confirmation.child_unit) for row in state["lines"]):
            raise ValueError("Cannot replace a conflicting published line unit")
        if confirmation.parent_unit is not None and state["header"].get("parent_unit") not in (None, confirmation.parent_unit):
            raise ValueError("Cannot replace a conflicting confirmed parent unit")

    def precheck(self, request):
        with self.store.transaction() as connection:
            manifest = []
            for confirmation in request.products:
                state = self._state(connection, confirmation.product, confirmation.revision)
                self._validate(state, confirmation)
                manifest.append({"product": confirmation.product, "revision": confirmation.revision,
                                 "fingerprint": digest(state), "state": state})
        return manifest

    def apply(self, request, expected_manifest):
        expected = {(row["product"], row["revision"]): row["fingerprint"] for row in expected_manifest}
        if set(expected) != {(p.product, p.revision) for p in request.products}:
            raise ValueError("Confirmation manifest scope differs")
        events = []
        with self.store.transaction(write_lock=True) as connection:
            for confirmation in request.products:
                payload = {**request.model_dump(exclude={"products"}), **confirmation.model_dump(exclude_none=True)}
                key = digest(payload)
                # Header lock serializes concurrent confirmations on PostgreSQL as well.
                before = self._state(connection, confirmation.product, confirmation.revision, lock=True)
                self._validate(before, confirmation)
                existing = self.store.execute(connection,
                    "SELECT id,after_json FROM published_bom_confirmations WHERE confirmation_key=?", (key,)).fetchone()
                if existing:
                    if digest(json.loads(existing[1])) != digest(before):
                        raise ValueError("Idempotent confirmation no longer matches published facts")
                    events.append({"confirmation_id": str(existing[0]), "product": confirmation.product,
                                   "revision": confirmation.revision, "status": "already_applied"})
                    continue
                if digest(before) != expected[(confirmation.product, confirmation.revision)]:
                    raise ValueError("Published BOM changed after precheck; repeat validation")
                self.store.execute(connection, "UPDATE bom_headers SET effective_from=? WHERE id=?",
                    (confirmation.effective_from, before["header"]["id"]))
                self.store.execute(connection, "UPDATE bom_lines SET unit=? WHERE bom_header_id=?",
                    (confirmation.child_unit, before["header"]["id"]))
                if confirmation.parent_unit is not None:
                    # Explicit product confirmation only. Do not update child masters,
                    # staged import history, inventory units, or inventory facts.
                    self.store.execute(connection, "UPDATE items SET unit=? WHERE material_code=?",
                        (confirmation.parent_unit, confirmation.product))
                after = self._state(connection, confirmation.product, confirmation.revision)
                event_id = str(uuid4())
                self.store.execute(connection,
                    "INSERT INTO published_bom_confirmations (id,confirmation_key,bom_header_id,confirmed_by,reason,"
                    "evidence_uri,evidence_sha256,payload_json,before_json,after_json,recorded_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (event_id, key, before["header"]["id"], request.confirmed_by, request.reason,
                     request.evidence_uri, request.evidence_sha256, canonical_json(payload),
                     canonical_json(before), canonical_json(after), datetime.now(UTC).isoformat()))
                events.append({"confirmation_id": event_id, "product": confirmation.product,
                               "revision": confirmation.revision, "status": "applied"})
        return events

    def get(self, confirmation_id):
        with self.store.transaction() as connection:
            rows = _cursor_dicts(self.store.execute(connection,
                "SELECT * FROM published_bom_confirmations WHERE id=?", (confirmation_id,)))
        if not rows:
            return None
        event = rows[0]
        for key in ("payload_json", "before_json", "after_json"):
            event[key.removesuffix("_json")] = json.loads(event.pop(key))
        return event

    def provenance_for_bom(self, bom_header_id):
        with self.store.transaction() as connection:
            migrated = self.store.execute(connection,
                "SELECT version FROM schema_migrations WHERE version='011_published_bom_confirmations'").fetchone()
            if migrated is None:
                return []
            rows = _cursor_dicts(self.store.execute(connection,
                "SELECT id,confirmed_by,reason,evidence_uri,evidence_sha256,payload_json,recorded_at "
                "FROM published_bom_confirmations WHERE bom_header_id=? ORDER BY recorded_at,id", (bom_header_id,)))
        return [{**{key: str(value) for key, value in row.items() if key != "payload_json"},
                 "confirmed_fields": json.loads(row["payload_json"])} for row in rows]

    def scoped_context(self, confirmation_ids):
        """Internal, explicitly opted-in sessions only; never a global default."""
        events = [self.get(event_id) for event_id in confirmation_ids]
        if not events or any(event is None for event in events):
            raise ValueError("Missing confirmation evidence")
        return {"fact": {}, "production": {}, "active_domain": None,
                "confirmed_analysis_scope": {event["payload"]["product"]: event["id"] for event in events}}
