-- Unknown units remain SQL NULL. Structural publication is an explicit import
-- policy; calculation services must continue to reject missing unit facts.
-- SQLiteFactStore runs this table rebuild in one transaction, with FK checking
-- disabled before BEGIN and foreign_key_check before COMMIT.
CREATE TABLE items_nullable_units (
    id TEXT PRIMARY KEY,
    material_code TEXT NOT NULL UNIQUE,
    name TEXT,
    specification TEXT,
    unit TEXT,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_sheet TEXT NOT NULL,
    source_row_number INTEGER NOT NULL
);
-- statement-break
INSERT INTO items_nullable_units
    (id, material_code, name, specification, unit, import_batch_id,
     source_file_id, source_sheet, source_row_number)
SELECT id, material_code, name, specification, unit, import_batch_id,
       source_file_id, source_sheet, source_row_number FROM items;
-- statement-break
DROP TABLE items;
-- statement-break
ALTER TABLE items_nullable_units RENAME TO items;
-- statement-break
CREATE TABLE item_revisions_nullable_units (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES items(id),
    name TEXT,
    specification TEXT,
    unit TEXT,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_sheet TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    UNIQUE(item_id, import_batch_id)
);
-- statement-break
INSERT INTO item_revisions_nullable_units
    (id, item_id, name, specification, unit, import_batch_id,
     source_file_id, source_sheet, source_row_number)
SELECT id, item_id, name, specification, unit, import_batch_id,
       source_file_id, source_sheet, source_row_number FROM item_revisions;
-- statement-break
DROP TABLE item_revisions;
-- statement-break
ALTER TABLE item_revisions_nullable_units RENAME TO item_revisions;
-- statement-break
CREATE INDEX idx_item_revisions_item ON item_revisions(item_id);
-- statement-break
CREATE TABLE bom_lines_nullable_units (
    id TEXT PRIMARY KEY,
    bom_header_id TEXT NOT NULL REFERENCES bom_headers(id),
    item_id TEXT NOT NULL REFERENCES items(id),
    quantity TEXT NOT NULL,
    unit TEXT,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_sheet TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    UNIQUE(bom_header_id, item_id)
);
-- statement-break
INSERT INTO bom_lines_nullable_units
    (id, bom_header_id, item_id, quantity, unit, import_batch_id,
     source_file_id, source_sheet, source_row_number)
SELECT id, bom_header_id, item_id, quantity, unit, import_batch_id,
       source_file_id, source_sheet, source_row_number FROM bom_lines;
-- statement-break
DROP TABLE bom_lines;
-- statement-break
ALTER TABLE bom_lines_nullable_units RENAME TO bom_lines;
-- statement-break
ALTER TABLE staged_bom_headers ADD COLUMN unit_policy TEXT NOT NULL
    DEFAULT 'required' CHECK (unit_policy IN ('required', 'optional_for_structure'));
