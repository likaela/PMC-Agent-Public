"""独立 v1.11 Tool 列表；不改变现有 Agent、FACT_QUERY_TOOLS 或 Demo。"""

from __future__ import annotations

from collections.abc import Callable

from langchain.tools import tool

from fact_layer.query_models import FactQueryResult
from repositories.fact_repository import FactRepository
from repositories.fact_store import create_fact_store
from repositories.inventory_analysis_repository import InventoryAnalysisRepository
from services.published_inventory_analysis_service import (
    AvailabilityBasis,
    PublishedInventoryAnalysisService,
)


def _service() -> PublishedInventoryAnalysisService:
    return PublishedInventoryAnalysisService(InventoryAnalysisRepository(FactRepository(create_fact_store())))


def _safe(operation: Callable[[], FactQueryResult], query: dict) -> FactQueryResult:
    try:
        return operation()
    except Exception:
        return {"status": "query_failed", "query": query, "data": None,
                "completeness": {}, "provenance": None,
                "warnings": ["批次分析事实查询暂不可用，未生成推断值"]}


@tool
def published_inventory_risk_analysis(
    material_code: str,
    as_of: str | None = None,
    availability_basis: AvailabilityBasis = "source_only",
    expiry_warning_days: int | None = None,
    stagnant_days: int | None = None,
) -> FactQueryResult:
    """查询 published 批次、预留、安全库存、临期和库龄风险；缺少权威事实不作判断。"""
    query = dict(material_code=material_code, as_of=as_of, availability_basis=availability_basis,
                 expiry_warning_days=expiry_warning_days, stagnant_days=stagnant_days)
    return _safe(lambda: _service().analyze(**query), query)


@tool
def published_inventory_fifo_plan(
    material_code: str,
    required_qty: str,
    as_of: str | None = None,
    availability_basis: AvailabilityBasis = "source_only",
) -> FactQueryResult:
    """按明确入库时间模拟 FIFO 批次扣减；需求量使用十进制字符串，不实际写入库存。"""
    query = dict(material_code=material_code, required_qty=required_qty, as_of=as_of,
                 availability_basis=availability_basis)
    return _safe(lambda: _service().fifo(**query), query)


@tool
def published_supply_by_start_query(
    material_code: str,
    start_date: str,
    as_of: str | None = None,
    availability_basis: AvailabilityBasis = "source_only",
) -> FactQueryResult:
    """只按 published 在途事实及明确到货日期筛选开工日前供给，不生成采购或生产决策。"""
    query = dict(material_code=material_code, start_date=start_date, as_of=as_of,
                 availability_basis=availability_basis)
    return _safe(lambda: _service().planned_supply(**query), query)


INVENTORY_ANALYSIS_TOOLS = [
    published_inventory_risk_analysis,
    published_inventory_fifo_plan,
    published_supply_by_start_query,
]


__all__ = ["INVENTORY_ANALYSIS_TOOLS", "published_inventory_risk_analysis",
           "published_inventory_fifo_plan", "published_supply_by_start_query"]
