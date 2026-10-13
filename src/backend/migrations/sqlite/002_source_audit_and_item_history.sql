ALTER TABLE source_files ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'local';
-- statement-break
ALTER TABLE source_files ADD COLUMN storage_key TEXT;
-- statement-break
ALTER TABLE source_files ADD COLUMN source_uri TEXT;
-- statement-break
ALTER TABLE source_files ADD COLUMN immutable_copy INTEGER NOT NULL DEFAULT 0;
-- statement-break
UPDATE source_files
SET source_uri = 'legacy://unresolved/' || id
WHERE source_uri IS NULL AND storage_key IS NULL;
-- statement-break
CREATE TABLE IF NOT EXISTS item_revisions (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES items(id),
    name TEXT,
    specification TEXT,
    unit TEXT NOT NULL,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_sheet TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    UNIQUE(item_id, import_batch_id)
);
-- statement-break
INSERT OR IGNORE INTO item_revisions (
    id, item_id, name, specification, unit, import_batch_id,
    source_file_id, source_sheet, source_row_number
)
SELECT
    'legacy-' || id || '-' || import_batch_id,
    id, name, specification, unit, import_batch_id,
    source_file_id, source_sheet, source_row_number
FROM items;
-- statement-break
CREATE INDEX IF NOT EXISTS idx_item_revisions_item ON item_revisions(item_id);
-- statement-break
CREATE INDEX IF NOT EXISTS idx_source_files_sha256 ON source_files(content_sha256);
