"""当前进程内的事实查询上下文；与 PMC Workflow 会话严格分离。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from uuid import uuid4


_CONTEXT_TTL = timedelta(hours=1)
_MAX_CONTEXTS = 512


@dataclass(frozen=True)
class FactDialogueContext:
    material_code: str
    query_type: str
    revision: str | None = None
    as_of: str | None = None


_contexts: dict[str, tuple[FactDialogueContext, datetime]] = {}
_lock = RLock()


def _prune(now: datetime) -> None:
    expired = [key for key, (_, touched) in _contexts.items() if now - touched > _CONTEXT_TTL]
    for key in expired:
        _contexts.pop(key, None)
    if len(_contexts) > _MAX_CONTEXTS:
        oldest = sorted(_contexts, key=lambda key: _contexts[key][1])
        for key in oldest[: len(_contexts) - _MAX_CONTEXTS]:
            _contexts.pop(key, None)


def get_fact_dialogue_context(session_id: str | None) -> FactDialogueContext | None:
    """仅用独立 fact session id 读取，不接收 workflow_id。"""
    if not session_id:
        return None
    with _lock:
        now = datetime.now(UTC)
        _prune(now)
        stored = _contexts.get(session_id)
        if stored is None:
            return None
        context, _ = stored
        _contexts[session_id] = (context, now)
        return context


def remember_fact_dialogue_context(
    session_id: str | None,
    *,
    material_code: str,
    query_type: str,
    revision: str | None = None,
    as_of: str | None = None,
) -> str:
    """保存显式查询对象或 Tool 已解析的 canonical item，不写入 PMC 状态。"""
    if not material_code.strip() or query_type not in {"inventory", "inventory_batches", "bom", "inbound"}:
        raise ValueError("事实查询上下文需要有效料号和查询类型")
    with _lock:
        now = datetime.now(UTC)
        _prune(now)
        key = session_id if session_id in _contexts else str(uuid4())
        _contexts[key] = (
            FactDialogueContext(
                material_code=material_code.strip(),
                query_type=query_type,
                revision=revision.strip() if revision else None,
                as_of=as_of.strip() if as_of else None,
            ),
            now,
        )
        _prune(now)
        return key


def clear_fact_dialogue_context(session_id: str | None) -> None:
    if session_id:
        with _lock:
            _contexts.pop(session_id, None)


__all__ = [
    "FactDialogueContext",
    "clear_fact_dialogue_context",
    "get_fact_dialogue_context",
    "remember_fact_dialogue_context",
]
