"""独立 v1.12 采购建议 Tool，暂不加入 Agent、Router 或 Demo 工具清单。"""

from __future__ import annotations

from langchain.tools import tool

from fact_layer.query_models import FactQueryResult
from repositories.fact_repository import FactRepository
from repositories.fact_store import create_fact_store
from repositories.published_purchase_repository import PublishedPurchaseRepository
from services.published_inventory_analysis_service import AvailabilityBasis
from services.published_purchase_planning_service import PublishedPurchasePlanningService


@tool
def published_purchase_recommendations_query(
    product: str,
    revision: str,
    effective_date: str,
    quantity: int,
    start_date: str,
    as_of: str | None = None,
    suppliers: dict[str, str] | None = None,
    availability_basis: AvailabilityBasis = "source_only",
) -> FactQueryResult:
    """基于 published 多层 BOM、库存、在途、安全库存和显式采购参数生成可追溯采购建议。"""
    query = dict(product=product, revision=revision, effective_date=effective_date, quantity=quantity,
                 start_date=start_date, as_of=as_of, suppliers=suppliers, availability_basis=availability_basis)
    try:
        # 只读查询不自动 migrate；不访问原始 Excel 或 LLM。
        repository = PublishedPurchaseRepository(FactRepository(create_fact_store()))
        return PublishedPurchasePlanningService(repository).query(**query)
    except Exception:
        return {"status": "query_failed", "query": query, "data": None, "completeness": {},
                "provenance": None, "warnings": ["采购事实分析暂不可用，未生成推断数量或成本"]}


PUBLISHED_PURCHASE_TOOLS = [published_purchase_recommendations_query]


__all__ = ["PUBLISHED_PURCHASE_TOOLS", "published_purchase_recommendations_query"]
