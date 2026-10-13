"""Enterprise-only observation tools, separate from published availability tools."""
from langchain_core.tools import StructuredTool


def build_book_inventory_tools(service):
    def inventory(material_code: str) -> dict:
        return service.inventory(material_code)

    def demand(product: str, revision: str | None = None, quantity: int | None = None,
               requested_unit: str | None = None) -> dict:
        return service.analyze(product, revision, quantity, requested_unit)

    def capacity(product: str, revision: str | None = None) -> dict:
        from services.book_capacity_query_service import BookCapacityQueryService
        return BookCapacityQueryService(service).query(product, revision)

    return {
        "enterprise_book_capacity_query": StructuredTool.from_function(capacity,
            name="enterprise_book_capacity_query", description="按来源确认的Excel数量核算物料产量上限；明确完整/部分范围"),
        "enterprise_book_inventory_query": StructuredTool.from_function(inventory,
            name="enterprise_book_inventory_query", description="只读主库存Excel账面记录，不提供权威可用量"),
        "enterprise_book_demand_analysis": StructuredTool.from_function(demand,
            name="enterprise_book_demand_analysis", description="Published BOM需求与主库存记录部分核对，不批准生产"),
    }
