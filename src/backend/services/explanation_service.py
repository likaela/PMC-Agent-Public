"""基于确定性 PMC 结果生成自然语言解释。

本模块不会调用 BOM、MRP、库存、采购或生产决策计算；它只将既有报告的字段
提供给 DeepSeek 总结，并在模型不可用时生成等价的确定性模板解释。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel

from api.schemas.explanation import ExplanationLlmOutput, ExplanationResponse
from config import get_llm


ExplanationTopic = Literal["blocked", "ready", "impact", "general"]

_EXPLANATION_PROMPT = """
你是 PMC 结果解释助手。你只能基于提供的“确定性事实”总结结果、解释原因并用已有建议
生成文字。不得计算、修改或补充 BOM、MRP、库存、缺料、采购量、风险、供应商、交期或生产结论。
不要声称报告未给出的事实。只返回符合 schema 的 answer 文本。
"""


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _as_public_materials(materials: Sequence[object]) -> list[dict[str, object]]:
    """保留已有优先级顺序，只传递可解释的结构化字段。"""
    public_items: list[dict[str, object]] = []
    for material in materials:
        item = _as_mapping(material)
        if not item:
            continue
        public_items.append(
            {
                "priority": item.get("priority"),
                "material": item.get("material"),
                "shortage": item.get("shortage"),
                "purchase_status": item.get("purchase_status"),
                "risk_level": item.get("risk_level"),
                "missing_fields": item.get("missing_fields", []),
            }
        )
    return public_items


def _allowed_facts(
    production_report: Mapping[str, object],
    production_decision: Mapping[str, object],
    missing_materials: Sequence[object],
) -> dict[str, object]:
    """建立仅含既有结果的事实快照，不产生任何新计算。"""
    report_decision = _as_mapping(production_report.get("production_decision"))
    decision = production_decision or report_decision
    return {
        "decision_status": decision.get("decision_status"),
        "can_start_production": decision.get("can_start_production"),
        "decision_reasons": decision.get("decision_reasons", []),
        "recommended_actions": decision.get("recommended_actions", []),
        "blocking_materials": _as_public_materials(missing_materials),
        "purchase_parameter_missing_items": production_report.get("purchase_parameter_missing_items", []),
        "data_limitations_and_assumptions": production_report.get("data_limitations_and_assumptions", []),
    }


def _material_lines(materials: Sequence[Mapping[str, object]]) -> str:
    if not materials:
        return "无确定阻塞物料"
    return "；".join(
        f"P{item.get('priority')} {item.get('material')}：缺料 {item.get('shortage')}，采购状态 {item.get('purchase_status')}"
        for item in materials
    )


def _template_answer(topic: ExplanationTopic, facts: Mapping[str, object]) -> str:
    """模型失败时，逐字复用确定性字段生成说明。"""
    materials = facts.get("blocking_materials", [])
    material_items = materials if isinstance(materials, list) else []
    material_text = _material_lines([item for item in material_items if isinstance(item, Mapping)])
    if topic == "ready":
        return "当前结构化生产决策为可开工。报告未列出确定阻塞物料；请仍按报告中的数据限制与假设执行生产准备。"
    if topic == "impact":
        return f"以下物料已按既有生产决策优先级排序，是当前最需要处理的阻塞项：{material_text}。"
    if topic == "blocked":
        missing = facts.get("purchase_parameter_missing_items", [])
        missing_text = ""
        if isinstance(missing, list) and missing:
            names = [str(item.get("material")) for item in missing if isinstance(item, Mapping) and item.get("material")]
            if names:
                missing_text = f"待补采购参数物料：{'、'.join(names)}。"
        return f"当前生产未能开工，原因是既有生产决策识别到阻塞物料：{material_text}。{missing_text}"
    return f"当前生产结论来自既有确定性报告。阻塞物料：{material_text}。"


def _coerce_llm_output(response: object) -> ExplanationLlmOutput:
    if isinstance(response, ExplanationLlmOutput):
        return response
    if isinstance(response, BaseModel):
        return ExplanationLlmOutput.model_validate(response.model_dump())
    if isinstance(response, Mapping):
        return ExplanationLlmOutput.model_validate(dict(response))
    raise ValueError("LLM 未返回结构化解释")


def explain_pmc_result(
    *,
    question: str,
    topic: ExplanationTopic,
    production_report: Mapping[str, object],
    production_decision: Mapping[str, object],
    missing_materials: Sequence[object],
) -> ExplanationResponse:
    """优先请求 DeepSeek 解释；失败时返回同一事实快照的模板解释。"""
    facts = _allowed_facts(production_report, production_decision, missing_materials)
    try:
        llm = get_llm()
        structured_llm = llm.with_structured_output(ExplanationLlmOutput, method="json_mode")
        response = structured_llm.invoke(
            [
                ("system", _EXPLANATION_PROMPT),
                ("human", f"用户问题：{question}\n确定性事实：{json.dumps(facts, ensure_ascii=False)}"),
            ]
        )
        output = _coerce_llm_output(response)
        return ExplanationResponse(topic=topic, answer=output.answer, source="llm")
    except Exception:
        return ExplanationResponse(topic=topic, answer=_template_answer(topic, facts), source="template_fallback")


__all__ = ["ExplanationResponse", "explain_pmc_result"]
