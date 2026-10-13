"""当前进程内的 Chat 会话历史。

仅保存展示层消息与工作流关联，不保存业务计算结果，也不提供持久化。
"""

from __future__ import annotations

from threading import RLock
from typing import TypedDict


class ChatHistoryItem(TypedDict):
    role: str
    content: str
    turn_type: str
    workflow_id: str


_history_by_workflow: dict[str, list[ChatHistoryItem]] = {}
_history_lock = RLock()


def append_chat_history(
    conversation_workflow_id: str,
    *,
    role: str,
    content: str,
    turn_type: str,
    workflow_id: str,
) -> None:
    """向当前工作流会话追加一条已脱敏的展示消息。"""
    item: ChatHistoryItem = {
        "role": role,
        "content": content,
        "turn_type": turn_type,
        "workflow_id": workflow_id,
    }
    with _history_lock:
        _history_by_workflow.setdefault(conversation_workflow_id, []).append(item)


def get_chat_history(conversation_workflow_id: str) -> list[ChatHistoryItem]:
    """返回消息副本，避免调用方修改内存会话。"""
    with _history_lock:
        return [item.copy() for item in _history_by_workflow.get(conversation_workflow_id, [])]
