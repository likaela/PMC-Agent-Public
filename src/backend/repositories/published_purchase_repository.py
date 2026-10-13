"""已发布采购参数与价格的显式关联查询，不选择或推荐供应商。"""

from __future__ import annotations

from typing import Any

from repositories.fact_query_repository import _cursor_dicts
from repositories.inventory_analysis_repository import InventoryAnalysisRepository


class PublishedPurchaseRepository(InventoryAnalysisRepository):
    def get_purchase_parameters(self, item_id: str) -> list[dict[str, Any]]:
        with self.store.transaction() as connection:
            return _cursor_dicts(self.store.execute(
                connection,
                "SELECT p.id, p.item_id, p.supplier_code, p.purchase_unit, p.minimum_order_quantity, "
                "p.order_multiple, p.packaging_multiple, p.order_multiple_applicable, "
                "p.packaging_multiple_applicable, p.lead_time_days, p.price_record_id, "
                "p.effective_from, p.effective_to, p.import_batch_id, p.source_sheet AS sheet_name, "
                "p.source_row_number AS row_number, sf.original_filename AS source_file, sf.content_sha256 "
                "FROM purchase_parameter_facts p JOIN import_batches b ON b.id = p.import_batch_id "
                "JOIN source_files sf ON sf.id = p.source_file_id "
                "WHERE p.item_id = ? AND b.status = 'published' ORDER BY p.effective_from, p.id",
                (item_id,),
            ))

    def get_price_record(self, price_record_id: str) -> dict[str, Any] | None:
        with self.store.transaction() as connection:
            rows = _cursor_dicts(self.store.execute(
                connection,
                "SELECT p.id, p.item_id, p.tax_included_unit_price, p.currency, p.unit, p.supplier_code, "
                "p.effective_from, p.effective_to, p.import_batch_id, p.source_sheet AS sheet_name, "
                "p.source_row_number AS row_number, sf.original_filename AS source_file, sf.content_sha256 "
                "FROM price_records p JOIN import_batches b ON b.id = p.import_batch_id "
                "JOIN source_files sf ON sf.id = p.source_file_id "
                "WHERE p.id = ? AND b.status = 'published'",
                (price_record_id,),
            ))
        return rows[0] if rows else None


__all__ = ["PublishedPurchaseRepository"]
