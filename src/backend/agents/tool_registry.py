"""Thin, scoped aliases for existing published-fact tools; no business algorithms."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType


TOOL_KINDS = MappingProxyType({
    "inventory_query": "inventory",
    "batch_inventory_query": "inventory_batches",
    "bom_query": "bom",
    "production_analysis": "production_capacity",
    "purchase_plan": "purchase_planning",
    "inventory_risk_analysis": "inventory",
    "inventory_risk": "inventory",
    "inventory_fifo": "inventory_batches",
    "published_product_search": "fact_query",
})
PRIMARY_TOOLS = MappingProxyType({kind: name for name, kind in TOOL_KINDS.items()
                                if name not in {"inventory_risk_analysis", "inventory_risk", "inventory_fifo", "published_product_search"}})
REQUIRED_PARAMETERS = MappingProxyType({
    "inventory_query": ("material_code",),
    "batch_inventory_query": ("material_code",),
    "bom_query": ("material_code", "revision"),
    "production_analysis": ("material_code", "revision", "effective_date"),
    "purchase_plan": ("material_code", "revision", "effective_date", "quantity", "start_date"),
    "inventory_risk_analysis": ("material_code", "as_of"),
    "inventory_risk": ("material_code", "as_of"),
    "inventory_fifo": ("material_code", "as_of", "required_qty"),
    "published_product_search": (),
})


class ToolRegistry:
    def __init__(self, *, resolver: Callable | None = None, scope: str = "enterprise", analysis_resolver: Callable | None = None, product_resolver: Callable | None = None) -> None:
        if scope != "enterprise" and resolver is None:
            raise ValueError("An isolated scope requires its own bound tool resolver")
        self.scope = scope
        self._resolver = resolver
        self._analysis_resolver = analysis_resolver
        self._product_resolver = product_resolver

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name in TOOL_KINDS if self.scope == "enterprise" or name not in {"inventory_risk_analysis", "inventory_risk", "inventory_fifo", "published_product_search"})

    def resolve(self, name: str):
        kind = TOOL_KINDS[name]  # Closed whitelist; never eval/import a model-provided name.
        if name == "published_product_search":
            if self.scope != "enterprise":
                raise ValueError("Enterprise product discovery is not enabled in Demo")
            if self._product_resolver is not None:
                return self._product_resolver()
            from tools.published_product_tools import build_published_product_search
            from repositories.fact_store import create_fact_store
            return build_published_product_search(create_fact_store())
        if name in {"inventory_risk_analysis", "inventory_risk", "inventory_fifo"}:
            if self.scope != "enterprise":
                raise ValueError("Enterprise analysis tools are not enabled in Demo")
            if self._analysis_resolver is not None:
                return self._analysis_resolver(name)
            from tools.inventory_analysis_tools import published_inventory_risk_analysis, published_inventory_fifo_plan
            return {"inventory_risk_analysis": published_inventory_risk_analysis,
                    "inventory_risk": published_inventory_risk_analysis,
                    "inventory_fifo": published_inventory_fifo_plan}[name]
        if self._resolver is not None:
            return self._resolver(kind)
        if kind in {"inventory", "inventory_batches", "bom"}:
            from tools.fact_query_registry import get_fact_query_tool
            return get_fact_query_tool(kind)
        if kind == "production_capacity":
            from tools.production_feasibility_query_tool import published_production_capacity_query
            return published_production_capacity_query
        from tools.published_purchase_tools import published_purchase_recommendations_query
        return published_purchase_recommendations_query

    @staticmethod
    def arguments(name: str, entities: Mapping) -> dict:
        if name not in TOOL_KINDS:
            raise ValueError("Unregistered tool")
        missing = [key for key in REQUIRED_PARAMETERS[name] if entities.get(key) is None]
        if missing:
            raise ValueError("Missing required tool parameters")
        code, revision = entities.get("material_code"), entities.get("revision")
        if name == "published_product_search":
            return dict(search_term=entities.get("search_term"))
        if name in {"inventory_risk_analysis", "inventory_risk", "inventory_fifo"}:
            args = dict(material_code=code, as_of=entities.get("as_of"))
            if name == "inventory_fifo":
                args["required_qty"] = entities["required_qty"]
            else:
                args.update(expiry_warning_days=entities.get("expiry_warning_days"), stagnant_days=entities.get("stagnant_days"))
            return args
        if name in {"inventory_query", "batch_inventory_query"}:
            return dict(material_code=code, as_of=entities.get("as_of"))
        if name == "bom_query":
            return dict(parent_material_code=code, revision=revision)
        if name == "production_analysis":
            return dict(product=code, bom_version=revision, effective_date=entities.get("effective_date"),
                        requested_quantity=entities.get("quantity"), as_of=entities.get("as_of"))
        return dict(product=code, revision=revision, effective_date=entities.get("effective_date"),
                    quantity=entities.get("quantity"), start_date=entities.get("start_date"),
                    as_of=entities.get("as_of"))


__all__ = ["ToolRegistry", "TOOL_KINDS", "PRIMARY_TOOLS", "REQUIRED_PARAMETERS"]
