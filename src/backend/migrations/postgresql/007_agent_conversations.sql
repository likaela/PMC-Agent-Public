CREATE TABLE agent_sessions (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    context_json TEXT NOT NULL,
    context_version INTEGER NOT NULL DEFAULT 0
);
-- statement-break
CREATE TABLE agent_conversations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE REFERENCES agent_sessions(id),
    created_at TIMESTAMPTZ NOT NULL
);
-- statement-break
CREATE TABLE agent_turns (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES agent_conversations(id),
    sequence_no INTEGER NOT NULL,
    user_query TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(conversation_id, sequence_no)
);
-- statement-break
CREATE TABLE agent_fact_references (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL REFERENCES agent_turns(id),
    tool_name TEXT NOT NULL,
    query_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL
);
