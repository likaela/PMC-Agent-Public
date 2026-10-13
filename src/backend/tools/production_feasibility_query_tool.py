"""独立受控产能查询 Tool；不加入现有 Demo/PMC Tool 列表。"""

from __future__ import annotations

from langchain.tools import tool

from repositories.fact_query_repository import FactQueryRepository
from repositories.fact_repository import FactRepository
from repositories.fact_store import create_fact_store
from services.fact_query_service import FactQueryService
from services.production_feasibility_query_service import ProductionFeasibilityQueryService


@tool
def published_production_capacity_query(
    product: str,
    bom_version: str,
    requested_quantity: int | None = None,
    requested_unit: str | None = None,
    as_of: str | None = None,
    effective_date: str | None = None,
) -> dict:
    """基于已发布 BOM 与权威可用库存查询理论产量；显式生效日期启用多层计算。"""
    query = {
        "product": product,
        "bom_version": bom_version,
        "requested_quantity": requested_quantity,
        "requested_unit": requested_unit,
        "as_of": as_of,
        "effective_date": effective_date,
    }
    try:
        repository = FactRepository(create_fact_store())
        facts = FactQueryService(FactQueryRepository(repository))
        return ProductionFeasibilityQueryService(facts).query(
            product, bom_version, requested_quantity=requested_quantity,
            requested_unit=requested_unit, as_of=as_of,
            effective_date=effective_date,
        )
    except Exception:
        return {
            "status": "query_failed",
            "query": query,
            "data": None,
            "completeness": {},
            "provenance": None,
            "warnings": ["生产能力事实查询暂不可用，未返回推断值"],
        }


__all__ = ["published_production_capacity_query"]
