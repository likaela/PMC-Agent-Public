ALTER TABLE inventory_facts ADD COLUMN last_movement_at TIMESTAMPTZ;
-- statement-break
CREATE TABLE inventory_risk_policies (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES items(id),
    safety_stock_qty NUMERIC(38, 12) CHECK (safety_stock_qty >= 0),
    unit TEXT NOT NULL,
    expiry_warning_days INTEGER CHECK (expiry_warning_days >= 0),
    stagnant_days INTEGER CHECK (stagnant_days > 0),
    effective_from DATE NOT NULL,
    effective_to DATE,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_sheet TEXT NOT NULL,
    source_row_number INTEGER NOT NULL CHECK (source_row_number > 0),
    UNIQUE(item_id, effective_from, import_batch_id)
);
-- statement-break
CREATE INDEX idx_inventory_risk_policy_item ON inventory_risk_policies(item_id, effective_from, effective_to);
