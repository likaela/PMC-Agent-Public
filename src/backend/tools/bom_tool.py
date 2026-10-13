"""供 Agent 调用的 BOM 工具适配层。"""

from repositories.bom_repository import load_bom_data
from services.bom_service import explode_bom as calculate_bom


def explode_bom(product: str, quantity: int) -> dict[str, int]:
    """读取 BOM 主数据并计算物料需求。"""
    return calculate_bom(product, quantity, load_bom_data())


__all__ = ["explode_bom"]
