"""真实 MRP 的 Agent 工具适配层。"""

from collections.abc import Mapping

from repositories.real_inventory_repository import (
    build_real_inventory_scope,
    exclude_inventory_data_errors,
)
from services.mrp_service import analyze_mrp


def analyze_real_mrp(bom_requirements: Mapping[str, int]) -> dict[str, object]:
    """按一期库存口径执行真实 MRP；所有计算由 MRP 服务完成。"""
    mrp_requirements, excluded_materials = exclude_inventory_data_errors(bom_requirements)
    inventory_scope = build_real_inventory_scope(mrp_requirements)
    return {
        "mrp_result": analyze_mrp(mrp_requirements, inventory_scope),
        "data_quality_status": {
            "source_policy": "一期可用库存仅使用已清洗的主库存表。",
            "reserved": 0,
            "incoming": 0,
            "inventory_data_error_exclusions": excluded_materials,
        },
    }


__all__ = ["analyze_real_mrp"]
