"""真实 Excel 适配器共用的基础清洗函数。"""


def normalize_material_code(value: object) -> str:
    """将 Excel 中的文本或数值料号规范为无小数点的字符串。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        raise ValueError("料号不能为布尔值")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


def normalize_nonnegative_number(value: object, field_name: str) -> int | float:
    """校验并转换原始 Excel 中的非负数值，保留小数库存精度。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} 必须是非负数值")
    if value < 0:
        raise ValueError(f"{field_name} 必须是非负数值")
    return int(value) if float(value).is_integer() else float(value)


def normalize_nonnegative_int(value: object, field_name: str) -> int:
    """校验必须按整数计量的字段，例如 BOM 单位用量。"""
    normalized = normalize_nonnegative_number(value, field_name)
    if not isinstance(normalized, int):
        raise ValueError(f"{field_name} 必须是非负整数")
    return normalized


def normalize_header(value: object) -> str:
    """去除 Excel 表头的普通空格和全角空格。"""
    return str(value or "").replace(" ", "").replace("　", "").strip()
