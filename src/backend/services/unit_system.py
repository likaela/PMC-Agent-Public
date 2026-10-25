"""统一单位解析与换算：一个入口，禁止跨物理维度直接运算。

设计边界（与既有业务口径一致）：
- 维度只有“可比较”与“不可比较”两种结论：计数、重量、长度、面积、体积、包装
  互不换算；重量或长度不能被换算成计数单位。
- 计数维度内，业务确认可与“片”一比一等价的单位（片/块/个/件/PCS）规范化到
  “片”；套/台/只/箱/包属于同一维度但**不等价**，必须由物料主数据、BOM 单耗或
  已有物料专属换算规则（unit_conversions）给出依据，否则拒绝换算。
- 重量基准为克，长度基准为毫米；全部使用 Decimal，避免浮点误差。
- 本模块只做解析与换算，不读取也不改写任何 Excel、企业库存库或发布事实。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext

COUNT = "count"
WEIGHT = "weight"
LENGTH = "length"
AREA = "area"
VOLUME = "volume"
PACKAGE = "package"

DIMENSION_LABELS = {
    COUNT: "计数",
    WEIGHT: "重量",
    LENGTH: "长度",
    AREA: "面积",
    VOLUME: "体积",
    PACKAGE: "包装",
}

# 业务确认缺失单位时的默认计数单位。
DEFAULT_COUNT_UNIT = "片"
# 业务允许进入统一计数体系、且可与“片”一比一等价的单位。
_EQUIVALENT_COUNT = ("片", "块", "个", "件", "pcs")

# (规范名, 维度, 到该维度基准单位的系数, 是否可与同基准计数单位等价互换)
_TABLE: dict[str, tuple[str, str, str, bool]] = {
    # ---- 计数
    "片": ("片", COUNT, "1", True),
    "块": ("片", COUNT, "1", True),
    "个": ("片", COUNT, "1", True),
    "件": ("片", COUNT, "1", True),
    "pcs": ("片", COUNT, "1", True),
    "套": ("套", COUNT, "1", False),
    "台": ("台", COUNT, "1", False),
    "只": ("只", COUNT, "1", False),
    # ---- 重量（基准：克）
    "吨": ("克", WEIGHT, "1000000", True),
    "千克": ("克", WEIGHT, "1000", True),
    "公斤": ("克", WEIGHT, "1000", True),
    "kg": ("克", WEIGHT, "1000", True),
    "斤": ("克", WEIGHT, "500", True),
    "克": ("克", WEIGHT, "1", True),
    "g": ("克", WEIGHT, "1", True),
    # ---- 长度（基准：毫米）
    "米": ("毫米", LENGTH, "1000", True),
    "m": ("毫米", LENGTH, "1000", True),
    "厘米": ("毫米", LENGTH, "10", True),
    "cm": ("毫米", LENGTH, "10", True),
    "毫米": ("毫米", LENGTH, "1", True),
    "mm": ("毫米", LENGTH, "1", True),
    "英寸": ("毫米", LENGTH, "25.4", True),
    "英尺": ("毫米", LENGTH, "304.8", True),
    # ---- 其他维度：可解析但只在自身维度内等价
    "平方米": ("平方米", AREA, "1", True),
    "平方厘米": ("平方厘米", AREA, "1", True),
    "立方米": ("立方米", VOLUME, "1", True),
    "升": ("毫升", VOLUME, "1000", True),
    "毫升": ("毫升", VOLUME, "1", True),
    "卷": ("卷", PACKAGE, "1", True),
    "箱": ("箱", PACKAGE, "1", True),
    "包": ("包", PACKAGE, "1", True),
}

#: 供调用方与测试枚举；键为可识别写法。
UNIT_ALIASES = tuple(sorted(_TABLE, key=lambda item: (-len(item), item)))
ALIASES = {item.lower(): item for item in _TABLE}


@dataclass(frozen=True)
class UnitSpec:
    """一个可识别的单位写法。"""

    raw: str
    name: str
    dimension: str
    factor: Decimal
    equivalent: bool

    @property
    def dimension_label(self) -> str:
        return DIMENSION_LABELS.get(self.dimension, self.dimension)


def spec(value) -> UnitSpec | None:
    """解析单位写法；无法识别返回 None（调用方必须按“单位未知”处理）。"""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    key = ALIASES.get(text.lower())
    if key is None:
        return None
    name, dimension, factor, equivalent = _TABLE[key]
    return UnitSpec(raw=text, name=name, dimension=dimension, factor=Decimal(factor), equivalent=equivalent)


def canonical(value) -> str | None:
    parsed = spec(value)
    return parsed.name if parsed else None


def dimension(value) -> str | None:
    parsed = spec(value)
    return parsed.dimension if parsed else None


def count_equivalent(value) -> bool:
    """是否为业务确认与“片”一比一等价的计数单位。"""
    parsed = spec(value)
    return bool(parsed and parsed.dimension == COUNT and parsed.equivalent and parsed.name == DEFAULT_COUNT_UNIT)


def compatible(left, right) -> bool:
    """两个单位能否直接比较/加减。

    只有同维度且落在同一基准单位时才允许；跨维度、未知单位、套/台等需物料
    专属规则的计数单位都返回 False。
    """
    one, two = spec(left), spec(right)
    if one is None or two is None:
        return False
    if one.dimension != two.dimension:
        return False
    return one.name == two.name


def conflict_reason(left, right) -> str | None:
    """返回不可比较的具体原因；可比较时返回 None。"""
    one, two = spec(left), spec(right)
    if one is None and two is None:
        return "双方计量单位都无法识别"
    if one is None:
        return f"缺少可核验的计量单位（对照单位 {two.name}）"
    if two is None:
        return f"缺少可核验的计量单位（对照单位 {one.name}）"
    if one.dimension != two.dimension:
        return f"{one.dimension_label}单位 {one.raw} 与{two.dimension_label}单位 {two.raw} 属于不同计量维度"
    if one.name != two.name:
        return (f"计数单位 {one.raw} 与 {two.raw} 没有一比一等价依据，"
                f"需要物料专属换算规则")
    return None


def to_base(amount, unit) -> Decimal | None:
    """换算到该维度基准单位；无法换算返回 None。"""
    parsed = spec(unit)
    value = _decimal(amount)
    if parsed is None or value is None:
        return None
    with localcontext() as context:
        context.prec = 28
        return value * parsed.factor


def convert(amount, source, target) -> Decimal | None:
    """同维度内的精确换算；跨维度或单位不可识别时返回 None。"""
    if not compatible(source, target):
        return None
    value = _decimal(amount)
    if value is None:
        return None
    one, two = spec(source), spec(target)
    with localcontext() as context:
        context.prec = 28
        return value * one.factor / two.factor


def difference(left_amount, left_unit, right_amount, right_unit) -> Decimal | None:
    """在共同基准单位上做减法；不可比较返回 None（绝不返回 0 冒充结果）。"""
    if not compatible(left_unit, right_unit):
        return None
    left = to_base(left_amount, left_unit)
    right = to_base(right_amount, right_unit)
    if left is None or right is None:
        return None
    return left - right


def resolve_missing(value, *, default: str = DEFAULT_COUNT_UNIT) -> tuple[str | None, str]:
    """区分“用户明确给出单位”和“缺失后按默认计数单位解释”。

    返回 (单位, 来源标记)。来源标记：explicit / default_piece / missing。
    """
    parsed = spec(value)
    if parsed is not None:
        return parsed.raw, "explicit"
    if default:
        fallback = spec(default)
        if fallback is not None:
            return fallback.raw, "default_piece"
    return None, "missing"


def text_number(value) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _decimal(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


__all__ = ["COUNT", "WEIGHT", "LENGTH", "AREA", "VOLUME", "PACKAGE", "DIMENSION_LABELS",
           "DEFAULT_COUNT_UNIT", "UnitSpec", "UNIT_ALIASES", "spec", "canonical", "dimension",
           "count_equivalent", "compatible", "conflict_reason", "to_base", "convert",
           "difference", "resolve_missing", "text_number"]
