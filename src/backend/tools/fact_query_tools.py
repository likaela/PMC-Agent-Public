"""供后续 Agent 使用的受控 published facts 查询 Tool。

这些 Tool 只做参数适配和查询服务调用，不读取 raw Excel，不执行 PMC 决策。
为避免改变现有 Demo Agent 行为，本阶段通过独立 ``FACT_QUERY_TOOLS`` 注册表暴露。
"""

from __future__ import annotations

from collections.abc import Callable

from langchain.tools import tool

from fact_layer.query_models import FactQueryResult
from repositories.fact_query_repository import FactQueryRepository
from repositories.fact_repository import FactRepository
from repositories.fact_store import create_fact_store
from services.fact_query_service import FactQueryService


def _build_query_service() -> FactQueryService:
    # 查询阶段不自动 migrate，避免只读 Tool 隐式修改数据库。
    fact_repository = FactRepository(create_fact_store())
    return FactQueryService(FactQueryRepository(fact_repository))


def _safe_query(
    operation: Callable[[], FactQueryResult],
    query: dict[str, object],
) -> FactQueryResult:
    try:
        return operation()
    except Exception:
        return {
            "status": "query_failed",
            "query": query,
            "data": None,
            "completeness": {},
            "provenance": None,
            "warnings": ["事实存储查询暂时失败，未返回任何推断值"],
        }


@tool
def published_inventory_query(
    material_code: str,
    as_of: str | None = None,
) -> FactQueryResult:
    """精确查询 canonical item 或 alias 的已发布库存汇总；as_of 为带时区 ISO 时间。"""
    query = {"material_code": material_code, "as_of": as_of}
    return _safe_query(
        lambda: _build_query_service().query_inventory(material_code, as_of),
        query,
    )


@tool
def published_inventory_batch_query(
    material_code: str,
    as_of: str | None = None,
) -> FactQueryResult:
    """查询已发布库存的批次与库位明细；不会生成 FIFO、临期或呆滞建议。"""
    query = {"material_code": material_code, "as_of": as_of}
    return _safe_query(
        lambda: _build_query_service().query_inventory_batches(material_code, as_of),
        query,
    )


@tool
def published_bom_query(
    parent_material_code: str,
    revision: str,
) -> FactQueryResult:
    """按父件和版本查询已发布 BOM 行、价格与来源，不读取原始 Excel。"""
    query = {"parent_material_code": parent_material_code, "revision": revision}
    return _safe_query(
        lambda: _build_query_service().query_bom(parent_material_code, revision),
        query,
    )


@tool
def published_inbound_supply_query(
    material_code: str,
    as_of: str | None = None,
) -> FactQueryResult:
    """查询已发布权威在途事实；无事实时明确返回 data_unavailable。"""
    query = {"material_code": material_code, "as_of": as_of}
    return _safe_query(
        lambda: _build_query_service().query_inbound(material_code, as_of),
        query,
    )


FACT_QUERY_TOOLS = [
    published_inventory_query,
    published_inventory_batch_query,
    published_bom_query,
    published_inbound_supply_query,
]


__all__ = [
    "FACT_QUERY_TOOLS",
    "published_bom_query",
    "published_inbound_supply_query",
    "published_inventory_batch_query",
    "published_inventory_query",
]
