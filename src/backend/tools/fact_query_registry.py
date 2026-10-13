"""独立的 published facts Tool Registry；不加入生产 Demo 工具链。"""

from __future__ import annotations

from types import MappingProxyType
from typing import Literal

from langchain_core.tools import BaseTool

from tools.fact_query_tools import FACT_QUERY_TOOLS


FactQueryKind = Literal["inventory", "inventory_batches", "bom", "inbound"]

_TOOL_NAMES: dict[FactQueryKind, str] = {
    "inventory": "published_inventory_query",
    "inventory_batches": "published_inventory_batch_query",
    "bom": "published_bom_query",
    "inbound": "published_inbound_supply_query",
}
_registered_tools = {tool.name: tool for tool in FACT_QUERY_TOOLS}
if set(_registered_tools) != set(_TOOL_NAMES.values()):
    raise RuntimeError("事实查询 Tool Registry 与 FACT_QUERY_TOOLS 不一致")

FACT_QUERY_TOOL_REGISTRY = MappingProxyType(
    {kind: _registered_tools[name] for kind, name in _TOOL_NAMES.items()}
)


def get_fact_query_tool(kind: FactQueryKind) -> BaseTool:
    """只能返回四个受控 published-fact 查询 Tool。"""
    return FACT_QUERY_TOOL_REGISTRY[kind]


__all__ = ["FACT_QUERY_TOOL_REGISTRY", "FactQueryKind", "get_fact_query_tool"]
