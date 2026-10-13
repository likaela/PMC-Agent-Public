-- Unknown BOM validity is NULL, never a fabricated date. Existing dated facts
-- retain their dates, keys, provenance, indexes, and foreign-key references.
CREATE TABLE bom_headers_structural (
    id TEXT PRIMARY KEY,
    parent_material_code TEXT NOT NULL,
    version TEXT NOT NULL,
    effective_from TEXT,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    source_sheet TEXT NOT NULL,
    effective_to TEXT,
    UNIQUE(parent_material_code, version)
);
-- statement-break
INSERT INTO bom_headers_structural SELECT id, parent_material_code, version,
    effective_from, import_batch_id, source_file_id, source_sheet, effective_to FROM bom_headers;
-- statement-break
DROP TABLE bom_headers;
-- statement-break
ALTER TABLE bom_headers_structural RENAME TO bom_headers;
-- statement-break
CREATE INDEX idx_bom_headers_lookup ON bom_headers(parent_material_code, version);
-- statement-break
CREATE INDEX idx_bom_headers_effective ON bom_headers(parent_material_code, effective_from, effective_to);
-- statement-break
ALTER TABLE staged_bom_headers ADD COLUMN publication_scope TEXT NOT NULL
    DEFAULT 'full' CHECK (publication_scope IN ('full', 'structure'));
