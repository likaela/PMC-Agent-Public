CREATE TABLE IF NOT EXISTS item_aliases (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES items(id),
    alias_type TEXT NOT NULL,
    alias_code TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(alias_type, alias_code)
);
-- statement-break
CREATE TABLE IF NOT EXISTS inventory_import_contexts (
    import_batch_id TEXT PRIMARY KEY REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_sheet TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    snapshot_at TIMESTAMPTZ,
    excluded_sources_json TEXT NOT NULL DEFAULT '[]',
    source_row_count INTEGER NOT NULL DEFAULT 0,
    duplicate_row_count INTEGER NOT NULL DEFAULT 0
);
-- statement-break
CREATE TABLE IF NOT EXISTS staged_inventory_rows (
    id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    sheet_name TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    raw_material_code TEXT,
    item_id TEXT REFERENCES items(id),
    match_status TEXT NOT NULL,
    site TEXT,
    warehouse TEXT,
    location TEXT,
    batch_no TEXT,
    on_hand_qty NUMERIC(38, 12),
    available_qty NUMERIC(38, 12),
    reserved_qty NUMERIC(38, 12),
    quality_status TEXT,
    received_at TIMESTAMPTZ,
    expiry_date DATE,
    validation_status TEXT NOT NULL,
    is_duplicate BOOLEAN NOT NULL DEFAULT FALSE,
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(import_batch_id, sheet_name, source_row_number)
);
-- statement-break
CREATE TABLE IF NOT EXISTS inventory_snapshots (
    id TEXT PRIMARY KEY,
    snapshot_at TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    import_batch_id TEXT NOT NULL UNIQUE REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_content_sha256 TEXT NOT NULL,
    source_sheet TEXT NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    UNIQUE(source_content_sha256, source_sheet, snapshot_at)
);
-- statement-break
CREATE TABLE IF NOT EXISTS inventory_facts (
    id TEXT PRIMARY KEY,
    inventory_snapshot_id TEXT NOT NULL REFERENCES inventory_snapshots(id),
    item_id TEXT NOT NULL REFERENCES items(id),
    raw_material_code TEXT NOT NULL,
    site TEXT,
    warehouse TEXT,
    location TEXT,
    batch_no TEXT,
    on_hand_qty NUMERIC(38, 12) NOT NULL,
    available_qty NUMERIC(38, 12),
    reserved_qty NUMERIC(38, 12),
    quality_status TEXT,
    received_at TIMESTAMPTZ,
    expiry_date DATE,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_sheet TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    is_duplicate BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE(import_batch_id, source_sheet, source_row_number)
);
-- statement-break
CREATE TABLE IF NOT EXISTS inbound_import_contexts (
    import_batch_id TEXT PRIMARY KEY REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    observed_at TIMESTAMPTZ NOT NULL,
    source_name TEXT NOT NULL,
    source_reliability TEXT NOT NULL
);
-- statement-break
CREATE TABLE IF NOT EXISTS staged_inbound_supply_rows (
    id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    sheet_name TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    raw_material_code TEXT,
    item_id TEXT REFERENCES items(id),
    match_status TEXT NOT NULL,
    supplier_code TEXT,
    order_no TEXT,
    ordered_qty NUMERIC(38, 12),
    received_qty NUMERIC(38, 12),
    in_transit_qty NUMERIC(38, 12),
    expected_arrival_date DATE,
    committed_date DATE,
    supply_status TEXT,
    validation_status TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(import_batch_id, sheet_name, source_row_number)
);
-- statement-break
CREATE TABLE IF NOT EXISTS inbound_supply_observations (
    id TEXT PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    import_batch_id TEXT NOT NULL UNIQUE REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_content_sha256 TEXT NOT NULL,
    source_name TEXT NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    UNIQUE(source_content_sha256, source_name, observed_at)
);
-- statement-break
CREATE TABLE IF NOT EXISTS inbound_supply_facts (
    id TEXT PRIMARY KEY,
    inbound_observation_id TEXT NOT NULL REFERENCES inbound_supply_observations(id),
    item_id TEXT NOT NULL REFERENCES items(id),
    raw_material_code TEXT NOT NULL,
    supplier_code TEXT,
    order_no TEXT,
    ordered_qty NUMERIC(38, 12) NOT NULL,
    received_qty NUMERIC(38, 12),
    in_transit_qty NUMERIC(38, 12),
    expected_arrival_date DATE,
    committed_date DATE,
    supply_status TEXT,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_sheet TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    UNIQUE(import_batch_id, source_sheet, source_row_number)
);
-- statement-break
CREATE TABLE IF NOT EXISTS import_quality_reports (
    id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL UNIQUE REFERENCES import_batches(id),
    fact_type TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
-- statement-break
CREATE INDEX IF NOT EXISTS idx_item_alias_lookup ON item_aliases(alias_type, alias_code);
-- statement-break
CREATE INDEX IF NOT EXISTS idx_staged_inventory_batch ON staged_inventory_rows(import_batch_id);
-- statement-break
CREATE INDEX IF NOT EXISTS idx_staged_inventory_match ON staged_inventory_rows(import_batch_id, match_status);
-- statement-break
CREATE INDEX IF NOT EXISTS idx_inventory_snapshot_time ON inventory_snapshots(snapshot_at);
-- statement-break
CREATE INDEX IF NOT EXISTS idx_inventory_fact_item ON inventory_facts(item_id, inventory_snapshot_id);
-- statement-break
CREATE INDEX IF NOT EXISTS idx_staged_inbound_batch ON staged_inbound_supply_rows(import_batch_id);
-- statement-break
CREATE INDEX IF NOT EXISTS idx_inbound_fact_item ON inbound_supply_facts(item_id, inbound_observation_id);
