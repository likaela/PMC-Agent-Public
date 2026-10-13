"""产能问题的受控参数适配与事实引用答复；LLM 不参与数值计算。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from services.task_parser_service import extract_production_task_fields
from tools.production_feasibility_query_tool import published_production_capacity_query


_VERSION = re.compile(r"(?<![A-Za-z0-9_-])V\d+(?![A-Za-z0-9])", re.IGNORECASE)
_AS_OF = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})")
_UNIT = re.compile(r"(?:\d+|多少|几)\s*(台|套|件|个|只|PCS)", re.IGNORECASE)


def _parameters(message: str, task_context: Mapping[str, object] | None) -> dict[str, object]:
    extracted = extract_production_task_fields(message)
    explicit_product = extracted["product"]
    context_product = task_context.get("product") if task_context else None
    context_version = task_context.get("bom_version") if task_context else None
    product = explicit_product or context_product
    revision = _VERSION.search(message)
    same_task = explicit_product is None or explicit_product == context_product
    version = revision.group() if revision else (context_version if same_task else None)
    quantity = extracted["quantity"]
    if quantity is None and task_context and re.search(r"够吗|够不够|是否足够|能否满足", message):
        quantity = task_context.get("quantity")
    time_match = _AS_OF.search(message)
    unit_match = _UNIT.search(message)
    return {
        "product": product if isinstance(product, str) else None,
        "bom_version": version if isinstance(version, str) else None,
        "requested_quantity": quantity if isinstance(quantity, int) and not isinstance(quantity, bool) else None,
        "requested_unit": unit_match.group(1) if unit_match else None,
        "as_of": time_match.group() if time_match else None,
    }


def _requirement_summary(data: Mapping[str, Any], question: str) -> str:
    if "需要哪些物料" not in question:
        return ""
    requirements = [
        f"{item.get('material_code')} {item.get('required_qty')} {item.get('unit')}"
        for item in data.get("materials", [])
        if isinstance(item, Mapping) and item.get("required_qty") is not None
    ]
    return "按已发布 BOM，所需直接子项：" + "；".join(requirements) + "。" if requirements else ""


def _public_answer(result: Mapping[str, Any], question: str) -> str:
    status = result.get("status")
    if status == "invalid_query":
        return "请提供产品型号和已发布的 BOM 版本；如果询问指定产量，也请给出正整数生产数量。"
    if status == "data_unavailable":
        data = result.get("data")
        known_requirements = _requirement_summary(data, question) if isinstance(data, Mapping) else ""
        return known_requirements + "当前事实库缺少计算可靠产能所需的 BOM、权威可用库存、单位或一致库存时点，无法确定最多可生产多少。请查看数据完整性提示。"
    if status != "ok":
        return "生产能力事实查询暂不可用，目前无法给出可靠数值。"
    data = result.get("data")
    query = result.get("query")
    if not isinstance(data, Mapping) or not isinstance(query, Mapping):
        return "生产能力事实查询结果不完整，目前无法给出可靠数值。"
    maximum = data.get("max_producible_quantity")
    if not isinstance(maximum, int):
        return "生产能力事实查询结果不完整，目前无法给出可靠数值。"
    product = query.get("product")
    version = query.get("bom_version")
    product_unit = data.get("product_unit")
    answer = f"按已发布 {product} / {version} 一级 BOM 与同批次权威可用库存，理论最多可生产 {maximum} {product_unit}。"
    answer += _requirement_summary(data, question)
    requested = data.get("requested_quantity")
    if isinstance(requested, int):
        suffices = data.get("inventory_sufficient_for_requested")
        answer += (
            f" 对 {requested} {product_unit}的库存用量核对结果：{'足够' if suffices else '不足'}。"
        )
        if suffices is False:
            shortages = [
                f"{item.get('material_code')} 缺口 {item.get('shortage_qty')}"
                for item in data.get("materials", [])
                if isinstance(item, Mapping) and item.get("is_short") is True
            ]
            if shortages:
                answer += " 缺口物料：" + "；".join(shortages[:10]) + "。"
    limiters = [
        str(item.get("material_code")) for item in data.get("limiting_materials", [])
        if isinstance(item, Mapping)
    ]
    if limiters:
        answer += " 限制产量的物料：" + "、".join(limiters[:10]) + "。"
    answer += " 在途仅供参考，未计入当前库存产能；这不是开工许可或排程结论。"
    return answer


def run_production_feasibility_agent(
    message: str,
    *,
    task_context: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """明确上下文来自既有 PMC 任务或已查询的 BOM，不根据料号猜版本。"""
    params = _parameters(message, task_context)
    if not params["product"] or not params["bom_version"]:
        result: dict[str, Any] = {
            "status": "invalid_query", "query": params, "data": None,
            "completeness": {}, "provenance": None,
            "warnings": ["产品型号或已发布 BOM 版本未明确，未执行产能计算"],
        }
    else:
        result = published_production_capacity_query.invoke({
            "product": params["product"],
            "bom_version": params["bom_version"],
            "requested_quantity": params["requested_quantity"],
            "requested_unit": params["requested_unit"],
            "as_of": params["as_of"],
        })
    return {**result, "answer": _public_answer(result, message)}


__all__ = ["run_production_feasibility_agent"]
