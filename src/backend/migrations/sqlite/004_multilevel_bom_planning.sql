ALTER TABLE bom_headers ADD COLUMN effective_to TEXT;
-- statement-break
CREATE TABLE IF NOT EXISTS unit_conversions (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES items(id),
    from_unit TEXT NOT NULL,
    to_unit TEXT NOT NULL,
    factor TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_sheet TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    UNIQUE(item_id, from_unit, to_unit, effective_from, import_batch_id)
);
-- statement-break
CREATE INDEX IF NOT EXISTS idx_bom_headers_effective ON bom_headers(parent_material_code, effective_from, effective_to);
-- statement-break
CREATE INDEX IF NOT EXISTS idx_unit_conversions_lookup ON unit_conversions(item_id, from_unit, to_unit, effective_from, effective_to);
