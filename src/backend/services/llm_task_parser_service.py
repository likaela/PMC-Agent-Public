"""受控的 DeepSeek 生产任务解析器。

本模块只识别生产任务字段，不接触 BOM、库存、MRP、采购或生产决策 Service。
所有 LLM 输出都必须通过本地字段校验；调用失败时使用规则解析回退。
"""

from __future__ import annotations

from collections.abc import Mapping
from pydantic import BaseModel, ValidationError

from api.schemas.llm_task import LlmTaskExtraction, ProductionTaskIntent
from config import get_llm
from services.task_parser_service import (
    extract_production_task_fields,
    validate_production_task_fields,
)


_TASK_PARSER_PROMPT = """
你是制造业 PMC 的任务参数提取器。只从用户消息提取生产任务，不计算也不推测
BOM、库存、缺料、采购、风险或生产结论。intent 固定为 analysis。若未出现 BOM
版本，bom_version 可以为 null；若无法识别字段，置为 null 并在 missing_fields 中说明。
只返回与结构化 schema 一致的内容。
"""


class LlmTaskParserError(ValueError):
    """LLM 输出或连接不符合任务解析契约。"""


def _coerce_extraction(response: object) -> LlmTaskExtraction:
    """兼容 LangChain 返回的 Pydantic 模型或 JSON object。"""
    if isinstance(response, LlmTaskExtraction):
        return response
    if isinstance(response, BaseModel):
        return LlmTaskExtraction.model_validate(response.model_dump())
    if isinstance(response, Mapping):
        return LlmTaskExtraction.model_validate(dict(response))
    raise LlmTaskParserError("LLM 未返回结构化任务对象")


def _invoke_structured_llm(message: str) -> LlmTaskExtraction:
    """通过 config.py 的模型配置调用 DeepSeek 结构化输出。"""
    try:
        llm = get_llm()
        structured_llm = llm.with_structured_output(LlmTaskExtraction, method="json_mode")
        response = structured_llm.invoke(
            [
                ("system", _TASK_PARSER_PROMPT),
                ("human", message),
            ]
        )
        return _coerce_extraction(response)
    except (ValidationError, ValueError, TypeError) as error:
        raise LlmTaskParserError("LLM 结构化任务输出无效") from error
    except Exception as error:
        raise LlmTaskParserError("LLM 任务解析暂不可用") from error


def _build_validated_intent(extraction: LlmTaskExtraction, *, parser_source: str) -> ProductionTaskIntent:
    """对完整 LLM 任务字段执行本地校验；字段不完整时返回澄清契约。"""
    try:
        validated = validate_production_task_fields(
            extraction.product,
            extraction.bom_version,
            extraction.quantity,
        )
    except ValueError:
        normalized_product: str | None = None
        normalized_version: str | None = None
        normalized_quantity: int | None = None
        try:
            normalized_product = validate_production_task_fields(extraction.product, "V01", 1)["product"]
        except ValueError:
            pass
        try:
            normalized_quantity = validate_production_task_fields("KQ-Z112", "V01", extraction.quantity)["quantity"]
        except ValueError:
            pass
        if normalized_product is not None:
            try:
                normalized_version = validate_production_task_fields(
                    normalized_product,
                    extraction.bom_version,
                    1,
                )["bom_version"]
            except ValueError:
                pass
        missing_fields: list[str] = []
        if normalized_product is None:
            missing_fields.append("产品型号")
        if normalized_quantity is None:
            missing_fields.append("生产数量")
        return ProductionTaskIntent(
            intent="analysis",
            product=normalized_product,
            bom_version=normalized_version,
            quantity=normalized_quantity,
            missing_fields=missing_fields,
            parser_source=parser_source,
        )
    return ProductionTaskIntent(
        intent=extraction.intent,
        **validated,
        missing_fields=[],
        parser_source=parser_source,
    )


def _build_rule_fallback_intent(message: str) -> ProductionTaskIntent:
    extracted = extract_production_task_fields(message)
    if not extracted["missing_fields"]:
        try:
            validated = validate_production_task_fields(
                extracted["product"],
                extracted["bom_version"],
                extracted["quantity"],
            )
        except ValueError:
            return ProductionTaskIntent(
                intent="analysis",
                product=extracted["product"],
                bom_version=extracted["bom_version"],
                quantity=None,
                missing_fields=["生产数量"],
                parser_source="rule_fallback",
            )
        return ProductionTaskIntent(intent="analysis", **validated, parser_source="rule_fallback")
    return ProductionTaskIntent(intent="analysis", **extracted, parser_source="rule_fallback")


def parse_production_task_with_llm(message: str) -> ProductionTaskIntent:
    """优先使用 DeepSeek；任何失败均安全回退到本地规则解析。"""
    try:
        return _build_validated_intent(_invoke_structured_llm(message), parser_source="llm")
    except LlmTaskParserError:
        return _build_rule_fallback_intent(message)


__all__ = ["LlmTaskParserError", "parse_production_task_with_llm"]
