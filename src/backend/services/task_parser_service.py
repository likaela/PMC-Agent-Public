"""自然语言生产任务解析。

本模块只提取任务参数，不读取库存、不计算 MRP，也不产生生产或采购结论。
当前 MVP 使用可离线验证的规则解析；未来若接入 LLM，只能让 LLM 输出相同的
``product / bom_version / quantity`` 契约，并必须经过本模块的字段校验。
"""

from __future__ import annotations

import re
from typing import TypedDict


DEFAULT_BOM_VERSION = "V01"


class ParsedProductionTask(TypedDict):
    """交给 LangGraph 工作流的最小生产任务契约。"""

    product: str
    bom_version: str
    quantity: int


class ExtractedProductionTask(TypedDict):
    """允许缺少字段的中间解析结果，仅用于主动澄清。"""

    product: str | None
    bom_version: str | None
    quantity: int | None
    missing_fields: list[str]


class TaskParseError(ValueError):
    """自然语言中缺少或包含非法任务参数。"""


_PRODUCT_PATTERN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,10}-[A-Za-z0-9]+)")
_PRODUCT_VALUE_PATTERN = re.compile(r"^[A-Za-z]{1,10}-[A-Za-z0-9]+$")
_BOM_VERSION_PATTERN = re.compile(r"(?<![A-Za-z0-9])[Vv]\s*(\d{1,3})(?![A-Za-z0-9])")
_BOM_VERSION_VALUE_PATTERN = re.compile(r"^[Vv]\s*(\d{1,3})$")
_NUMBER_TOKEN = r"(?:\d+|[零〇一二三四五六七八九十百千万两]+)"
_PRODUCTION_QUANTITY_PATTERN = re.compile(
    rf"(?:生产|安排生产|计划生产|做)\s*({_NUMBER_TOKEN})\s*(?:台|套|件|个|只|片|PCS|pcs)?"
)
_QUANTITY_LABEL_PATTERN = re.compile(rf"(?:生产数量|数量)\s*(?:为|是|：|:)?\s*({_NUMBER_TOKEN})")
_UNIT_QUANTITY_PATTERN = re.compile(rf"({_NUMBER_TOKEN})\s*(?:台|套|件|个|只|片|PCS|pcs)")
_CHINESE_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def _parse_number(token: str) -> int | None:
    """解析阿拉伯数字或常见中文整数；不承担任何生产计算。"""
    if token.isdigit():
        return int(token)
    total = 0
    section = 0
    digit: int | None = None
    for char in token:
        if char in _CHINESE_DIGITS:
            digit = _CHINESE_DIGITS[char]
            continue
        unit = _CHINESE_UNITS.get(char)
        if unit is None:
            return None
        if unit == 10000:
            section = (section + (digit or 0)) * unit
            total += section
            section = 0
        else:
            section += (digit if digit is not None else 1) * unit
        digit = None
    return total + section + (digit or 0)


def _parse_quantity(message: str) -> int | None:
    match = (
        _PRODUCTION_QUANTITY_PATTERN.search(message)
        or _QUANTITY_LABEL_PATTERN.search(message)
        or _UNIT_QUANTITY_PATTERN.search(message)
    )
    if not match:
        return None
    return _parse_number(match.group(1))


def _parse_bom_version(message: str) -> str:
    match = _BOM_VERSION_PATTERN.search(message)
    if not match:
        return DEFAULT_BOM_VERSION
    return f"V{int(match.group(1)):02d}"


def validate_production_task_fields(
    product: object,
    bom_version: object,
    quantity: object,
) -> ParsedProductionTask:
    """校验任意解析器产生的任务字段，避免 LLM 直接进入业务工作流。"""
    missing_fields: list[str] = []
    normalized_product: str | None = None
    normalized_version: str | None = None

    if isinstance(product, str) and product.strip():
        candidate = product.strip().upper()
        if _PRODUCT_VALUE_PATTERN.fullmatch(candidate):
            normalized_product = candidate
    if normalized_product is None:
        missing_fields.append("产品型号")

    if bom_version is None:
        normalized_version = DEFAULT_BOM_VERSION
    elif isinstance(bom_version, str) and bom_version.strip():
        match = _BOM_VERSION_VALUE_PATTERN.fullmatch(bom_version.strip())
        if match:
            normalized_version = f"V{int(match.group(1)):02d}"
    if normalized_version is None:
        missing_fields.append("BOM版本")

    if not isinstance(quantity, int) or isinstance(quantity, bool):
        missing_fields.append("生产数量")
    elif quantity <= 0:
        raise TaskParseError("生产数量必须为正整数")

    if missing_fields:
        raise TaskParseError(f"无法解析生产任务，缺少或非法：{'、'.join(missing_fields)}")

    return {
        "product": normalized_product,
        "bom_version": normalized_version,
        "quantity": quantity,
    }


def extract_production_task_fields(message: str) -> ExtractedProductionTask:
    """从消息取得可确认字段，用于澄清与 LLM 失败时的本地回退。"""
    if not isinstance(message, str) or not message.strip():
        return {"product": None, "bom_version": None, "quantity": None, "missing_fields": ["产品型号", "生产数量"]}

    product_match = _PRODUCT_PATTERN.search(message)
    product = product_match.group(1).upper() if product_match else None
    quantity = _parse_quantity(message)
    missing_fields: list[str] = []
    if product is None:
        missing_fields.append("产品型号")
    if quantity is None:
        missing_fields.append("生产数量")
    return {
        "product": product,
        "bom_version": _parse_bom_version(message) if product is not None else None,
        "quantity": quantity,
        "missing_fields": missing_fields,
    }


def parse_production_task(message: str) -> ParsedProductionTask:
    """从中文生产请求中抽取任务参数。

    ``bom_version`` 没有在消息中出现时，当前展示 MVP 明确使用 ``V01`` 默认版本；
    产品型号和生产数量则必须由用户提供，避免猜测业务数据。
    """

    if not isinstance(message, str) or not message.strip():
        raise TaskParseError("请输入生产任务，例如：我要生产100台KQ-Z112")

    extracted = extract_production_task_fields(message)
    return validate_production_task_fields(
        extracted["product"],
        extracted["bom_version"],
        extracted["quantity"],
    )
