"""真实 BOM 的 Agent 工具适配层。"""

from repositories.real_bom_repository import load_real_bom_data, load_real_bom_relationships
from services.bom_service import explode_bom


def explode_real_bom(product: str, bom_version: str, quantity: int) -> dict[str, object]:
    """从真实 BOM 元数据验证产品与版本，并由服务层展开需求。"""
    payload = load_real_bom_relationships(
        expected_parent_material=product,
        expected_version=bom_version,
    )
    return {
        "product": product,
        "bom_version": payload["version"],
        "quantity": quantity,
        "bom_requirements": explode_bom(product, quantity, load_real_bom_data()),
    }


__all__ = ["explode_real_bom"]
