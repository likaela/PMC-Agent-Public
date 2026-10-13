"""企业问答会话存储；与 Demo/MemorySaver 隔离，不写入业务事实。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from repositories.fact_store import PersistentFactStore


class SessionNotFound(ValueError):
    pass


class SessionConflict(ValueError):
    pass


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class AgentConversationRepository:
    def __init__(self, store: PersistentFactStore) -> None:
        self.store = store

    def create_session(self, *, initial_context: dict | None = None) -> dict:
        session_id, conversation_id = str(uuid4()), str(uuid4())
        now = datetime.now(UTC).isoformat()
        with self.store.transaction(write_lock=True) as connection:
            self.store.execute(connection,
                "INSERT INTO agent_sessions (id, created_at, updated_at, context_json) VALUES (?, ?, ?, ?)",
                (session_id, now, now, _json(initial_context if initial_context is not None else
                                          {"fact": {}, "production": {}, "active_domain": None})))
            self.store.execute(connection,
                "INSERT INTO agent_conversations (id, session_id, created_at) VALUES (?, ?, ?)",
                (conversation_id, session_id, now))
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict:
        with self.store.transaction() as connection:
            row = self.store.execute(connection,
                "SELECT s.id, c.id, s.context_json, s.context_version, s.created_at, s.updated_at "
                "FROM agent_sessions s JOIN agent_conversations c ON c.session_id = s.id WHERE s.id = ?",
                (session_id,)).fetchone()
        if row is None:
            raise SessionNotFound("会话不存在")
        return dict(session_id=str(row[0]), conversation_id=str(row[1]), context=json.loads(row[2]),
                    context_version=int(row[3]), created_at=str(row[4]), updated_at=str(row[5]))

    def append_turn(self, session: dict, message: str, response: dict, context: dict, references: list[dict]) -> str:
        """一次事务保存上下文、问答和调用证据；并发旧上下文提交明确拒绝。"""
        now, turn_id = datetime.now(UTC).isoformat(), str(uuid4())
        version = session["context_version"]
        with self.store.transaction(write_lock=True) as connection:
            updated = self.store.execute(connection,
                "UPDATE agent_sessions SET context_json = ?, context_version = context_version + 1, updated_at = ? "
                "WHERE id = ? AND context_version = ?",
                (_json(context), now, session["session_id"], version))
            if updated.rowcount != 1:
                raise SessionConflict("会话已有新消息，请重试")
            self.store.execute(connection,
                "INSERT INTO agent_turns (id, conversation_id, sequence_no, user_query, response_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (turn_id, session["conversation_id"], version + 1, message, _json({**response, "turn_id": turn_id}), now))
            for reference in references:
                self.store.execute(connection,
                    "INSERT INTO agent_fact_references (id, turn_id, tool_name, query_json, result_json, provenance_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (str(uuid4()), turn_id, reference["tool_name"], _json(reference["query"]),
                     _json(reference["result"]), _json(reference["result"].get("provenance"))))
        return turn_id

    def history(self, session_id: str, *, after: int = 0, limit: int = 50) -> dict:
        session = self.get_session(session_id)
        with self.store.transaction() as connection:
            rows = self.store.execute(connection,
                "SELECT id, sequence_no, user_query, response_json, created_at FROM agent_turns "
                "WHERE conversation_id = ? AND sequence_no > ? ORDER BY sequence_no LIMIT ?",
                (session["conversation_id"], after, limit)).fetchall()
            turns = []
            for row in rows:
                references = self.store.execute(connection,
                    "SELECT tool_name, query_json, result_json, provenance_json FROM agent_fact_references "
                    "WHERE turn_id = ? ORDER BY id", (row[0],)).fetchall()
                turns.append(dict(turn_id=str(row[0]), sequence_no=int(row[1]), user_query=row[2],
                                  response=json.loads(row[3]), created_at=str(row[4]),
                                  fact_references=[dict(tool_name=ref[0], query=json.loads(ref[1]),
                                                        result=json.loads(ref[2]), provenance=json.loads(ref[3]))
                                                   for ref in references]))
        return {**session, "turns": turns}
