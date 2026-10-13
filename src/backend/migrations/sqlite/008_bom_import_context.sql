CREATE TABLE IF NOT EXISTS staged_bom_headers (
    import_batch_id TEXT PRIMARY KEY REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    sheet_name TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    parent_material_code TEXT NOT NULL,
    version TEXT NOT NULL,
    price_policy TEXT NOT NULL CHECK (price_policy IN ('required', 'optional')),
    publish_parent_item INTEGER NOT NULL DEFAULT 0,
    parent_unit TEXT,
    confirmations_json TEXT NOT NULL DEFAULT '[]'
);
