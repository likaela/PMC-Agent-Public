"""显式绑定场景库的 Tool 工厂；不修改全局工具列表或数据库环境变量。"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from repositories.fact_query_repository import FactQueryRepository
from repositories.published_purchase_repository import PublishedPurchaseRepository
from repositories.demo_scenario_repository import SCENARIO_ID, DemoScenarioRepository
from services.fact_query_service import FactQueryService
from services.production_feasibility_query_service import ProductionFeasibilityQueryService
from services.published_purchase_planning_service import PublishedPurchasePlanningService


def build_demo_scenario_tools(repository: DemoScenarioRepository) -> dict[str, StructuredTool]:
    facts = FactQueryService(FactQueryRepository(repository.facts))
    capacity = ProductionFeasibilityQueryService(facts)
    purchase = PublishedPurchasePlanningService(PublishedPurchaseRepository(repository.facts))

    def tagged(result):
        # 只添加来源标签，不改任何计算值、完整性或业务状态。
        return {**result, "provenance": {**(result.get("provenance") or {}),
                                       "scenario_id": SCENARIO_ID, "synthetic": True}}

    def inventory(material_code: str, as_of: str | None = None) -> dict:
        """查询隔离场景的 published 库存。"""
        return tagged(facts.query_inventory(material_code, as_of))

    def inventory_batches(material_code: str, as_of: str | None = None) -> dict:
        """查询隔离场景的 published 批次。"""
        return tagged(facts.query_inventory_batches(material_code, as_of))

    def bom(parent_material_code: str, revision: str) -> dict:
        """查询隔离场景的 published BOM。"""
        return tagged(facts.query_bom(parent_material_code, revision))

    def inbound(material_code: str, as_of: str | None = None) -> dict:
        """查询隔离场景的显式合成采购记录。"""
        return tagged(facts.query_inbound(material_code, as_of))

    def production_capacity(product: str, bom_version: str, requested_quantity: int | None = None,
                            as_of: str | None = None, effective_date: str | None = None) -> dict:
        """复用现有多层 BOM/库存齐套与理论产能计算。"""
        return tagged(capacity.query(product, bom_version, requested_quantity=requested_quantity,
                                     as_of=as_of, effective_date=effective_date))

    def purchase_planning(product: str, revision: str, effective_date: str, quantity: int,
                          start_date: str, as_of: str | None = None) -> dict:
        """复用现有 published 采购规划算法。"""
        return tagged(purchase.query(product, revision, effective_date, quantity, start_date, as_of=as_of))

    return {kind: StructuredTool.from_function(function, name=f"demo_{kind}_query") for kind, function in
            dict(inventory=inventory, inventory_batches=inventory_batches, bom=bom, inbound=inbound,
                 production_capacity=production_capacity, purchase_planning=purchase_planning).items()}
