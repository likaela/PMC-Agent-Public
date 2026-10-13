"""BOM 展开业务计算。"""

from collections.abc import Mapping


def explode_bom(
    product: str,
    quantity: int,
    bom_data: Mapping[str, Mapping[str, int]],
) -> dict[str, int]:
    """递归展开 BOM 并汇总最终叶子物料的需求。"""
    if product not in bom_data:
        raise ValueError(f"没有找到产品 {product} 的 BOM")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise ValueError("生产数量必须是正整数")

    result: dict[str, int] = {}

    def expand(material: str, required_quantity: int, path: tuple[str, ...]) -> None:
        if material in path:
            cycle = " -> ".join((*path, material))
            raise ValueError(f"检测到循环 BOM：{cycle}")

        children = bom_data.get(material)
        if not children:
            result[material] = result.get(material, 0) + required_quantity
            return

        next_path = (*path, material)
        for child_material, qty_per in children.items():
            if (
                not isinstance(child_material, str)
                or not isinstance(qty_per, int)
                or isinstance(qty_per, bool)
                or qty_per <= 0
            ):
                raise ValueError(f"物料 {material} 的 BOM 数据无效")
            expand(child_material, required_quantity * qty_per, next_path)

    expand(product, quantity, ())
    return result
