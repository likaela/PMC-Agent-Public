ALTER TABLE price_records ADD COLUMN unit TEXT;
-- statement-break
ALTER TABLE price_records ADD COLUMN supplier_code TEXT;
-- statement-break
ALTER TABLE price_records ADD COLUMN effective_to DATE;
-- statement-break
CREATE TABLE purchase_parameter_facts (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES items(id),
    supplier_code TEXT,
    purchase_unit TEXT,
    minimum_order_quantity NUMERIC(38, 12) CHECK (minimum_order_quantity >= 0),
    order_multiple NUMERIC(38, 12) CHECK (order_multiple > 0),
    packaging_multiple NUMERIC(38, 12) CHECK (packaging_multiple > 0),
    order_multiple_applicable BOOLEAN,
    packaging_multiple_applicable BOOLEAN,
    lead_time_days INTEGER CHECK (lead_time_days >= 0),
    price_record_id TEXT REFERENCES price_records(id),
    effective_from DATE NOT NULL,
    effective_to DATE,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_sheet TEXT NOT NULL,
    source_row_number INTEGER NOT NULL CHECK (source_row_number > 0),
    UNIQUE(item_id, supplier_code, effective_from, import_batch_id)
);
-- statement-break
CREATE INDEX idx_purchase_parameter_item ON purchase_parameter_facts(item_id, supplier_code, effective_from, effective_to);
