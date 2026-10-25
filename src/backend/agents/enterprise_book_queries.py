"""Local-only partial analysis adapter, isolated from formal production approval.

Never sends source observations, private prompts, or stored Evidence to an LLM.
The original orchestrator still owns all other published-fact capabilities.
"""
import copy
import re

from agents.evidence import EvidenceCollector
from agents import business_language as language
from services.persistent_agent_service import parse_enterprise_query


class EnterpriseBookQueries:
    def __init__(self, service):
        self.service = service
        from tools.book_inventory_tools import build_book_inventory_tools
        self.tools = build_book_inventory_tools(service)

    def run(self, message, context, history_loader):
        text = language.normalize_business_dates(message)
        if language.conversational(text) or language.product_discovery(text)[0]:
            return None
        try:
            if re.search(r"(?:生产|数量|做)\s*(?:[-−]\s*\d|\d+\.\d|\d+[eE][+-]?\d|[A-Za-z]+\s*(?:个|件|片))", text):
                raise ValueError("Invalid production quantity")
            parsed, _ = parse_enterprise_query(language.identifier_message(text), allow_llm=False)
            if re.search(r"生产\s*(?:负数|负[零〇一二三四五六七八九十百千万两\d]+|非法数量|\d+\s*/\s*\d+)", text):
                raise ValueError("Invalid production quantity")
        except ValueError:
            return self._invalid_quantity(context)
        previous = context.get("book_analysis", {})
        from agents.query_presentation import maximum_requested
        maximum = maximum_requested(text)
        active = context.get(context.get("active_domain"), {})
        # The currently selected query object wins over a prior book-analysis
        # object, including after a published BOM query or a failed product switch.
        code = parsed.material_code or active.get("material_code") or previous.get("product")
        if not code:
            return None
        quantity = parsed.quantity
        revision = parsed.revision
        same = code == previous.get("product")
        if quantity is None and (same or re.search(r"换成|切换", text)):
            quantity = previous.get("quantity")
        if revision is None and same:
            revision = previous.get("revision")
        stock = bool(re.search(r"库存|存量|还有多少|还有几件|余量", text))
        if stock and parsed.quantity is None:
            quantity = None  # Inventory intent never inherits a production target.
        if maximum:
            quantity = None
        demand = bool(re.search(r"生产|需求|需要多少(?:物料|材料)|数量对照|账面|再算|重算|核算|算一次|算一下", text)) and quantity is not None
        from services.production_request_context import bare_quantity
        if parsed.quantity is not None and bare_quantity(text):
            demand = True
        needs_quantity = quantity is None and bool(re.search(r"生产|做", text))
        purchase = bool(re.search(r"采购建议|给个采购|采购多少|采购数量|采购量|买多少|补货|需要采购|材料需要采购", text))
        followup = bool(re.search(r"不够|不足|缺料|差多少|缺口|未匹配|没.*记录|为什么|依据|来源|来自哪里|怎么.*判断|哪些(?:物料|材料).*(?:影响|限制)|限制在哪里", text))
        switch = bool(re.search(r"换成|切换", text)) and bool(previous)
        if not (stock or demand or purchase or followup or switch or needs_quantity or maximum):
            return None
        # Explicit authority/FIFO queries retain their existing strict semantics.
        # Capacity uses this source-scoped actual-quantity path, not snapshot gates.
        if re.search(r"FIFO|批次.*优先|临期|呆滞|权威可用", text, re.I):
            return None
        if stock and parsed.as_of:
            return None
        if context.get("last_query_failure") and parsed.material_code is None and parsed.quantity is None:
            return None
        if previous and same and (purchase or followup) and not language.refresh_requested(text):
            for turn in reversed(history_loader()):
                for reference in turn["fact_references"]:
                    if reference["tool_name"] not in ("enterprise_book_demand_analysis", "enterprise_book_capacity_query"):
                        continue
                    result = reference["result"]
                    query = result["query"]
                    if query.get("product") == code and query.get("requested_quantity") == quantity:
                        return self._turn(result, context, purchase=purchase, reused=turn["turn_id"], message=message)
        identity = self.service.facts.repository.resolve_item(code)
        canonical = identity.get("material_code") if identity and not identity.get("ambiguous") else code
        versions = self.service.facts.repository.list_published_bom_versions(canonical)
        if not versions:
            if not stock:
                return None
            result = self.tools["enterprise_book_inventory_query"].invoke(dict(material_code=code))
            if result["status"] == "item_not_found":
                # 明确区分“未找到料号”和“库存缺失/为零”：这里是当前查询范围内未找到。
                from agents.orchestrator import OrchestratedTurn
                answer = (f"未找到物料编码 {code} 的匹配记录，请核对料号。"
                          " 当前查询范围内未找到该料号，这不代表库存为零，也不代表它一定不存在于全部库存中。")
                response = dict(intent="inventory", status="item_not_found", answer=answer,
                    structured_data=dict(query_type="inventory", query=dict(material_code=code),
                        data=dict(material_code=code, record_found=False, scope="current_query_range"),
                        completeness={"record_found": False}, completed_steps=[]),
                    provenance=None, warnings=[])
                return OrchestratedTurn(response, copy.deepcopy(context), [])
            return self._turn(result, context, inventory=True, message=message)
        if maximum:
            result = self.tools["enterprise_book_capacity_query"].invoke(dict(product=code, revision=revision))
            if parsed.effective_date:
                result["query"]["requested_analysis_date"] = parsed.effective_date
            return self._turn(result, context, message=message)
        if quantity is None and not stock:
            if needs_quantity and len(versions) == 1 and revision in (None, versions[0]["version"]):
                return self._needs_quantity(context, canonical, versions[0]["version"])
            return None
        result = self.tools["enterprise_book_demand_analysis"].invoke(dict(product=code, revision=revision,
            quantity=quantity, requested_unit=language.production_unit(text) if parsed.quantity is not None else None))
        return self._turn(result, context, purchase=purchase, inventory=stock and quantity is None, message=message)

    @staticmethod
    def _invalid_quantity(context):
        from agents.orchestrator import OrchestratedTurn
        active = context.get(context.get("active_domain"), {})
        code = active.get("material_code") or context.get("book_analysis", {}).get("product") or "该产品"
        response = dict(intent="production_capacity", status="invalid_query",
            answer=f"生产数量需要大于0且为整数。请告诉我希望生产多少片{code}，例如1片、50片或100片。",
            structured_data=dict(operation="quantity_validation", query_type="production_capacity", data=None,
                completed_steps=[], validation_error="quantity_must_be_positive_integer"), provenance=None, warnings=[])
        # User/error message may be kept in history. Never overwrite a legitimate
        # business context, quantity or Evidence with the invalid input.
        return OrchestratedTurn(response, copy.deepcopy(context), [])

    def _needs_quantity(self, context, product, revision):
        from agents.orchestrator import OrchestratedTurn
        result = self.service.facts.query_bom(product, revision)
        if result["status"] != "ok":
            return None
        data = result["data"]
        unit = data["parent_item"].get("unit")
        saved = copy.deepcopy(context)
        saved.pop("last_query_failure", None)
        saved.pop("book_analysis", None)
        saved["active_domain"] = "production"
        saved["production"] = dict(query_type="production_capacity", material_code=product,
            revision=revision, quantity=None)
        response = dict(intent="clarification", status="needs_input",
            answer=f"已找到{product} / {revision}，共{data['line_count']}项BOM。请告诉我希望生产多少{unit or '成品单位'}，例如1、50或100。",
            structured_data=dict(operation="quantity_clarification", query_type="production_capacity",data=None,
                query=dict(product=product,revision=revision), missing_parameters=["quantity"],completed_steps=[]),
            provenance=result["provenance"],warnings=[])
        reference=EvidenceCollector().collect("bom_query",result["query"],result).reference()
        return OrchestratedTurn(response,saved,[reference])

    def _turn(self, result, context, *, purchase=False, inventory=False, reused=None, message=""):
        from agents.orchestrator import OrchestratedTurn
        data = result.get("data") or {}
        kind = "purchase_planning" if purchase else "inventory" if inventory else "production_capacity"
        tool = ("enterprise_book_capacity_query" if data.get("capacity_query") else
            "enterprise_book_inventory_query" if inventory else "enterprise_book_demand_analysis")
        record = EvidenceCollector().collect(tool, result["query"], result)
        saved = copy.deepcopy(context)
        if result["status"] == "ok":
            saved.pop("last_query_failure", None)
            saved["book_analysis"] = dict(product=data.get("product"), revision=data.get("revision"), quantity=data.get("requested_quantity"))
            domain = "fact" if inventory else "production"
            saved[domain] = dict(query_type=kind, material_code=data.get("product"), revision=data.get("revision"), quantity=data.get("requested_quantity"))
            saved["active_domain"] = domain
        elif result["status"] == "query_failed":
            if data.get("product"):
                saved["book_analysis"] = dict(product=data["product"], revision=data["revision"], quantity=data.get("requested_quantity"))
                saved["production"] = dict(query_type=kind, material_code=data["product"], revision=data["revision"], quantity=data.get("requested_quantity"))
                saved["active_domain"] = "production"
            saved["last_query_failure"] = dict(intent=kind, entities=dict(material_code=result["query"].get("product")))
        structured = dict(query_type=kind, query=result["query"], data=result["data"], completeness=result["completeness"],
            response_source="deterministic", completed_steps=[] if reused else [dict(operation=tool, status=result["status"])],
            analysis_level="book_observation", business_decision=dict(status="NOT_APPROVED", approved=False,
                reason="账面记录核对不是正式生产许可"))
        if reused:
            structured.update(reused_from_turn_id=reused, operation="explain")
        if data.get("quantity_review"):
            structured["analysis_level"] = "actual_quantity_review"
            structured["business_decision"] = dict(status=data.get("quantity_check_status"),
                approved=False, quantity_requirements_satisfied=data.get("inventory_sufficient_for_requested"),
                reason="实际库存数量核对，不代表正式工单放行")
        response = dict(intent=kind, status=result["status"], answer=self.answer(result, purchase, reused),
            structured_data=structured, provenance=result["provenance"], warnings=result["warnings"])
        if result["status"] == "ok" and re.search(r"来源|依据|来自哪里", message):
            summary = data["source_summary"]
            response["answer"] = (f"{data.get('product')} / {data.get('revision') or ''}：BOM单耗来自已发布记录，库存数量来自 {summary['source_file']} 的“{summary['sheet_name']}”。"
                "逐项来源可在来源详情查看。")
        if result["status"] == "ok" and re.search(r"未匹配|没.*记录", message) and data.get("no_record_materials"):
            response["answer"] += "无记录物料示例：" + "、".join(data["no_record_materials"][:5]) + "；完整列表见明细。"
        if result["status"] == "ok" and re.search(r"换成|切换", message) and data.get("product"):
            quantity = data.get("requested_quantity")
            head = (f"已切换到 {data['product']}"
                    + (f"，按 {quantity} {data.get('product_unit') or '片'}重新核算。" if quantity is not None else "。"))
            if not response["answer"].startswith(head):
                response["answer"] = head + response["answer"]
        return OrchestratedTurn(response, saved, [record.reference()])

    @staticmethod
    def answer(result, purchase=False, reused=None):
        if result["status"] != "ok" and not (result.get("data") or {}).get("materials"):
            return "暂不能完成本次账面核对。" + "；".join(result["warnings"])
        data = result["data"]; rows = data["materials"]
        code = data["product"]
        prefix = "依据本会话已保存的账面证据（不是最新库存重查）：\n" if reused else ""
        if data.get("capacity_query"):
            maximum = data.get("verified_material_capacity")
            if maximum is None:
                return prefix + f"已找到{code} / {data['revision']}的BOM和库存明细，当前没有可比较的数量记录，未计算产量上限。"
            text = prefix + f"按本次核验的Excel库存，{code}有库存记录的{data['verified_material_count']}项物料，其产量上限为{maximum}{data['product_unit']}。"
            if data["capacity_scope"] == "complete":
                text += "已覆盖本次BOM全部物料。"
            elif maximum == 0:
                text += "其中必需物料不足1片用量，完整BOM也无法满足任何正整数数量的物料需求。"
            else:
                text += "该上限仅覆盖有库存记录的物料，不代表完整BOM齐套。"
            if data["limiting_materials"]:
                text += "限制物料：" + "；".join(f"{r['material_code']}（库存{r['stock_qty']}，单耗{r['qty_per']}）" for r in data["limiting_materials"][:5]) + "。"
            return text
        if "total_material_count" not in data:
            row = rows[0]
            unit = row["inventory_unit"] or ""
            total = (f"账面数量合计 {row['recorded_stock_qty']} {unit}。" if row["recorded_stock_qty"] is not None
                else f"库存表未查询到{code}。")
            return prefix + (f"{code} 在主库存表有 {row['record_count']} 条记录，{total}"
                if row["record_found"] else f"{code} 在主库存表无记录。")
        count = data["total_material_count"]; matched = data["matched_material_count"]
        if data.get("stock_read_failed"):
            return prefix + f"本次生产{data['requested_quantity']}片{code}，已计算{sum(r['required_qty'] is not None for r in rows)}项BOM需求。BOM需求已保留；库存文件本次读取失败，请稍后重试。"
        scope = "推演库存覆盖" if data["source_summary"].get("review_scenario") else "主库存表匹配"
        text = prefix + f"{code} / {data['revision']}：{count}项物料，{scope}{matched}项。"
        if data.get("missing_stock_material_count"):
            text += f"库存未查询到{data['missing_stock_material_count']}种，需求明细已保留，尚不能计算其采购数量。"
        if data["requested_quantity"] is not None:
            text += f"本次生产 {data['requested_quantity']} {data['product_unit'] or '单位待确认'}，已计算 {sum(r['required_qty'] is not None for r in rows)} 项BOM需求。"
        else:
            unit = (data["source_summary"].get("unit_confirmation") or {}).get("unit")
            return text + (f"库存数量按{unit}展示。" if unit else "") + "库存数量及记录见物料明细。"
        if purchase:
            text += "企业评审入口提供BOM需求与库存记录查询，不提供正式采购数量建议；完整采购流程可在隔离合成Demo中体验。"
        if data.get("stock_read_failed"):
            return text + "BOM需求已保留；库存文件本次读取失败，请稍后重试。"
        if data.get("quantity_review"):
            state = data.get("quantity_check_status")
            if state == "sufficient":
                text += (f"按当前确认的实际库存数量核对，{data['comparable_material_count']}项物料全部满足，"
                    f"可以满足生产{data['requested_quantity']}{data.get('product_unit') or '片'}的物料需求，无需补充材料。")
            elif state == "insufficient":
                gaps = data["book_shortages"]
                ok_count = data["comparable_material_count"] - len(gaps)
                text += f"按当前库存数量逐项核对：{ok_count}项满足、{len(gaps)}项不足。"
                # 结论只给摘要，缺口逐项明细由下方物料明细表承载，避免正文过长。
                text += f"不足物料（需补充）共{len(gaps)}种，缺口明细见下方物料明细表；按缺口补齐后方可齐套。"
            elif data["requested_quantity"] is not None:
                text += "已有记录的物料已逐项计算，尚不能证明全部齐套。"
            if data["requested_quantity"] is not None and data.get("missing_stock_material_count"):
                text += "无记录物料保留需求明细，未生成缺口或采购数量；未查询到不等于零库存。"
            if data["source_summary"].get("review_scenario"):
                text = "【评审推演：包含合成补充库存，不代表真实库存】\n" + text
            return text
        text += "右侧可查看全部需求、账面数量、库位批次和行级来源。账面数量不等于可用库存；当前不能批准生产。"
        return text
