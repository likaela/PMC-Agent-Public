"""Read-only published metadata for Agent context, without migrations or fact writes."""

from __future__ import annotations

from repositories.fact_query_repository import FactQueryRepository, _cursor_dicts


class AgentContextRepository:
    def __init__(self, facts: FactQueryRepository):
        self.facts = facts
        self.store = facts.store

    def resolve_product(self, code: str) -> dict | None:
        return self.facts.resolve_item(code)

    def bom_versions(self, canonical_code: str) -> list[dict]:
        return self.facts.list_published_bom_versions(canonical_code)

    def published_business_fields(self, canonical_code: str, revision: str) -> dict:
        """Confirmed metadata only; analysis_date is never a global default."""
        with self.store.transaction() as connection:
            row = self.store.execute(connection,
                "SELECT h.id FROM bom_headers h JOIN import_batches b ON b.id=h.import_batch_id "
                "WHERE h.parent_material_code=? AND h.version=? AND b.status='published'",
                (canonical_code, revision)).fetchone()
        events = self.facts.bom_confirmation_provenance(str(row[0])) if row else []
        fields = {}
        for event in events:
            confirmed = event["confirmed_fields"]
            fields.update({key: confirmed[key] for key in
                ("parent_unit", "business_publication_date", "business_snapshot_date", "business_timezone")
                if confirmed.get(key) is not None})
        return fields

    def structure_evidence(self, code: str, revision: str | None) -> dict | None:
        """Describe a unique published structure, never select a production-valid BOM."""
        product = self.resolve_product(code)
        if product is None or product.get("ambiguous"):
            return None
        versions = self.bom_versions(str(product["material_code"]))
        if revision is not None:
            versions = [row for row in versions if row["version"] == revision]
        if len(versions) != 1:
            return None
        from services.fact_query_service import FactQueryService
        result = FactQueryService(self.facts).query_bom(str(product["material_code"]), str(versions[0]["version"]))
        return result if result["status"] == "ok" else None

    def confirmed_analysis_context(self, context: dict, canonical_code: str) -> dict | None:
        """Accept only an explicitly session-bound, current audited confirmation."""
        event_id = context.get("confirmed_analysis_scope", {}).get(canonical_code)
        if event_id is None:
            return None
        from repositories.published_bom_confirmation_repository import PublishedBomConfirmationRepository
        from services.published_bom_confirmation_service import digest
        repository = PublishedBomConfirmationRepository(self.store)
        event = repository.get(event_id)
        if event is None or event["payload"]["product"] != canonical_code:
            raise ValueError("Session confirmation scope differs from product")
        with self.store.transaction() as connection:
            current = repository._state(connection, canonical_code, event["payload"]["revision"])
        if digest(current) != digest(event["after"]):
            raise ValueError("Session confirmation evidence is stale")
        return {**event["payload"], "confirmation_id": event["id"], "recorded_at": str(event["recorded_at"])}

    def requested_analysis_context(self, canonical_code: str, revision: str | None, analysis_date: str | None) -> dict:
        """Read an explicitly requested current audit; ambiguous scenarios never pick latest."""
        from repositories.published_bom_confirmation_repository import PublishedBomConfirmationRepository
        from services.published_bom_confirmation_service import digest
        repository = PublishedBomConfirmationRepository(self.store)
        with self.store.transaction() as connection:
            migrated = self.store.execute(connection,
                "SELECT version FROM schema_migrations WHERE version='011_published_bom_confirmations'").fetchone()
            if migrated is None:
                return {"status": "data_unavailable", "context": None}
            ids = self.store.execute(connection,
                "SELECT c.id FROM published_bom_confirmations c JOIN bom_headers h ON h.id=c.bom_header_id "
                "JOIN import_batches b ON b.id=h.import_batch_id "
                "WHERE h.parent_material_code=? AND b.status='published' ORDER BY c.recorded_at,c.id",
                (canonical_code,)).fetchall()
        matches = {}
        for row in ids:
            event = repository.get(str(row[0]))
            payload = event['payload']
            if (not payload.get('analysis_date') or
                    revision is not None and payload['revision'] != revision or
                    analysis_date is not None and payload['analysis_date'] != analysis_date):
                continue
            with self.store.transaction() as connection:
                current = repository._state(connection, canonical_code, payload['revision'])
            if digest(current) != digest(event['after']):
                continue
            # Equivalent audit renewals may share a scenario, conflicting dates may not.
            key = tuple(payload.get(k) for k in ('revision', 'analysis_date', 'business_publication_date',
                'business_snapshot_date', 'business_timezone'))
            matches[key] = {**payload, 'confirmation_id': event['id'], 'recorded_at': str(event['recorded_at'])}
        if len(matches) != 1:
            return {"status": "needs_input" if matches else "data_unavailable", "context": None}
        return {"status": "ok", "context": next(iter(matches.values()))}

    def evidence_is_current(self, reference: dict) -> bool:
        """Refresh changed BOM metadata without deleting or rewriting old history."""
        result = reference["result"]
        data = result.get("data") or {}
        structure = data.get("bom_structure")
        previous = structure if structure else result if reference["tool_name"] == "published_bom_query" else None
        if previous is None:
            return True
        previous_data = previous.get("data") or {}
        code = (previous_data.get("parent_item") or {}).get("material_code")
        if not code:
            return True
        with self.store.transaction() as connection:
            row = self.store.execute(connection,
                "SELECT h.id FROM bom_headers h JOIN import_batches b ON b.id=h.import_batch_id "
                "WHERE h.parent_material_code=? AND h.version=? AND b.status='published'",
                (code, previous_data.get("revision"))).fetchone()
        if row is None:
            return False
        # A small audit watermark check preserves no-reread/no-recalculation
        # follow-ups. Complete BOM facts are reread only after a confirmed update.
        current = self.facts.bom_confirmation_provenance(str(row[0]))
        previous_events = (previous.get("provenance") or {}).get("business_confirmations", [])
        return {event["id"] for event in current} == {event["id"] for event in previous_events}

    def latest_inventory_time(self) -> dict | None:
        """A published observation anchor, not a wall-clock/default business date."""
        # SQLite TEXT may carry different explicit offsets; compare instants, not strings.
        ordering = "julianday(s.snapshot_at)" if self.store.dialect == "sqlite" else "s.snapshot_at"
        with self.store.transaction() as connection:
            rows = _cursor_dicts(self.store.execute(connection,
                "SELECT s.snapshot_at, s.import_batch_id, s.source_sheet AS sheet_name, "
                "sf.original_filename AS source_file, sf.content_sha256 "
                "FROM inventory_snapshots s JOIN import_batches b ON b.id = s.import_batch_id "
                "JOIN source_files sf ON sf.id = s.source_file_id "
                "WHERE b.status = 'published' AND s.published_at IS NOT NULL "
                "AND EXISTS (SELECT 1 FROM inventory_facts f WHERE f.inventory_snapshot_id = s.id) "
                f"ORDER BY {ordering} DESC, s.id LIMIT 1"))
        return rows[0] if rows else None


__all__ = ["AgentContextRepository"]
