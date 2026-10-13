-- Opt-in structural BOM publication retains missing units as SQL NULL.
ALTER TABLE items ALTER COLUMN unit DROP NOT NULL;
-- statement-break
ALTER TABLE item_revisions ALTER COLUMN unit DROP NOT NULL;
-- statement-break
ALTER TABLE bom_lines ALTER COLUMN unit DROP NOT NULL;
-- statement-break
ALTER TABLE staged_bom_headers ADD COLUMN unit_policy TEXT NOT NULL
    DEFAULT 'required' CHECK (unit_policy IN ('required', 'optional_for_structure'));
