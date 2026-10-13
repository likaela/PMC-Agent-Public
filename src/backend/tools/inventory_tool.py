"""供 Agent 调用的库存工具适配层。"""

from collections.abc import Mapping

from repositories.inventory_repository import load_inventory_data
from services.inventory_service import check_inventory as calculate_inventory


def check_inventory(materials: Mapping[str, int]) -> dict[str, dict[str, int]]:
    """读取库存主数据并计算可用量和缺口。"""
    return calculate_inventory(materials, load_inventory_data())


__all__ = ["check_inventory"]
