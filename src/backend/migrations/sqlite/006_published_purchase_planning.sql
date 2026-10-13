ALTER TABLE price_records ADD COLUMN unit TEXT;
-- statement-break
ALTER TABLE price_records ADD COLUMN supplier_code TEXT;
-- statement-break
ALTER TABLE price_records ADD COLUMN effective_to TEXT;
-- statement-break
CREATE TABLE purchase_parameter_facts (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES items(id),
    supplier_code TEXT,
    purchase_unit TEXT,
    minimum_order_quantity TEXT,
    order_multiple TEXT,
    packaging_multiple TEXT,
    order_multiple_applicable INTEGER CHECK (order_multiple_applicable IN (0, 1)),
    packaging_multiple_applicable INTEGER CHECK (packaging_multiple_applicable IN (0, 1)),
    lead_time_days INTEGER CHECK (lead_time_days >= 0),
    price_record_id TEXT REFERENCES price_records(id),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_sheet TEXT NOT NULL,
    source_row_number INTEGER NOT NULL CHECK (source_row_number > 0),
    UNIQUE(item_id, supplier_code, effective_from, import_batch_id)
);
-- statement-break
CREATE INDEX idx_purchase_parameter_item ON purchase_parameter_facts(item_id, supplier_code, effective_from, effective_to);
