ALTER TABLE bom_headers ALTER COLUMN effective_from DROP NOT NULL;
-- statement-break
ALTER TABLE staged_bom_headers ADD COLUMN publication_scope TEXT NOT NULL
    DEFAULT 'full' CHECK (publication_scope IN ('full', 'structure'));
