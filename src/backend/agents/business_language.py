"""Agent-only language rules. User constraints are requests, never published facts."""

from __future__ import annotations

import re


_ABBREVIATIONS = r"MOQ|LT|BOM|FIFO|MRP|PMC|PCS|ERP|WMS|APS"
_CONSTRAINT = re.compile(r"(?<![A-Za-z0-9_-])(MOQ|LT)\s*[:：=]?\s*(\d+)(?![\d.])", re.I)
_REFRESH = re.compile(r"重新计算|重新分析|按最新库存|再查一次|重新查|刷新库存")


def normalize_business_dates(message: str) -> str:
    """Normalize explicit calendar spelling, not relative dates or time-of-day."""
    return re.sub(r"(?<!\d)(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日",
        lambda m: f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}", message)


def production_unit(message: str) -> str | None:
    match = re.search(r"(?:\d+|[零〇一二三四五六七八九十百千万两]+)\s*(片|件|台|套|个|只|PCS)", message, re.I)
    return match[1] if match else None


def conversational(message: str) -> bool:
    # Normalize conversation spelling only, never enterprise identifiers.
    phrase = r"你好|您好|嗨|哈喽|早上好|晚上好|hello|hi|ni\s*hao|介绍一下(?:自己|你的能力)?|你是谁|你能做什么|你能帮我做什么|你有什么功能|谢谢你?"
    text = message.strip(" \t\r\n!！?？。，,.、;；")
    return bool(re.fullmatch(rf"(?:{phrase})(?:[，,、\s]*(?:{phrase}))*", text, re.I))


def product_discovery(message: str) -> tuple[bool, str | None]:
    """Explicit catalog requests cannot inherit a prior production object."""
    if re.search(r"这个产品|该产品|所选产品|当前产品", message) and not re.search(r"产品目录|哪些产品", message):
        return False, None  # A selected-object pronoun is not a product category.
    if re.search(r"(?:查询|查看|查一下|列出|提供|展示)(?:一下)?\s*已发布的?\s*BOM(?:的?产品)?", message, re.I):
        return True, None
    if re.search(r"我能生产什么|(?:有哪些|有什么|看看.*哪些|查看.*哪些)(?:可生产的?|已发布的?)?产品|产品(?:目录|列表)|(?:提供|列出|展示|查看)(?:一下)?已发布的?产品|已发布的?\s*BOM.*(?:有哪些|有什么|列表|目录)", message, re.I):
        return True, None
    category = re.search(r"(?:生产|查找|查询)(.+?)产品", message)
    if category:
        return True, category.group(1).strip(" ，。‘’“”\"'")
    return False, None


def confirmed_context_requested(message: str) -> bool:
    """Explicit opt-in to an audited scenario, not a date default for new tasks."""
    return bool(re.search(r"(?:按|按照|沿用|使用|复用).*(?:之前|此前|已确认|已指定|原先|指定|确认).*(?:业务情境|业务场景|分析情境|分析日期)", message))


def refresh_requested(message: str) -> bool:
    # Negated instructions must not trigger refresh.
    message = re.sub(r"(?:不要|不用|无需|不必)\s*(?:重新计算|重新分析|再查一次|重新查)", "", message)
    return bool(_REFRESH.search(message))


def operation(message: str) -> str:
    if re.search(r"外部.*风险|供应链风险|政策.*风险|政策.*断供|外部供应|新闻.*供应", message):
        return "external_risk"
    if re.search(r"邮件|email", message, re.I) and re.search(r"写|草稿|生成|起草", message):
        return "email_draft"
    if re.search(r"FIFO|批次.*(?:优先|先用)|优先.*批次|先用.*库存|库存.*先用", message, re.I):
        return "fifo"
    if re.search(r"临期|快过期|快到期|过期风险", message):
        return "expiry"
    if re.search(r"呆滞|积压", message):
        return "stagnation"
    if "安全库存" in message:
        return "safety"
    if re.search(r"库存.*(?:问题|健康|诊断)|(?:分析|诊断).*库存.*风险", message):
        return "inventory_health"
    if not refresh_requested(message) and re.search(r"为什么|原因|限制|哪些物料.*影响|怎么回事|主要是什么|主要有哪些|从哪里来|来自哪里|来源是什么|依据是什么", message):
        return "explain"
    return "query"


def intent_override(message: str, local_kind: str) -> str:
    action = operation(message)
    if action == "external_risk":
        return "clarification"
    if action == "email_draft":
        # Retain public contract: standalone draft vs an explicit procurement-plan draft.
        return "purchase_planning" if re.search(r"采购建议|采购计划", message) else "clarification"
    if action == "fifo":
        return "inventory_batches"
    if action in {"inventory_health", "stagnation", "safety"}:
        return "inventory"
    if action == "expiry":
        return "inventory" if local_kind != "clarification" else local_kind
    # Arrival/transport questions precede general purchase language.
    if re.search(r"什么时候到|到货|运输|已采购|预计交付|在途|采购订单", message):
        return "inbound"
    if re.search(r"采购|买|下单|补货|缺料采购|MOQ|成本|供应商", message, re.I):
        return "purchase_planning"
    if re.search(r"相关信息|产品(?:信息|概览)|物料(?:组成|有哪些)|有哪些物料|有哪些原材料|原材料清单|完整物料|全部\d*项?物料", message) and local_kind == "clarification":
        return "bom"
    if local_kind == "clarification" and re.fullmatch(
            r"\s*(?:查询|查看|查一下)\s*(?:物料|料号)\s*[A-Za-z0-9][A-Za-z0-9_-]*[。！？?!\s]*", message):
        return "inventory"
    if re.search(r"多少种物料|多少项物料|BOM版本|BOM.*多少|怎么样[？?]?\s*$", message, re.I) and local_kind == "clarification":
        return "bom"
    return local_kind


def price_requested(message: str) -> bool:
    return bool(re.search(r"成本|多少钱|金额|单价|报价|价格|核价", message))


def identifier_message(message: str) -> str:
    """Protect unquoted manufacturing terms and their parameter values from code regex."""
    message = _CONSTRAINT.sub("", message)
    return re.sub(rf"(?<![A-Za-z0-9_\-\"“‘])(?:{_ABBREVIATIONS})(?![A-Za-z0-9_\-\"”’])", "", message, flags=re.I)


def constraints(message: str) -> list[dict]:
    return [{"type": match.group(1).upper(), "value": int(match.group(2))}
            for match in _CONSTRAINT.finditer(message)]


def inventory_request(message: str) -> dict:
    qty = re.search(r"(?:领用|使用|用|需求|扣减|分配)\s*(\d+(?:\.\d+)?)\s*(?:件|个|kg|g|PCS)", message, re.I)
    expiry = re.search(r"临期(?:阈值)?\s*[:：]?\s*(\d+)\s*天", message)
    stagnant = re.search(r"呆滞(?:阈值)?\s*[:：]?\s*(\d+)\s*天", message)
    return dict(required_qty=qty.group(1) if qty else None,
                expiry_warning_days=int(expiry.group(1)) if expiry else None,
                stagnant_days=int(stagnant.group(1)) if stagnant else None)
