"""统一自然语言入口的受控意图分流；不执行任何 PMC 或事实计算。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict

from config import get_llm, get_llm_configuration_status


RoutedIntent = Literal["pmc_analysis", "fact_query", "production_capacity"]


class IntentExtraction(BaseModel):
    """LLM 仅允许返回入口意图；字段由下游已校验解析器提取。"""

    model_config = ConfigDict(extra="forbid")

    intent: RoutedIntent


class RouteDecision(TypedDict):
    intent: RoutedIntent | Literal["clarification"]
    parser_source: Literal["llm", "rule_fallback", "rule_override", "ambiguous", "fact_context"]


_ROUTER_PROMPT = """
你只做意图分类，不回答问题、不计算业务数值、不调用数据源。
pmc_analysis：生产任务、开工判断、生产原因追问、产量调整或方案推演。
fact_query：查询已发布的库存、批次、BOM、在途事实。
production_capacity：询问最多能生产多少、当前库存是否满足指定数量、哪些物料限制产量。
只返回符合 schema 的 intent，不返回事实、数量、采购或生产结论。
"""
_PMC_PATTERN = re.compile(r"生产|开工|减(?:少|量)|调整.*(?:台|件|个)|产量|齐套|缺料|生产方案|哪些物料.*影响")
_FACT_PATTERN = re.compile(
    r"库存|批次|哪批|库位|FIFO|有效期|BOM|物料清单|物料组成|有哪些(?:物料|料)|"
    r"在途|到货|什么时候到|采购(?:订单|在途|到货|状态|情况)|订单|剩余|余量|存量|还有多少|还剩多少",
    re.IGNORECASE,
)
_FACT_FOLLOWUP_PATTERN = re.compile(r"这个|该物料|它|还能|还有|能用|可用|版本|明细|同一|刚才|继续|多少|几[个件]|^\s*V\d+\s*$", re.IGNORECASE)
_EXPLICIT_ITEM_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9][A-Za-z0-9_-]{2,}(?![A-Za-z0-9])")
_RESERVED_ITEM_TOKENS = {"BOM", "FIFO", "PMC", "MRP", "STOCK", "BATCH", "INBOUND"}
_CAPACITY_PATTERN = re.compile(
    r"(?:最多|最大|至多|极限|理论).*?(?:生产|做|产量).*?(?:多少|几)|"
    r"(?:当前|现在)?库存.*?(?:够|能).*?(?:生产|做).*?(?:多少|几)|"
    r"(?:能|可).*?生产.*?(?:多少|几)\s*(?:台|件|套|个)?|"
    r"哪些物料.*?(?:限制|制约).*?(?:产量|生产)|"
    r"(?:\d+\s*(?:台|件|套|个)).*?需要哪些物料|"
    r"(?:当前|现在)?库存够(?:不够|吗|生产)|"
    r"(?:哪些物料).*?(?:是瓶颈|瓶颈)",
    re.IGNORECASE,
)


def is_standalone_material_code(message: str) -> bool:
    """只识别明确的编码形态；是否真实存在仍由 item/alias resolver 精确核验。"""
    token = message.strip()
    if not _EXPLICIT_ITEM_PATTERN.fullmatch(token) or len(token) > 64:
        return False
    if token.upper() in _RESERVED_ITEM_TOKENS or re.fullmatch(r"V\d+", token, re.IGNORECASE):
        return False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
        return False
    if token.isdigit():
        return len(token) >= 8
    return any(char.isdigit() or char in "-_" for char in token) or (token.isupper() and len(token) >= 5)


def _rule_classify(message: str) -> RoutedIntent | Literal["ambiguous"] | None:
    if _CAPACITY_PATTERN.search(message):
        return "production_capacity"
    pmc = bool(_PMC_PATTERN.search(message))
    fact = bool(_FACT_PATTERN.search(message)) or (not pmc and "采购" in message)
    if pmc and fact:
        return "ambiguous"
    if pmc:
        return "pmc_analysis"
    if fact:
        return "fact_query"
    if is_standalone_material_code(message):
        return "fact_query"
    if re.search(r"多少|有无|有没有|几[个件]", message) and any(
        is_standalone_material_code(token)
        for token in _EXPLICIT_ITEM_PATTERN.findall(message)
    ):
        return "fact_query"
    return None


def _invoke_llm_classification(message: str) -> IntentExtraction:
    llm = get_llm().with_structured_output(IntentExtraction, method="json_mode")
    response = llm.invoke([("system", _ROUTER_PROMPT), ("human", message)])
    if isinstance(response, IntentExtraction):
        return response
    if isinstance(response, BaseModel):
        return IntentExtraction.model_validate(response.model_dump())
    if isinstance(response, Mapping):
        return IntentExtraction.model_validate(dict(response))
    raise ValueError("模型未返回受控意图对象")


def route_agent_message(
    message: str,
    *,
    fact_context_active: bool = False,
    pmc_context_active: bool = False,
) -> RouteDecision:
    """分类失败时仅回退规则；双重意图不猜测，要求用户澄清。"""
    rule = _rule_classify(message)
    if rule == "ambiguous":
        return {"intent": "clarification", "parser_source": "ambiguous"}
    if rule is None and fact_context_active and _FACT_FOLLOWUP_PATTERN.search(message) and not re.search(r"\d+\s*台", message):
        return {"intent": "fact_query", "parser_source": "fact_context"}
    if rule is None and pmc_context_active:
        return {"intent": "clarification", "parser_source": "rule_fallback"}

    try:
        llm_available = get_llm_configuration_status()["status"] == "ok"
    except Exception:
        llm_available = False
    if llm_available:
        try:
            llm_intent = _invoke_llm_classification(message).intent
            if rule is not None and llm_intent != rule:
                return {"intent": rule, "parser_source": "rule_override"}
            return {"intent": llm_intent, "parser_source": "llm"}
        except Exception:
            pass

    if rule is not None:
        return {"intent": rule, "parser_source": "rule_fallback"}
    return {"intent": "clarification", "parser_source": "rule_fallback"}


__all__ = ["IntentExtraction", "RouteDecision", "RoutedIntent", "route_agent_message"]
