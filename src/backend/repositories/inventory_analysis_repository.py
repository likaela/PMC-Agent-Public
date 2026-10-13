"""v1.11 只读批次库存、在途观察与风险策略；保留源行粒度。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from repositories.fact_query_repository import FactQueryRepository, _cursor_dicts


class InventoryAnalysisRepository(FactQueryRepository):
    """沿用标准 Item/Alias，额外读取 published 批次分析事实。"""

    def get_lots(self, item_id: str, as_of: datetime) -> dict[str, Any] | None:
        time_column = "julianday(s.snapshot_at)" if self.store.dialect == "sqlite" else "s.snapshot_at"
        time_parameter = "julianday(?)" if self.store.dialect == "sqlite" else "?"
        with self.store.transaction() as connection:
            snapshots = _cursor_dicts(self.store.execute(
                connection,
                "SELECT s.id, s.snapshot_at, s.observed_at, s.import_batch_id, "
                "s.source_sheet, sf.original_filename AS source_file, sf.content_sha256 "
                "FROM inventory_snapshots s JOIN import_batches b ON b.id = s.import_batch_id "
                "JOIN source_files sf ON sf.id = s.source_file_id "
                f"WHERE b.status = 'published' AND {time_column} <= {time_parameter} AND EXISTS "
                "(SELECT 1 FROM inventory_facts f WHERE f.inventory_snapshot_id = s.id AND f.item_id = ?) "
                f"ORDER BY {time_column} DESC, s.id LIMIT 2",
                (self._time_param(as_of), item_id),
            ))
            if not snapshots:
                return None
            if len(snapshots) > 1 and self._same_instant(snapshots[0]["snapshot_at"], snapshots[1]["snapshot_at"]):
                return {"ambiguous": True}
            snapshot = snapshots[0]
            lots = _cursor_dicts(self.store.execute(
                connection,
                "SELECT f.id, f.batch_no, f.site, f.warehouse, f.location, f.on_hand_qty, "
                "f.available_qty, f.reserved_qty, f.quality_status, f.received_at, "
                "f.last_movement_at, f.expiry_date, f.is_duplicate, f.import_batch_id, "
                "f.source_sheet AS sheet_name, f.source_row_number AS row_number, "
                "sf.original_filename AS source_file, sf.content_sha256 "
                "FROM inventory_facts f JOIN source_files sf ON sf.id = f.source_file_id "
                "JOIN import_batches b ON b.id = f.import_batch_id "
                "WHERE f.inventory_snapshot_id = ? AND f.item_id = ? AND b.status = 'published' "
                "ORDER BY f.source_sheet, f.source_row_number",
                (snapshot["id"], item_id),
            ))
        return {"snapshot": snapshot, "lots": lots}

    def get_supplies(self, item_id: str, as_of: datetime) -> dict[str, Any] | None:
        time_column = "julianday(o.observed_at)" if self.store.dialect == "sqlite" else "o.observed_at"
        time_parameter = "julianday(?)" if self.store.dialect == "sqlite" else "?"
        with self.store.transaction() as connection:
            observations = _cursor_dicts(self.store.execute(
                connection,
                "SELECT o.id, o.observed_at, o.import_batch_id, o.source_name, "
                "sf.original_filename AS source_file, sf.content_sha256 "
                "FROM inbound_supply_observations o JOIN import_batches b ON b.id = o.import_batch_id "
                "JOIN source_files sf ON sf.id = o.source_file_id "
                f"WHERE b.status = 'published' AND {time_column} <= {time_parameter} AND EXISTS "
                "(SELECT 1 FROM inbound_supply_facts f WHERE f.inbound_observation_id = o.id AND f.item_id = ?) "
                f"ORDER BY {time_column} DESC, o.id LIMIT 2",
                (self._time_param(as_of), item_id),
            ))
            if not observations:
                return None
            if len(observations) > 1 and self._same_instant(observations[0]["observed_at"], observations[1]["observed_at"]):
                return {"ambiguous": True}
            observation = observations[0]
            lines = _cursor_dicts(self.store.execute(
                connection,
                "SELECT f.id, f.supplier_code, f.order_no, f.ordered_qty, f.received_qty, "
                "f.in_transit_qty, f.expected_arrival_date, f.committed_date, f.supply_status, "
                "f.import_batch_id, f.source_sheet AS sheet_name, f.source_row_number AS row_number, "
                "sf.original_filename AS source_file, sf.content_sha256 "
                "FROM inbound_supply_facts f JOIN source_files sf ON sf.id = f.source_file_id "
                "JOIN import_batches b ON b.id = f.import_batch_id "
                "WHERE f.inbound_observation_id = ? AND f.item_id = ? AND b.status = 'published' "
                "ORDER BY f.source_sheet, f.source_row_number",
                (observation["id"], item_id),
            ))
        return {"observation": observation, "lines": lines}

    @staticmethod
    def _same_instant(first: object, second: object) -> bool:
        return datetime.fromisoformat(str(first).replace("Z", "+00:00")) == datetime.fromisoformat(str(second).replace("Z", "+00:00"))

    def get_risk_policies(self, item_id: str) -> list[dict[str, Any]]:
        with self.store.transaction() as connection:
            return _cursor_dicts(self.store.execute(
                connection,
                "SELECT p.id, p.safety_stock_qty, p.unit, p.expiry_warning_days, p.stagnant_days, "
                "p.effective_from, p.effective_to, p.import_batch_id, p.source_sheet AS sheet_name, "
                "p.source_row_number AS row_number, sf.original_filename AS source_file, sf.content_sha256 "
                "FROM inventory_risk_policies p JOIN import_batches b ON b.id = p.import_batch_id "
                "JOIN source_files sf ON sf.id = p.source_file_id "
                "WHERE p.item_id = ? AND b.status = 'published' ORDER BY p.effective_from, p.id",
                (item_id,),
            ))


__all__ = ["InventoryAnalysisRepository"]
