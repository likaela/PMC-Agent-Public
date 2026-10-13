CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
-- statement-break
CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT PRIMARY KEY,
    mapping_name TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    published_at TEXT,
    staged_row_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    pending_confirmation_fields_json TEXT NOT NULL DEFAULT '[]'
);
-- statement-break
CREATE TABLE IF NOT EXISTS source_files (
    id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    original_filename TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    UNIQUE(import_batch_id, content_sha256)
);
-- statement-break
CREATE TABLE IF NOT EXISTS staged_bom_rows (
    id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    sheet_name TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    parent_material_code TEXT,
    version TEXT,
    material_code TEXT,
    material_name TEXT,
    specification TEXT,
    quantity TEXT,
    tax_included_unit_price TEXT,
    source_total_price TEXT,
    unit TEXT,
    currency TEXT,
    effective_from TEXT,
    validation_status TEXT NOT NULL,
    pending_fields_json TEXT NOT NULL DEFAULT '[]',
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(import_batch_id, sheet_name, source_row_number)
);
-- statement-break
CREATE TABLE IF NOT EXISTS row_validation_errors (
    id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    sheet_name TEXT,
    source_row_number INTEGER,
    error_code TEXT NOT NULL,
    field_name TEXT,
    message TEXT NOT NULL,
    raw_value TEXT
);
-- statement-break
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    material_code TEXT NOT NULL UNIQUE,
    name TEXT,
    specification TEXT,
    unit TEXT NOT NULL,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_sheet TEXT NOT NULL,
    source_row_number INTEGER NOT NULL
);
-- statement-break
CREATE TABLE IF NOT EXISTS bom_headers (
    id TEXT PRIMARY KEY,
    parent_material_code TEXT NOT NULL,
    version TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_sheet TEXT NOT NULL,
    UNIQUE(parent_material_code, version)
);
-- statement-break
CREATE TABLE IF NOT EXISTS bom_lines (
    id TEXT PRIMARY KEY,
    bom_header_id TEXT NOT NULL REFERENCES bom_headers(id),
    item_id TEXT NOT NULL REFERENCES items(id),
    quantity TEXT NOT NULL,
    unit TEXT NOT NULL,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_sheet TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    UNIQUE(bom_header_id, item_id)
);
-- statement-break
CREATE TABLE IF NOT EXISTS price_records (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES items(id),
    tax_included_unit_price TEXT NOT NULL,
    currency TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_sheet TEXT NOT NULL,
    source_row_number INTEGER NOT NULL
);
-- statement-break
CREATE INDEX IF NOT EXISTS idx_staged_bom_rows_batch ON staged_bom_rows(import_batch_id);
-- statement-break
CREATE INDEX IF NOT EXISTS idx_validation_errors_batch ON row_validation_errors(import_batch_id);
-- statement-break
CREATE INDEX IF NOT EXISTS idx_bom_headers_lookup ON bom_headers(parent_material_code, version);
