"""Quantity syntax and the current demand pointer; never computes business facts.

Production supports positive Arabic integers and conventional Chinese integers.
Fractions, decimals, exponents, signs and multiple targets require correction.
An omitted quantity is distinct from an invalid one. Historical demands remain
in conversation evidence; only this pointer authorizes implicit follow-ups.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from services.task_parser_service import _parse_number
from services import unit_system

_NUMBER = r"[+\-−＋－﹣–—]?(?:\d+(?:[./]\d+)*(?:[eE][+\-]?\d+)?|[零〇一二三四五六七八九十百千万两]+)|负[零〇一二三四五六七八九十百千万两\d]+|负数|非法数量|NaN|Infinity"
# 单位写法集中在 services.unit_system 维护，避免各模块各自判断。
_UNITS = "|".join(re.escape(item) for item in unit_system.UNIT_ALIASES)
_TASK = re.compile(r"生产|投产|投料|备料|做|(?:目标|生产)?数量")
_CUE = re.compile(rf"(?:安排生产|计划生产|生产|投产|投料|备料|做|(?:目标|生产)?数量(?:为|是|：|:)?)\s*(?P<number>{_NUMBER})", re.I)
_WITH_UNIT = re.compile(rf"(?<![A-Za-z0-9_.+\-/−＋－﹣–—])(?P<number>{_NUMBER})\s*(?P<unit>{_UNITS})", re.I)
_BARE = re.compile(rf"^\s*(?:那|改成|改为|减少到|增加到|调整到)?\s*(?P<number>{_NUMBER})(?:\s*(?P<unit>{_UNITS}))?\s*(?:呢|可以吗|够吗|够不够|[。？！?!，,])*\s*$", re.I)
_PURCHASE = re.compile(r"采购|补货|补料|买料|下单|领料|扣减|出库|投料")
# 疑问代词不是数量：「最急缺的物料是哪一个」里的「一个」不能被当成 1 片需求。
# 用等长空格替换，保持其余匹配位置不变。
_INTERROGATIVE = re.compile(r"哪(?:一)?(?:个|些|种|项|条|批)")


@dataclass(frozen=True)
class QuantityRequest:
    status: str = "missing"
    quantity: int | None = None
    unit: str | None = None
    reason: str | None = None
    # 单位来源标记：explicit 用户明确给出；missing 未给出；default_piece 按默认计数单位解释。
    unit_source: str = "missing"


def unit_source_of(unit):
    """用户输入单位的来源标记，供上下文与结构化结果追溯。"""
    return "explicit" if unit is not None else "missing"


def _integer(raw):
    if re.fullmatch(r"[0-9]+", raw):
        try:
            return int(raw)
        except ValueError:
            return None
    # Reject digit strings such as 一二 and malformed Chinese unit ordering.
    normalized = raw.replace("两", "二").replace("〇", "零")
    if not re.fullmatch(r"[零一二三四五六七八九十百千万]+", normalized):
        return None
    if re.search(r"[一二三四五六七八九]{2}|零{2}|^零.+|.+零$", normalized):
        return None
    if re.search(r"[百千万][一二三四五六七八九]$", normalized):
        return None  # 一百二 may mean 102 or 120; ask for an unambiguous integer.
    previous = 100000
    for char in normalized:
        if char in "十百千万":
            value = {"十": 10, "百": 100, "千": 1000, "万": 10000}[char]
            if value == 10000:
                previous = 10000
            elif value >= previous:
                return None
            else:
                previous = value
    return _parse_number(normalized)


def parse_quantity(message: str) -> QuantityRequest:
    """Consume whole quantity expressions, never a valid numeric prefix."""
    text = re.sub(r"\d{4}(?:-\d{1,2}-\d{1,2}|年\d{1,2}月\d{1,2}日)(?:T[^，,。\s]+)?", "", message)
    text = _INTERROGATIVE.sub(lambda hit: " " * len(hit.group(0)), text)
    if re.search(r"(?<![A-Za-z0-9])(?:[+\-−＋－﹣–—]\s+|[负正]\s*)[0-9零〇一二三四五六七八九十百千万两]", text):
        return QuantityRequest("invalid", reason="quantity_must_be_positive_integer")
    matches = list(_CUE.finditer(text))
    matches.extend(hit for hit in _WITH_UNIT.finditer(text)
                   if not any(hit.start("number") == other.start("number") for other in matches))
    bare = _BARE.fullmatch(text)
    if bare and not matches:
        matches = [bare]
    if not matches:
        if re.search(r"(?:生产|投产|数量)\s*(?:[A-Za-z]+\s*(?:片|件|个|台|套)|[+\-−＋－﹣–—]|负|非法)", text):
            return QuantityRequest("invalid", reason="quantity_must_be_positive_integer")
        if re.search(r"(?:生产|投产|数量)\s*[¼-¾⅐-⅞]", text):
            return QuantityRequest("invalid", reason="quantity_must_be_positive_integer")
        return QuantityRequest()
    if len(matches) != 1:
        return QuantityRequest("ambiguous", reason="multiple_quantities")
    hit = matches[0]
    raw = hit["number"]
    quantity = _integer(raw)
    tail = text[hit.end("number"):]
    unit_match = re.match(rf"\s*({_UNITS})(?![A-Za-z])", tail, re.I)
    unit = unit_match[1] if unit_match else None
    remainder = tail[unit_match.end():] if unit_match else tail
    if quantity is None or quantity <= 0:
        return QuantityRequest("invalid", unit=unit, reason="quantity_must_be_positive_integer")
    if re.match(r"\s*(?:点|分之|半|余|十|百|千|万|亿)", remainder):
        return QuantityRequest("invalid", unit=unit, reason="invalid_quantity_suffix")
    if re.match(r"\s*(?:[\\:：,，_]\s*[+\-−＋－]?[0-9]|[×÷*^%％])", remainder):
        return QuantityRequest("invalid", unit=unit, reason="invalid_quantity_expression")
    if re.match(r"\s*(?:[./+−＋－﹣–—-]|[0-9]|[A-Za-z])", remainder) and not re.match(r"\s*[A-Za-z]{2,}[A-Za-z0-9]*-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", remainder):
        return QuantityRequest("invalid", unit=unit, reason="invalid_quantity_suffix")
    if unit is None and re.match(r"\s*[\u4e00-\u9fff]", remainder) and not re.match(
            r"\s*(?:可以|可否|能否|能|够|行|是否|时|的|并|且|需要|需求|物料|缺|采购|分析|核对|在|按|请|先|呢)", remainder):
        return QuantityRequest("invalid", reason="unrecognized_quantity_unit")
    if unit is not None:
        parsed = unit_system.spec(unit)
        # 识别不了的写法一律视为无效，不猜测成片或其它单位。
        if parsed is None:
            return QuantityRequest("invalid", unit=unit, reason="unrecognized_quantity_unit",
                                   unit_source="explicit")
        unit = parsed.raw
    return QuantityRequest("valid", quantity, unit, unit_source=unit_source_of(unit))


def is_new_demand(message, request):
    if re.search(r"库存|物料|材料", message) and not re.search(r"生产|投产|投料|备料|做", message):
        return False
    if request.status != "missing":
        return bool(_TASK.search(message) or _PURCHASE.search(message) or _BARE.fullmatch(message))
    return bool(re.search(r"生产|投产|投料|备料|做|目标数量|生产数量", message)
        and not re.search(r"最多|最大|产能|多少|为什么|原因|限制|来源|BOM|物料需求", message, re.I))


def identifier_message(message):
    """Do not let a unitless target become a material identifier."""
    return _WITH_UNIT.sub("", _CUE.sub("", message))


def bare_quantity(message):
    return bool(_BARE.fullmatch(message))


def needs_current_demand(message):
    return bool(_PURCHASE.search(message)) and not re.search(r"邮件|写信|订单|在途|到货|什么时候到", message)


def selected_product(context):
    binding = context.get("product_context") or {}
    if binding.get("product_code"):
        return binding["product_code"]
    active = context.get(context.get("active_domain")) or {}
    return active.get("material_code") or (context.get("production") or {}).get("material_code")


def selected_revision(context, product):
    binding = context.get("product_context") or {}
    if binding.get("product_code") == product and binding.get("revision"):
        return binding["revision"]
    for key in ("production", "fact"):
        task = context.get(key) or {}
        if task.get("material_code") == product and task.get("revision"):
            return task["revision"]
    return None


def expected_unit(service, product):
    """Read product identity only; no stock query or service construction."""
    book = getattr(service, "book_service", None)
    if book is not None:
        item = book.facts.repository.resolve_item(product) if product else None
        return (item or {}).get("unit")
    if product:
        from repositories.fact_query_repository import FactQueryRepository
        from repositories.fact_repository import FactRepository
        item = FactQueryRepository(FactRepository(service.repository.store)).resolve_item(product)
        return (item or {}).get("unit")
    return None


def resolve_unit(requested, actual):
    """解析本次计算实际采用的单位及其来源。

    - 用户明确给出单位 → 原样使用（绝不被默认规则覆盖）；
    - 未给出且业务上下文是计数单位 → 默认“片”，并标记 default_piece；
    - 未给出而成品/物料本身是重量或长度单位 → 沿用该单位，不强制改成片。
    """
    if requested is not None:
        parsed = unit_system.spec(requested)
        return (parsed.raw if parsed else requested), "explicit"
    if actual is None:
        return None, "missing"
    if unit_system.dimension(actual) == unit_system.COUNT:
        return unit_system.DEFAULT_COUNT_UNIT, "default_piece"
    return actual, "inherited_product_unit"


def compatible_unit(requested, actual):
    """单位是否可用于同一口径计算：同维度可换算，计数等价单位可互换。"""
    if actual is None:
        return True  # 成品单位本身未维护时保持既有行为，不在此处臆断单位
    unit, _ = resolve_unit(requested, actual)
    return unit_system.compatible(unit, actual)


def inherit_quantity(context, product=None):
    demand = context.get("current_demand") or {}
    if demand.get("status") != "valid" or not product or demand.get("product") != product:
        return None
    value = demand.get("quantity")
    return value if type(value) is int and value > 0 else None


def demand_context(context, product, request, status, *, unit=None, revision=None):
    saved = copy.deepcopy(context)
    previous = saved.get("current_demand") or {}
    generation = previous.get("generation", 0)
    resolved, unit_source = resolve_unit(request.unit, unit)
    saved["current_demand"] = dict(generation=generation + 1, status=status, product=product,
        revision=revision, quantity=request.quantity, unit=unit or request.unit or resolved,
        unit_source=unit_source)
    saved.pop("inventory_preview", None)
    return saved


def restore_demand(context, history):
    """Upgrade old sessions from their latest saved structured request, not field priority."""
    if "current_demand" in context:
        return context
    product = selected_product(context)
    for turn in reversed(history):
        response = turn.get("response") or {}
        structured = response.get("structured_data") or {}
        data = structured.get("data") or {}
        query = structured.get("query") or {}
        raw = turn.get("user_query") or ""
        if (structured.get("operation") in {"quantity_validation", "quantity_clarification"}
                or "quantity" in (structured.get("missing_parameters") or [])
                or response.get("status") != "ok" and is_new_demand(raw, parse_quantity(raw))):
            return demand_context(context, product, QuantityRequest(), "blocked")
        if response.get("intent") not in {"production_capacity", "purchase_planning"}:
            continue
        if response.get("status") in {"invalid_query", "needs_input", "query_failed"}:
            return demand_context(context, product, QuantityRequest(), "blocked")
        code = data.get("product") or query.get("product") or query.get("material_code")
        quantity = data.get("requested_quantity", query.get("quantity", query.get("requested_quantity")))
        if code == product and type(quantity) is int and quantity > 0 and response.get("status") == "ok":
            return demand_context(context, product, QuantityRequest("valid", quantity, data.get("product_unit")),
                                  "valid", revision=data.get("revision") or query.get("revision"))
    return context


def finish_demand(context, response, request, message):
    saved = copy.deepcopy(context)
    demand = saved.get("current_demand")
    structured = response.get("structured_data") or {}
    data = structured.get("data") or {}
    query = structured.get("query") or {}
    actual = data.get("requested_quantity", data.get("production_quantity", query.get("quantity", query.get("requested_quantity"))))
    code = data.get("product") or query.get("product")
    relative = bool(re.search(r"再多生产|再增加|多生产|生产(?:到)?(?:最大|最多)数量|按最大|生产上限", message))
    if relative and response.get("status") == "ok" and type(actual) is int and actual > 0 and code == selected_product(saved):
        if demand and request.status == "valid":
            demand.update(status="valid", quantity=actual, revision=data.get("revision") or demand.get("revision"))
            return saved
        return demand_context(saved, code, QuantityRequest("valid", actual, data.get("product_unit")), "valid", revision=data.get("revision"))
    if not demand or demand.get("status") != "valid" or not is_new_demand(message, request):
        return saved
    if response.get("status") in {"ok", "confirmation_required"} and actual == demand["quantity"] and code == demand["product"]:
        demand["status"] = "valid"
        # Revision is fixed before preview; enriching it afterwards would change
        # the persisted execution fingerprint of the same request.
        saved["active_domain"] = "production"
        saved["production"] = dict(saved.get("production") or {}, material_code=code,
            revision=demand.get("revision") or (saved.get("production") or {}).get("revision"),
            quantity=demand["quantity"], query_type=response["intent"])
    else:
        demand["status"] = "failed"
    return saved
