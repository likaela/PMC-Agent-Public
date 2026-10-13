"""Bind existing analysis services to the conversation's store, never to Demo/global facts."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from repositories.agent_context_repository import AgentContextRepository
from repositories.fact_query_repository import FactQueryRepository
from repositories.fact_repository import FactRepository
from repositories.inventory_analysis_repository import InventoryAnalysisRepository
from services.published_inventory_analysis_service import PublishedInventoryAnalysisService
from tools.inventory_analysis_tools import published_inventory_fifo_plan, published_inventory_risk_analysis


class InventoryOperations:
    def __init__(self, store):
        facts = FactRepository(store)
        self.context_repository = AgentContextRepository(FactQueryRepository(facts))
        service = PublishedInventoryAnalysisService(InventoryAnalysisRepository(facts))

        def analyze(**kwargs):
            return service.analyze(**kwargs)

        def fifo(**kwargs):
            return service.fifo(**kwargs)

        self.tools = {
            name: StructuredTool.from_function(func=fn, name=original.name,
                description=original.description, args_schema=original.args_schema)
            for name, fn, original in (
                ("inventory_risk", analyze, published_inventory_risk_analysis),
                ("inventory_fifo", fifo, published_inventory_fifo_plan))}

    def resolve(self, name):
        if name == "inventory_risk_analysis":
            name = "inventory_risk"  # Compatibility alias; same store-bound, existing analysis.
        return self.tools[name]

    def prepare(self, plan):
        if plan.entities.as_of is None:
            snapshot = self.context_repository.latest_inventory_time()
            if snapshot:
                plan = plan.model_copy(deep=True)
                plan.entities.as_of = str(snapshot["snapshot_at"])
        return plan
