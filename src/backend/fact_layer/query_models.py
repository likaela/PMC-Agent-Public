"""受控事实查询 Tool 的稳定公开结构。"""

from __future__ import annotations

from typing import Any, TypedDict


class FactQueryResult(TypedDict):
    """所有事实查询 Tool 共享的顶层契约。"""

    status: str
    query: dict[str, Any]
    data: dict[str, Any] | None
    completeness: dict[str, bool]
    provenance: dict[str, Any] | list[dict[str, Any]] | None
    warnings: list[str]


__all__ = ["FactQueryResult"]
