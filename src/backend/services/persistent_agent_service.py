"""持久企业问答：理解参数 -> 白名单 Tool -> 确定性答复 -> 问答/来源原子保存。

默认企业路径不访问 Demo；可显式注入隔离场景 Tool。不运行 MemorySaver，不包含业务算法。
"""

from __future__ import annotations

import copy
import logging
import os
import re
from collections.abc import Callable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents.fact_query_agent import _explicit_material_code, _rule_intent, _answer_from_result
from config import get_llm, get_llm_configuration_status
from repositories.agent_conversation_repository import AgentConversationRepository
from services.task_parser_service import extract_production_task_fields
from tools.fact_query_registry import get_fact_query_tool
from tools.production_feasibility_query_tool import published_production_capacity_query
from tools.published_purchase_tools import published_purchase_recommendations_query


logger = logging.getLogger(__name__)
QueryKind = Literal["inventory", "inventory_batches", "bom", "inbound", "production_capacity", "purchase_planning", "clarification"]


class EnterpriseQueryIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query_type: QueryKind
    material_code: str | None = None
    revision: str | None = None
    effective_date: str | None = None
    as_of: str | None = None
    start_date: str | None = None
    quantity: int | None = Field(default=None, ge=1)


_REVISION = re.compile(r"(?<![A-Za-z0-9])V\d+(?![A-Za-z0-9])", re.IGNORECASE)
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})")
_EFFECTIVE = re.compile(r"(?:生效(?:日期|日|时间)?|BOM日期)\s*[:：]?\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_START = re.compile(r"(?:开工(?:日期|日|时间)?|计划开工)\s*[:：]?\s*(\d{4}-\d{2}-\d{2})")
_PRODUCTION = re.compile(r"生产|开工|齐套|减(?:少|量)|产量|做\d+|限制产量")
_PURCHASE = re.compile(r"采购建议|采购计划|采购量|采购成本|买多少|补购")
_PROMPT = """只理解企业查询意图和逐字提取参数，禁止查询数据库、回答事实或计算。
query_type 只能为 inventory/inventory_batches/bom/inbound/production_capacity/purchase_planning/clarification。
material_code/revision/effective_date/as_of/start_date 必须来自原文；缺失用 null，不猜型号、版本或日期。
quantity 是原文的生产数量，可把中文整数规范化，不能计算产能/库存/采购数量。
只返回 schema 字段，不返回事实、生产结论或建议。"""


def parse_enterprise_query(message: str, *, allow_llm: bool = True) -> tuple[EnterpriseQueryIntent, str]:
    """规则优先；LLM 只可辅助分类，所有标识和数值均由原文核验。"""
    from agents.business_language import normalize_business_dates
    message = normalize_business_dates(message)
    if re.search(r"(?:-\s*\d+|\d+\.\d+)\s*(?:台|件|个|套|片)", message):
        raise ValueError("生产数量必须是正整数")
    fact = _rule_intent(message)
    if _PURCHASE.search(message):
        kind = "purchase_planning"
    elif _PRODUCTION.search(_START.sub("", message)) and not (fact and fact.query_type == "bom"):
        kind = "production_capacity"
    elif fact:
        kind = fact.query_type
    else:
        kind = "clarification"
    version, time, effective, start = (_REVISION.search(message), _TIMESTAMP.search(message),
                                      _EFFECTIVE.search(message), _START.search(message))
    code_message = re.sub(r"(?:\d+(?:\.\d+)?|[零〇一二三四五六七八九十百千万两]+)\s*(?:台|套|件|个|只|片|PCS)", "", message, flags=re.IGNORECASE)
    fields = dict(query_type=kind, material_code=_explicit_material_code(code_message),
                  revision=version.group() if version else None,
                  as_of=time.group() if time else None,
                  effective_date=effective.group(1) if effective else None,
                  start_date=start.group(1) if start else None,
                  quantity=extract_production_task_fields(message)["quantity"])
    # 日期不能被旧编码正则误当料号。
    if fields["material_code"] in {fields["as_of"], fields["effective_date"], fields["start_date"]}:
        fields["material_code"] = None
    rule = EnterpriseQueryIntent.model_validate(fields)
    if kind != "clarification" or not allow_llm:
        return rule, "rule"
    try:
        if get_llm_configuration_status()["status"] == "ok":
            candidate = get_llm().with_structured_output(EnterpriseQueryIntent, method="json_mode").invoke(
                [("system", _PROMPT), ("human", message)])
            if isinstance(candidate, BaseModel):
                candidate = candidate.model_dump()
            parsed = EnterpriseQueryIntent.model_validate(candidate)
            for field in ("material_code", "revision", "as_of", "effective_date", "start_date"):
                value = getattr(parsed, field)
                if value is not None and value not in message:
                    raise ValueError("参数不在原文")
                # 本地找到的明确标识优先，不允许 LLM 改换查询对象。
                local = getattr(rule, field)
                if value is not None and local is not None and value != local:
                    raise ValueError("参数与原文明确字段冲突")
            if parsed.quantity is not None and parsed.quantity != rule.quantity:
                raise ValueError("数量不是原文生产数量")
            return parsed.model_copy(update={field: getattr(rule, field) or getattr(parsed, field)
                                            for field in fields if field != "query_type"}), "llm"
    except Exception:
        pass
    return rule, "rule_fallback"


def _inherit(intent: EnterpriseQueryIntent, message: str, context: dict) -> EnterpriseQueryIntent:
    production = intent.query_type in {"production_capacity", "purchase_planning"}
    previous = context.get("production" if production else "fact", {})
    if not production and intent.query_type != "clarification" and intent.material_code is None and context.get("active_domain") == "production":
        # 非当前事实会话的隐含对象不能复活另一个旧物料。
        previous = {}
    # 库存 -> 产能：只借用当前明确查询对象及其版本/时间，不借用别的历史生产任务。
    if production and context.get("active_domain") == "fact":
        previous = context.get("fact", {})
    supplies_parameter = any(getattr(intent, field) is not None
                             for field in ("revision", "effective_date", "as_of", "start_date", "quantity"))
    if intent.query_type == "clarification" and (supplies_parameter or re.search(r"这个|它|还能|继续|版本|明细|V\d+|生效|开工", message)):
        domain = context.get("active_domain")
        previous = context.get(domain, {}) if domain in {"fact", "production"} else {}
        if previous.get("query_type"):
            intent = intent.model_copy(update={"query_type": previous["query_type"]})
    same = intent.material_code is None or intent.material_code == previous.get("material_code")
    updates = {"material_code": intent.material_code or previous.get("material_code")}
    if same:
        for field in ("revision", "effective_date", "as_of", "start_date", "quantity"):
            updates[field] = getattr(intent, field)
            if updates[field] is None:
                updates[field] = previous.get(field)
    if re.search(r"最新|当前|现在", message) and intent.as_of is None:
        updates["as_of"] = None
    return EnterpriseQueryIntent.model_validate({**intent.model_dump(), **updates})


def _answer(kind: str, result: dict) -> str:
    if kind in {"inventory", "inventory_batches", "bom", "inbound"}:
        return _answer_from_result(kind, result)
    if result["status"] == "invalid_query":
        return "查询条件不完整或无效，请确认产品、BOM版本、生效日期和数量。"
    if result["status"] == "data_unavailable":
        return "当前事实库没有完整的已发布生产或采购事实，无法给出可靠结论；请查看数据完整性提示。"
    if result["status"] != "ok":
        return "系统查询异常，请稍后重试。"
    data = result["data"]
    if kind == "production_capacity":
        maximum = data.get("theoretical_max_quantity")
        if maximum is None:
            return "当前事实库数据不完整，无法确定最大可生产数量。"
        text = f"依据已发布BOM及权威可用库存，理论最多可生产 {maximum} {data.get('product_unit') or '产品单位'}。"
        if data.get("requested_quantity") is not None:
            sufficient = data.get("inventory_sufficient_for_requested")
            text += " 本次需求的库存齐套结果：" + {True: "足够", False: "不足", None: "未知"}[sufficient] + "。"
            shortages = [f"{row.get('material_code')} 缺口 {row.get('shortage_qty')} {row.get('unit') or ''}"
                         for row in data.get("materials", [])
                         if isinstance(row, dict) and row.get("is_ready") is False]
            if shortages:
                text += " 缺料原因：" + "；".join(shortages[:10]) + "。"
        limiting = [str(row["material_code"]) for row in data.get("limiting_materials", [])
                    if isinstance(row, dict) and row.get("material_code")]
        if limiting:
            text += " 限制产量的物料：" + "、".join(limiting[:10]) + "。"
        return text + "这是库存用量分析，不是开工许可或交付承诺。"
    rows = data.get("recommendations", [])
    known = [row for row in rows if isinstance(row, dict) and row.get("suggested_purchase_qty") is not None]
    text = "采购分析基于已发布参数、库存和在途事实。"
    for row in known[:10]:
        text += f" {row.get('material')}：建议采购 {row['suggested_purchase_qty']} {row.get('unit') or ''}。"
    return text + " 未知的数量、价格或交期不作估算；建议不代表已下单或已到货。"


class PersistentAgentService:
    def __init__(self, repository: AgentConversationRepository, *, tool_resolver: Callable | None = None,
                 response_adapter: Callable | None = None, book_service=None) -> None:
        self.repository = repository
        self.tool_resolver = tool_resolver
        self.response_adapter = response_adapter
        self.orchestrator = None
        self.book_queries = None
        self.book_service = book_service

    def enable_orchestrator(self) -> None:
        """Opt-in API integration; direct legacy service users keep their existing path."""
        from agents.orchestrator import AgentOrchestrator
        from agents.planner import AgentPlanner
        from agents.response_generator import ResponseGenerator
        from agents.tool_registry import ToolRegistry
        scope = getattr(getattr(self.repository, "manifest", None), "scenario_id", "enterprise")
        resolver = self.tool_resolver or (lambda kind: self._tool(EnterpriseQueryIntent(query_type=kind))[0])
        llm_options = dict(llm_factory=lambda: get_llm(), llm_status=lambda: get_llm_configuration_status())
        context_resolver = None
        inventory_operations = None
        formatter = _answer
        product_resolver = None
        if scope == "enterprise":
            from agents.context_resolver import ContextResolver
            from repositories.agent_context_repository import AgentContextRepository
            from repositories.fact_query_repository import FactQueryRepository
            from repositories.fact_repository import FactRepository
            context_resolver = ContextResolver(AgentContextRepository(FactQueryRepository(FactRepository(self.repository.store))))
            from agents.business_formatter import format_business_answer
            from agents.inventory_operations import InventoryOperations
            formatter = format_business_answer
            inventory_operations = InventoryOperations(self.repository.store)
            from tools.published_product_tools import build_published_product_search
            product_tool = build_published_product_search(self.repository.store)
            product_resolver = lambda: product_tool
            # Explicit local enterprise binding only. Temporary/test databases do
            # not silently acquire access to private Excel. Trial never binds it.
            from pathlib import Path
            from repositories.fact_store import DEFAULT_SQLITE_PATH
            database = getattr(self.repository.store, "database_path", None)
            local_enterprise = database and Path(database).resolve() == DEFAULT_SQLITE_PATH.resolve()
            if os.getenv("PMC_TRIAL_MODE") != "1" and (self.book_service is not None or local_enterprise or os.getenv("PMC_BOOK_INVENTORY_FILE")):
                if self.book_service is None:
                    from repositories.book_inventory_repository import BookInventoryRepository
                    from repositories.data_source import RAW_INVENTORY_FILE
                    from services.book_inventory_service import BookInventoryService
                    from services.fact_query_service import FactQueryService
                    path = Path(os.getenv("PMC_BOOK_INVENTORY_FILE") or RAW_INVENTORY_FILE)
                    # Keep an explicitly bound missing/unreadable source bound:
                    # demand calculation must survive stock I/O failure rather
                    # than fall through to another model or a strict snapshot path.
                    self.book_service = BookInventoryService(FactQueryService(context_resolver.repository.facts), BookInventoryRepository(path))
                if self.book_service is not None:
                    from agents.enterprise_book_queries import EnterpriseBookQueries
                    self.book_queries = EnterpriseBookQueries(self.book_service)
                    # No authorization exists to export these enterprise prompts
                    # or observation Evidence, including on subsequent turns.
                    llm_options["llm_status"] = lambda: {"status": "warning", "provider": "private_local_only"}
        self.orchestrator = AgentOrchestrator(ToolRegistry(resolver=resolver, scope=scope or "enterprise",
            analysis_resolver=inventory_operations.resolve if inventory_operations else None,
            product_resolver=product_resolver),
            planner=AgentPlanner(enterprise_mode=scope == "enterprise", **llm_options),
            response_generator=ResponseGenerator(formatter=formatter, **llm_options),
            context_resolver=context_resolver, inventory_operations=inventory_operations)

    def query(self, session_id: str, message: str, *, execution_message: str | None = None) -> dict:
        original_message = message
        message = execution_message or message
        if self.orchestrator is not None:
            session = self.repository.get_session(session_id)
            history_loader = lambda: self.repository.history(session_id,
                after=max(0, session["context_version"] - 50), limit=50)["turns"]
            turn = None
            if self.book_queries is not None:
                try:
                    turn = self.book_queries.run(message, session["context"], history_loader)
                except Exception:
                    logger.exception("企业账面观察查询失败")
                    # Never fall through to a different model/data source after
                    # an observation failure, or display an older product result.
                    from agents.orchestrator import OrchestratedTurn
                    safe_context = copy.deepcopy(session["context"])
                    safe_context["last_query_failure"] = dict(intent="inventory", entities={})
                    turn = OrchestratedTurn(dict(intent="inventory", status="query_failed", answer="企业库存记录暂不可读取，请稍后重试；没有生成替代库存或生产结论。",
                        structured_data=dict(data=None, query={}, completed_steps=[]), provenance=None, warnings=[]), safe_context, [])
            if turn is None:
                turn = self.orchestrator.run(message, session["context"], history_loader=history_loader)
                if turn is not None and self.book_queries is not None:
                    active = turn.context.get(turn.context.get("active_domain"), {})
                    prior_book = turn.context.get("book_analysis", {})
                    if active.get("material_code") and active["material_code"] != prior_book.get("product"):
                        turn.context.pop("book_analysis", None)
            if turn is not None:
                response = {**turn.response, "session_id": session_id, "conversation_id": session["conversation_id"]}
                if self.orchestrator.registry.scope == "enterprise":
                    from agents.business_language import price_requested
                    from agents.business_answer import visible_warnings
                    relevant = price_requested(message)
                    response["warnings"] = visible_warnings(response["warnings"], price_relevant=relevant)
                    response["structured_data"].setdefault("presentation", {}).update(price_relevant=relevant)
                if response.get("structured_data") is not None:
                    from agents.query_presentation import present_query
                    response = present_query(response, message, getattr(self, "presentation_binding", None))
                if self.response_adapter is not None:
                    response = self.response_adapter(response)
                response["turn_id"] = self.repository.append_turn(session, original_message, response, turn.context, turn.references)
                return response
        return self._legacy_query(session_id, message)

    def _legacy_query(self, session_id: str, message: str) -> dict:
        session = self.repository.get_session(session_id)
        context = copy.deepcopy(session["context"])
        references: list[dict] = []
        try:
            intent, parser_source = parse_enterprise_query(message)
            intent = _inherit(intent, message, context)
        except ValueError:
            intent, parser_source = EnterpriseQueryIntent(query_type="clarification"), "rule_fallback"
            response = self._needs_input(intent, ["有效的生产数量"], parser_source)
        else:
            missing = []
            if intent.query_type == "clarification":
                missing.append("查询意图（库存、批次、BOM、在途、生产能力或采购建议）")
            if not intent.material_code:
                missing.append("物料或产品编码")
            if intent.query_type in {"bom", "production_capacity", "purchase_planning"} and not intent.revision:
                missing.append("BOM版本")
            if intent.query_type in {"production_capacity", "purchase_planning"} and not intent.effective_date:
                missing.append("BOM生效日期")
            if intent.query_type == "purchase_planning":
                if intent.quantity is None:
                    missing.append("生产数量")
                if not intent.start_date:
                    missing.append("计划开工日期")
            if missing:
                response = self._needs_input(intent, missing, parser_source)
            else:
                tool, args = self._tool(intent)
                if self.tool_resolver is not None:
                    tool = self.tool_resolver(intent.query_type)
                try:
                    result = tool.invoke(args)
                    if not isinstance(result, Mapping) or not all(
                        key in result for key in ("status", "query", "data", "completeness", "provenance", "warnings")
                    ) or result["status"] not in {"ok", "data_unavailable", "item_not_found", "item_alias_ambiguous", "invalid_query"}:
                        raise ValueError("Tool未返回完整受控结果")
                    if not isinstance(result["query"], dict) or not isinstance(result["completeness"], dict) or not isinstance(result["warnings"], list):
                        raise ValueError("Tool返回结构错误")
                    if result["data"] is not None and not isinstance(result["data"], dict):
                        raise ValueError("Tool返回数据结构错误")
                    if result["status"] == "ok" and result["data"] is None:
                        raise ValueError("Tool返回空事实")
                    result = {key: result[key] for key in ("status", "query", "data", "completeness", "provenance", "warnings")}
                except Exception:
                    logger.exception("持久事实查询失败")
                    result = dict(status="query_failed", query=args, data=None, completeness={}, provenance=None,
                                  warnings=["系统查询异常，请稍后重试；没有返回推断事实"])
                references.append(dict(tool_name=tool.name, query=args, result=result))
                response = dict(intent=intent.query_type, status=result["status"], answer=_answer(intent.query_type, result),
                                structured_data=dict(query_type=intent.query_type, query=result["query"], data=result["data"],
                                                     completeness=result["completeness"], parser_source=parser_source),
                                provenance=result["provenance"], warnings=result["warnings"])
            # 明确编码即使未找到也覆盖旧对象，避免下一轮错误回到旧料号。
            if intent.query_type != "clarification" and intent.material_code:
                domain = "production" if intent.query_type in {"production_capacity", "purchase_planning"} else "fact"
                saved = intent.model_dump()
                if references:
                    query = references[0]["result"].get("query", {})
                    data = references[0]["result"].get("data") or {}
                    saved["material_code"] = (query.get("resolved_material_code") or query.get("resolved_parent_material_code")
                                              or (data.get("product") if domain == "production" else None) or saved["material_code"])
                context[domain] = saved
                context["active_domain"] = domain
        response["session_id"] = session_id
        response["conversation_id"] = session["conversation_id"]
        if self.response_adapter is not None:
            response = self.response_adapter(response)
        turn_id = self.repository.append_turn(session, message, response, context, references)
        response["turn_id"] = turn_id
        return response

    @staticmethod
    def _needs_input(intent: EnterpriseQueryIntent, missing: list[str], parser_source: str) -> dict:
        return dict(intent=intent.query_type, status="needs_input", answer="请补充：" + "、".join(missing) + "。未执行事实计算。",
                    structured_data=dict(query=intent.model_dump(), missing_fields=missing, parser_source=parser_source),
                    provenance=None, warnings=[])

    @staticmethod
    def _tool(intent: EnterpriseQueryIntent):
        if intent.query_type in {"inventory", "inventory_batches", "bom", "inbound"}:
            args = (dict(parent_material_code=intent.material_code, revision=intent.revision)
                    if intent.query_type == "bom" else dict(material_code=intent.material_code, as_of=intent.as_of))
            return get_fact_query_tool(intent.query_type), args
        if intent.query_type == "production_capacity":
            return published_production_capacity_query, dict(product=intent.material_code, bom_version=intent.revision,
                effective_date=intent.effective_date, requested_quantity=intent.quantity, as_of=intent.as_of)
        return published_purchase_recommendations_query, dict(product=intent.material_code, revision=intent.revision,
            effective_date=intent.effective_date, quantity=intent.quantity, start_date=intent.start_date, as_of=intent.as_of)
