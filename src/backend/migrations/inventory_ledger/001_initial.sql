-- Runtime inventory is separate from immutable released facts.
CREATE TABLE IF NOT EXISTS ledger_namespaces (
    namespace TEXT PRIMARY KEY,
    seed_hash TEXT NOT NULL,
    source_json TEXT NOT NULL,
    quantity_basis TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    initialized_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger_lots (
    namespace TEXT NOT NULL REFERENCES ledger_namespaces(namespace),
    lot_id TEXT NOT NULL,
    material_code TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    on_hand_qty TEXT,
    available_qty TEXT,
    original_json TEXT NOT NULL,
    PRIMARY KEY(namespace, lot_id),
    UNIQUE(namespace, material_code, ordinal)
);
CREATE TABLE IF NOT EXISTS ledger_previews (
    preview_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL REFERENCES ledger_namespaces(namespace),
    owner_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger_executions (
    task_id TEXT PRIMARY KEY,
    preview_id TEXT NOT NULL UNIQUE REFERENCES ledger_previews(preview_id),
    namespace TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    result_json TEXT NOT NULL,
    executed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES ledger_executions(task_id),
    namespace TEXT NOT NULL,
    material_code TEXT NOT NULL,
    lot_id TEXT NOT NULL,
    before_qty TEXT NOT NULL,
    deducted_qty TEXT NOT NULL,
    after_qty TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    executed_at TEXT NOT NULL
);
