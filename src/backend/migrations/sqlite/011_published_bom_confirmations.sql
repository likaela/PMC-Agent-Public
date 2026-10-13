-- Append-only human confirmations; original import/staging provenance is unchanged.
CREATE TABLE published_bom_confirmations (
    id TEXT PRIMARY KEY,
    confirmation_key TEXT NOT NULL UNIQUE,
    bom_header_id TEXT NOT NULL REFERENCES bom_headers(id),
    confirmed_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_uri TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
-- statement-break
CREATE INDEX idx_bom_confirmation_history ON published_bom_confirmations(bom_header_id, recorded_at);
