"""Read-only discovery of BOM parents, never a production feasibility result."""

from repositories.fact_query_repository import _cursor_dicts


class PublishedProductRepository:
    def __init__(self, store):
        self.store = store

    def search(self, search_term=None, limit=50):
        params = []
        condition = ""
        if search_term:
            # Literal substring filtering, not approximate item/alias resolution.
            escaped = search_term.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            condition = " AND (h.parent_material_code LIKE ? ESCAPE '!' OR (ib.status = 'published' AND i.name LIKE ? ESCAPE '!'))"
            params.extend([f"%{escaped}%"] * 2)
        params.append(limit + 1)
        with self.store.transaction() as connection:
            rows = _cursor_dicts(self.store.execute(connection,
                "SELECT h.parent_material_code AS product_code, "
                "CASE WHEN ib.status = 'published' THEN i.name ELSE NULL END AS product_name, "
                "h.version AS revision, h.effective_from, h.effective_to, "
                "h.import_batch_id, h.source_sheet AS sheet_name, "
                "sf.original_filename AS source_file, sf.content_sha256 "
                ", i.import_batch_id AS name_import_batch_id, ns.original_filename AS name_source_file, "
                "ns.content_sha256 AS name_sha256, i.source_sheet AS name_sheet, i.source_row_number AS name_row "
                "FROM bom_headers h JOIN import_batches b ON b.id = h.import_batch_id "
                "JOIN source_files sf ON sf.id = h.source_file_id "
                "LEFT JOIN items i ON i.material_code = h.parent_material_code "
                "LEFT JOIN import_batches ib ON ib.id = i.import_batch_id "
                "LEFT JOIN source_files ns ON ns.id = i.source_file_id "
                "WHERE b.status = 'published'" + condition +
                " ORDER BY h.parent_material_code, h.version LIMIT ?", params))
        return rows[:limit], len(rows) > limit
