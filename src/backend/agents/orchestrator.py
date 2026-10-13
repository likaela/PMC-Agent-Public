"""Minimal bounded orchestration of existing tools, evidence and public response."""

from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass

from agents.evidence import EvidenceCollector
from agents.context_resolver import ContextResolver
from agents.planner import AgentPlan, AgentPlanner
from agents.response_generator import ResponseGenerator
from agents.tool_registry import PRIMARY_TOOLS, TOOL_KINDS, ToolRegistry
from agents import business_language as language
from agents.business_answer import business_decision, source_overview


logger = logging.getLogger(__name__)
_LABELS = {"intent": "查询意图", "material_code": "物料或产品编码", "revision": "BOM版本",
           "effective_date": "BOM生效日期", "quantity": "生产数量", "start_date": "计划开工日期",
           "as_of": "带时区的库存分析时点", "required_qty": "FIFO明确领用数量",
           "valid_parameters": "有效的查询参数（数量须为正整数，日期须明确有效）"}


@dataclass
class OrchestratedTurn:
    response: dict
    context: dict
    references: list[dict]


class AgentOrchestrator:
    def __init__(self, registry: ToolRegistry, *, planner: AgentPlanner | None = None,
                 response_generator: ResponseGenerator | None = None, context_resolver: ContextResolver | None = None,
                 inventory_operations=None):
        self.registry = registry
        self.planner = planner or AgentPlanner()
        self.response_generator = response_generator or ResponseGenerator()
        if context_resolver is not None and registry.scope != "enterprise":
            raise ValueError("Enterprise context resolution cannot be bound to Demo")
        self.context_resolver = context_resolver
        self.inventory_operations = inventory_operations

    def run(self, message: str, context: dict, *, history_loader=None) -> OrchestratedTurn | None:
        context = copy.deepcopy(context)
        original_context = copy.deepcopy(context)
        scope = context.get("scenario_id") or "enterprise"
        if scope != self.registry.scope:
            raise ValueError("Conversation and tool data scope differ")
        if self.context_resolver is not None:
            context = self.context_resolver.prepare_context(context)
        failure = original_context.get('last_query_failure') if self.registry.scope == 'enterprise' else None
        if failure and language.refresh_requested(message):
            # An implicit retry targets the failed request, not an older successful product.
            from services.persistent_agent_service import parse_enterprise_query
            try:
                explicit, _ = parse_enterprise_query(language.identifier_message(message), allow_llm=False)
            except ValueError:
                explicit = None  # Existing Planner validation handles malformed retries.
            if explicit is not None and explicit.material_code is None:
                domain = 'production' if failure['intent'] in {'production_capacity', 'purchase_planning'} else 'fact'
                context[domain] = {'query_type': failure['intent'], **failure['entities']}
                context['active_domain'] = domain
        plan, parser_source = self.planner.plan(message, context)
        # Revalidate at the execution boundary, including injected/custom planners.
        plan = AgentPlan.model_validate(plan.model_dump())
        if self.registry.scope != "enterprise" and "valid_parameters" in plan.missing_parameters and re.search(r"生产|产量", message):
            return self._information(plan, original_context, "needs_input",
                "生产数量必须是大于0的整数，请确认输入数量及日期，例如1台或50台。未执行事实计算。")
        if self.registry.scope != "enterprise" and plan.operation == "explain" and not language.refresh_requested(message):
            try:
                reused = self._reuse(plan, original_context, history_loader, message)
            except Exception:
                logger.exception("读取场景会话证据失败")
                return self._information(plan, original_context, "query_failed", "当前会话证据暂不可用，请稍后重试。")
            if reused is not None:
                return reused
        if self.registry.scope == "enterprise":
            if plan.operation == "conversation":
                turn = self._information(plan, original_context, "ok",
                    "您好，我是制造企业的PMC助手。可以帮您查看已发布产品BOM、库存与批次，分析生产齐套和采购需求，解释库存风险，并依据已有采购证据起草邮件。"
                    "您可以说“有哪些产品”，或告诉我产品编码和生产数量。没有权威数据时我会说明限制，不猜测库存或开工结论。")
                turn.response["warnings"] = []
                return turn
            if plan.operation == "external_risk":
                return self._information(plan, original_context, "data_unavailable",
                    "当前支持内部库存风险、生产缺料风险、临期与呆滞分析。由于没有权威外部数据源，外部供应链风险和政策影响暂无法判断。")
            if plan.operation in {"explain", "email_draft"} and not language.refresh_requested(message):
                if failure and (not plan.entities.material_code or plan.entities.material_code not in message
                                or plan.entities.material_code == failure['entities'].get('material_code')):
                    code = failure['entities'].get('material_code') or '该对象'
                    return self._information(plan, original_context, 'query_failed',
                        f'上一条对 {code} 的查询未成功，当前没有该次分析结果；不会用其他产品的历史证据替代。请重试该查询。')
                try:
                    reused = self._reuse(plan, original_context, history_loader, message)
                except Exception:
                    logger.exception("读取会话证据失败")
                    return self._information(plan, original_context, "query_failed",
                        "当前会话证据暂不可用，请稍后重试；未重新计算或生成推断事实。")
                if reused is not None:
                    if plan.entities.material_code and plan.entities.material_code in message:
                        reused.context.pop('last_query_failure', None)
                    return reused
                if plan.operation == "email_draft":
                    return self._information(plan, original_context, "needs_input", "当前没有匹配当前任务的采购证据，请先完成采购分析，再生成邮件草稿。")
                # No demand => no shortage cause. Do not revive unrelated or failed evidence.
                if plan.intent in {"inventory", "inventory_batches", "clarification"}:
                    return self._information(plan, original_context, "needs_input",
                        "当前只有库存记录，缺少对应需求及缺料原因证据，无法判断缺料风险；请明确生产任务或需求数量。")
            if plan.operation in {"inventory_health", "expiry", "stagnation", "safety", "fifo"}:
                if plan.intent == "clarification":
                    return self._information(plan, original_context, "needs_input",
                        "请明确要查询哪个物料的临期风险，以及相应有效期或已确认临期阈值；不推测全库范围。")
                if self.inventory_operations is not None:
                    try:
                        plan = self.inventory_operations.prepare(plan)
                    except Exception:
                        logger.exception("库存分析上下文读取失败")
                        return self._information(plan, original_context, "query_failed",
                            "库存分析服务暂不可用，请稍后重试；未生成风险或批次推断。")
        if plan.intent == "inbound":
            return None  # Explicit compatibility delegation, not a model-selected sixth tool.
        if plan.operation == "product_search":
            # Catalog browsing does not invalidate the last task's resolved evidence context.
            context = original_context
        if (self.context_resolver is not None and plan.intent == 'production_capacity' and plan.operation == 'query'
                and plan.entities.quantity is None and re.search(r'生产|开工|安排|做', message)
                and not re.search(r'多少|最多|最大|产能|生产能力', message)):
            guidance = self._quantity_guidance(plan, context, message)
            if guidance is not None:
                return guidance
        resolution, context_references = None, []
        if self.context_resolver is not None:
            resolution = self.context_resolver.resolve(plan, message, context)
            plan = resolution.plan
            reference = resolution.reference()
            context_references = [reference] if reference is not None else []
            if (plan.intent == "purchase_planning" and language.price_requested(message)
                    and (resolution.status != "ok" or AgentPlanner._missing(plan))):
                # Cost refusal is grounded in published completeness, not a new
                # calculation or a fabricated quantity/start date. Complete
                # procurement requests must reach the existing purchase tool:
                # authoritative purchase prices may be independent of BOM prices.
                try:
                    structure = self.context_resolver.repository.structure_evidence(
                        plan.entities.material_code, plan.entities.revision)
                except Exception:
                    logger.exception("读取成本完整性证据失败")
                    structure = None
                if structure and structure["completeness"].get("price") is False:
                    collector = EvidenceCollector()
                    record = collector.collect("published_bom_context", plan.entities.model_dump(),
                        dict(status="data_unavailable", query=plan.entities.model_dump(),
                            data={"bom_structure": structure}, completeness={"cost": False},
                            provenance=structure["provenance"], warnings=["price_missing：目前缺少可靠的价格数据，暂时无法计算完整成本"]))
                    answer, source = self.response_generator.generate([record], ["purchase_planning"])
                    response = dict(intent=plan.intent, status="data_unavailable", answer=answer,
                        structured_data=dict(query_type=plan.intent, query=plan.entities.model_dump(), data=None,
                            completeness={"cost": False}, response_source=source, completed_steps=[]),
                        provenance=structure["provenance"], warnings=list(record.warnings))
                    return OrchestratedTurn(response, self._context(context, plan, None), context_references + [record.reference()])
            if resolution.status != "ok":
                guidance = ("" if resolution.candidates else
                    " 您可以先查看已发布产品目录；若编码无误，请确认并发布对应BOM及有效期，或补充明确的业务日期后继续。")
                response = dict(intent=plan.intent, status=resolution.status, answer=resolution.message + guidance,
                    structured_data=dict(query_type=plan.intent, query=plan.entities.model_dump(), data=None,
                        completeness={"bom_context": False}, parser_source=parser_source,
                        context_resolution={"status": resolution.status, "candidates": resolution.candidates,
                                            "resolutions": resolution.resolutions}),
                    provenance=resolution.provenance or None, warnings=[resolution.message])
                # Explain a failed approval using published structural evidence, without
                # selecting a valid production date/version or executing any calculation.
                if plan.intent == "production_capacity" and not resolution.candidates:
                    try:
                        structure = self.context_resolver.repository.structure_evidence(
                            plan.entities.material_code, plan.entities.revision)
                    except Exception:
                        logger.exception("读取业务回答的已发布结构证据失败")
                        structure = None
                    if structure is not None:
                        warnings = [resolution.message]
                        complete = structure["completeness"]
                        if complete.get("unit") is False:
                            warnings.append("unit_missing")
                        if complete.get("parent_unit") is False:
                            warnings.append("parent_unit_missing")
                        if "当前没有明确的已发布业务时点" in resolution.message or "当前没有已发布库存快照" in resolution.message:
                            warnings.append("尚缺少权威可用库存数据及已确认库存快照时间")
                        collector = EvidenceCollector()
                        record = collector.collect("published_bom_context", plan.entities.model_dump(),
                            dict(status="data_unavailable", query=plan.entities.model_dump(),
                                data={"bom_structure": structure, "business_context": resolution.provenance}, completeness={"production_analysis": False},
                                provenance=structure["provenance"], warnings=warnings))
                        response["answer"], source = self.response_generator.generate([record], ["production_capacity"])
                        response["structured_data"].update(response_source=source,
                            business_decision=business_decision("production_capacity", record.output),
                            published_bom_summary={key: structure["data"][key] for key in
                                ("parent_item", "revision", "line_count", "effective_from")})
                        response["provenance"] = structure["provenance"]
                        response["warnings"] = warnings
                        context_references.append(record.reference())
                saved = self._context(context, plan, None)
                saved = self.context_resolver.remember(saved, plan, resolution)
                return OrchestratedTurn(response, saved, context_references)
        plan.missing_parameters = AgentPlanner._missing(plan) if plan.intent != "clarification" else (plan.missing_parameters or ["intent"])
        # A quantity-free capacity question is valid; a requested production task needs quantity.
        if (self.registry.scope == "enterprise" and plan.intent == "production_capacity" and plan.operation == "query"
                and plan.entities.quantity is None and re.search(r"生产|开工|安排|做", message)
                and not re.search(r"多少|最多|最大|产能|生产能力", message)):
            plan.missing_parameters.append("quantity")
        if plan.missing_parameters:
            labels = [_LABELS.get(key, "必要业务参数") for key in plan.missing_parameters]
            understood = (f"已识别产品或物料 {plan.entities.material_code}。" if plan.entities.material_code else "")
            if plan.entities.quantity is not None:
                understood += f"已识别生产数量 {plan.entities.quantity}。"
            next_step = (" 您可以说“有哪些产品”先查看目录，再提供产品编码和生产数量。" if "material_code" in plan.missing_parameters else
                " 您可以补充“生产50台”，或询问“最多能生产多少”；我会依据已发布事实继续分析。" if "quantity" in plan.missing_parameters else
                " 请补充这些业务条件，我会继续核验已发布事实；未确认的信息不会自行填写。")
            response = dict(intent=plan.intent, status="needs_input", answer=understood + "请补充：" + "、".join(labels) + "。未执行事实计算。" + next_step,
                            structured_data=dict(query={"query_type": plan.intent, **plan.entities.model_dump()},
                                                 missing_fields=labels, parser_source=parser_source, completed_steps=[]),
                            provenance=None, warnings=[])
            saved = self._context(context, plan, None)
            if resolution is not None:
                response["structured_data"]["context_evidence"] = resolution.resolutions
                response["provenance"] = resolution.provenance or None
                saved = self.context_resolver.remember(saved, plan, resolution)
            return OrchestratedTurn(response, saved, context_references)
        collector = EvidenceCollector()
        records, kinds, steps, aliases = [], [], [], []
        for name in plan.required_tools:
            # Validate complete arguments before any execution. Planner cannot issue raw SQL/kwargs.
            args = self.registry.arguments(name, {**plan.entities.model_dump(), **plan.inventory_request.model_dump(), "search_term": plan.search_term})
            tool_name = name
            try:
                tool = self.registry.resolve(name)
                tool_name = tool.name
                record = collector.collect(tool_name, args, tool.invoke(args))
            except Exception:
                logger.exception("受控 Agent Tool 执行失败")
                record = collector.collect(tool_name, args, dict(status="query_failed", query=args, data=None,
                    completeness={}, provenance=None, warnings=["系统查询异常，请稍后重试；未生成推断事实"]))
            records.append(record)
            kinds.append("inventory_risk" if name == "inventory_risk_analysis" else
                         name if name in {"inventory_risk", "inventory_fifo"} else TOOL_KINDS[name])
            aliases.append(name)
            steps.append(dict(operation=name, status=record.output["status"]))
            if record.output["status"] != "ok":
                break  # Fail closed; don't continue a plan on missing/failed facts.
        primary = plan.required_tools[0] if plan.operation in {"inventory_health", "expiry", "stagnation", "safety", "fifo", "product_search"} else PRIMARY_TOOLS[plan.intent]
        primary_record = (records[aliases.index(primary)] if primary in aliases else records[-1])
        result = primary_record.output
        if primary not in aliases:
            # A prerequisite failed: never label its BOM data as a production/purchase result.
            result = {**result, "data": None, "query": self.registry.arguments(primary, plan.entities.model_dump())}
        answer, response_source = self.response_generator.generate(records, kinds)
        provenance = result["provenance"] if len(records) == 1 else [r.provenance for r in records if r.provenance is not None]
        warnings = list(dict.fromkeys(warning for r in records for warning in r.warnings))
        status = next((r.output["status"] for r in records if r.output["status"] != "ok"), result["status"])
        response = dict(intent=plan.intent, status=status, answer=answer,
                        structured_data=dict(query_type=plan.intent, query=result["query"], data=result["data"],
                            completeness=result["completeness"], parser_source=parser_source,
                            response_source=response_source, completed_steps=steps),
                        provenance=provenance, warnings=warnings)
        if self.registry.scope == "enterprise":
            response["structured_data"]["business_decision"] = business_decision(plan.intent, result)
        if plan.constraints:
            response["structured_data"]["requested_constraints"] = [entry.model_dump() for entry in plan.constraints]
            response["warnings"].append("用户MOQ/LT仅作为待确认请求记录，计算仍采用已发布参数，不覆盖企业事实。")
        saved = original_context if status == "query_failed" else self._context(context, plan, result)
        if self.registry.scope == 'enterprise' and status == 'query_failed':
            saved['last_query_failure'] = {'intent': plan.intent, 'entities': plan.entities.model_dump()}
        if resolution is not None:
            response["structured_data"]["context_evidence"] = resolution.resolutions
            if resolution.resolutions:
                if isinstance(response["provenance"], dict):
                    response["provenance"] = {**response["provenance"], "context_resolution": resolution.provenance}
                else:
                    response["provenance"] = [*(response["provenance"] or []), resolution.provenance]
                prefix = (f"本次查询BOM版本 {plan.entities.revision} 的结构，不代表当前生产有效性。 "
                          if set(plan.required_tools) == {"bom_query"} and plan.entities.effective_date is None
                          else f"本次采用BOM版本 {plan.entities.revision}，分析生效日期 {plan.entities.effective_date}。 ")
                if plan.intent != "bom":
                    response["answer"] = prefix + response["answer"]
            if status != "query_failed":
                saved = self.context_resolver.remember(saved, plan, resolution)
        return OrchestratedTurn(response, saved, context_references + [r.reference() for r in records])

    def _quantity_guidance(self, plan, context, message):
        """Ask for quantity first, using published metadata without production calculation."""
        try:
            structure = self.context_resolver.repository.structure_evidence(plan.entities.material_code, plan.entities.revision)
            if structure is None:
                return None
            data = structure['data']
            code = data['parent_item']['material_code']
            fields = self.context_resolver.repository.published_business_fields(code, data['revision'])
            resolution = self.context_resolver.resolve(plan, message, context)
        except Exception:
            logger.exception('读取生产任务数量引导的结构证据失败')
            return None
        answer = (f"已识别生产任务：{code}。请先补充生产数量。"
            f"已找到 {data['revision']} BOM，共 {data['line_count']} 项；")
        if data.get('effective_from'):
            answer += f"BOM生效日期为 {data['effective_from']}。"
        if data['parent_item'].get('unit'):
            answer += f"成品单位已确认：{data['parent_item']['unit']}。"
        if fields.get('business_publication_date'):
            answer += f"企业业务发布日期为 {fields['business_publication_date']}。"
        if fields.get('business_snapshot_date'):
            answer += (f"已确认库存业务日期为 {fields['business_snapshot_date']}（{fields.get('business_timezone') or '时区未确认'}），"
                "仅有日期，尚未确认具体时刻，未作为正式库存快照。")
        plan = resolution.plan
        if plan.entities.effective_date:
            answer += f"本次生产分析日期为 {plan.entities.effective_date}。"
        else:
            answer += "请同时明确本次生产分析日期，或明确选择之前已确认的业务情境；不会把历史分析日期用于所有新计划。"
        answer += "请按“生产50台”这样的句式填写目标数量（单位请替换为本产品已确认单位）。尚未执行生产计算，权威可用库存仍需核验。"
        record = EvidenceCollector().collect('published_bom_context', plan.entities.model_dump(),
            dict(status='data_unavailable', query=plan.entities.model_dump(),
                data={'bom_structure': structure, 'business_context': resolution.provenance},
                completeness={'production_analysis': False}, provenance=structure['provenance'],
                warnings=['尚未提供生产数量，未执行生产计算']))
        response = dict(intent=plan.intent, status='needs_input', answer=answer,
            structured_data=dict(query_type=plan.intent, query=plan.entities.model_dump(), data=None,
                missing_fields=['生产数量'], completed_steps=[], response_source='deterministic',
                context_evidence=resolution.resolutions,
                business_decision=business_decision(plan.intent, record.output),
                published_bom_summary={key: data[key] for key in ('parent_item', 'revision', 'line_count', 'effective_from')}),
            provenance=structure['provenance'], warnings=[])
        saved = self.context_resolver.remember(self._context(context, plan, None), plan, resolution)
        reference = resolution.reference()
        return OrchestratedTurn(response, saved, ([reference] if reference else []) + [record.reference()])

    @staticmethod
    def _information(plan, context, status, answer):
        return OrchestratedTurn(dict(intent=plan.intent, status=status, answer=answer,
            structured_data=dict(query=plan.entities.model_dump(), data=None, operation=plan.operation,
                                 completed_steps=[]), provenance=None, warnings=[answer]), context, [])

    def _reuse(self, plan, context, history_loader, message=""):
        if history_loader is None:
            return None
        domain = context.get("active_domain")
        previous = context.get(domain, {}) if domain in {"production", "fact"} else {}
        email = plan.operation == "email_draft"
        kind = "purchase_planning" if email else plan.intent
        if email and domain != "production":
            return None
        if not email and kind != previous.get("query_type"):
            return None
        code = plan.entities.material_code or previous.get("material_code")
        if not code or (plan.entities.material_code and plan.entities.material_code != previous.get("material_code")):
            return None
        for turn in reversed(history_loader()):
            response = turn["response"]
            if response["intent"] != kind and not (email and response["intent"] == "clarification"):
                continue
            candidates = [ref for ref in turn["fact_references"] if ref["tool_name"] != "published_bom_context"
                          or (ref["result"].get("data") or {}).get("bom_structure")]
            for ref in candidates:
                permitted = {"production_capacity": {"published_production_capacity_query"},
                    "purchase_planning": {"published_purchase_recommendations_query"},
                    "inventory": {"published_inventory_query", "published_inventory_risk_analysis"},
                    "inventory_batches": {"published_inventory_batch_query", "published_inventory_fifo_plan"},
                    "bom": {"published_bom_query"}, "inbound": {"published_inbound_supply_query"}}
                if kind == "production_capacity":
                    permitted[kind].add("published_bom_context")
                if self.registry.scope != "enterprise" and kind in PRIMARY_TOOLS:
                    permitted.setdefault(kind, set()).add(self.registry.resolve(PRIMARY_TOOLS[kind]).name)
                if ref["tool_name"] not in permitted.get(kind, set()):
                    continue
                if self.context_resolver is not None and not self.context_resolver.repository.evidence_is_current(ref):
                    continue
                output = ref["result"]
                if self.registry.scope != "enterprise" and (output.get("provenance") or {}).get("scenario_id") != self.registry.scope:
                    continue
                query, data = output["query"], output.get("data") or {}
                actual = query.get("resolved_material_code") or query.get("resolved_parent_material_code") or data.get("product") or query.get("material_code") or query.get("product")
                if actual != code:
                    continue
                if kind == "purchase_planning" and not data.get("recommendations"):
                    continue
                # Same object is not enough: quantity/version/time also define the task.
                keys = {"revision": ("revision", "bom_version"), "quantity": ("quantity", "requested_quantity"),
                        "effective_date": ("effective_date",), "as_of": ("as_of",), "start_date": ("start_date",)}
                if any(getattr(plan.entities, field) is not None and
                       next((query[k] for k in aliases if query.get(k) is not None), None) != getattr(plan.entities, field)
                       for field, aliases in keys.items()
                       if (field != "quantity" or kind in {"production_capacity", "purchase_planning"})
                       and (field != "start_date" or kind == "purchase_planning")):
                    continue
                if kind == "inventory" and "shortage_qty" not in data and not data.get("inventory_lots"):
                    # Stock totals alone cannot explain a demand shortage.
                    return None
                collector = EvidenceCollector()
                record = collector.collect(ref["tool_name"], ref["query"], output)
                format_kind = {"published_inventory_risk_analysis": "inventory_risk",
                               "published_inventory_fifo_plan": "inventory_fifo"}.get(ref["tool_name"], kind)
                answer, source = (self.response_generator.draft_email([record], [kind]) if email
                                  else self.response_generator.generate([record], [format_kind]))
                if re.search(r"从哪里来|来源是什么|依据是什么", message):
                    answer, source = source_overview(kind, output), "deterministic"
                # This is an explanation/draft, not a newly executed query result card.
                result = dict(intent=plan.intent, status=output["status"],
                    answer="依据本会话已保存的分析证据（不是最新库存重查）：\n" + answer,
                    structured_data=dict(query=copy.deepcopy(query), data=None, completed_steps=[],
                        operation=plan.operation, response_source=source, reused_result=copy.deepcopy(output),
                        reused_from_turn_id=turn["turn_id"]), provenance=copy.deepcopy(output["provenance"]),
                    warnings=list(output["warnings"]))
                result["structured_data"]["business_decision"] = business_decision(kind, output)
                return OrchestratedTurn(result, context, [record.reference()])
        return None

    @staticmethod
    def _context(context, plan, result):
        if plan.intent != "clarification" and plan.entities.material_code:
            context.pop('last_query_failure', None)
            domain = "production" if plan.intent in {"production_capacity", "purchase_planning"} else "fact"
            saved = {"query_type": plan.intent, **plan.entities.model_dump()}
            if result:
                query, data = result["query"], result["data"] or {}
                saved["material_code"] = (query.get("resolved_material_code") or query.get("resolved_parent_material_code")
                                          or (data.get("product") if domain == "production" else None)
                                          or saved["material_code"])
            context[domain] = saved
            context["active_domain"] = domain
        return context


__all__ = ["AgentOrchestrator", "OrchestratedTurn"]
