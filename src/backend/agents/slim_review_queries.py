"""Evidence-grounded dialogue over an explicitly bound product dataset."""
import copy
import re

from agents.enterprise_book_queries import EnterpriseBookQueries
from agents.evidence import EvidenceCollector
from agents.orchestrator import OrchestratedTurn
from services.persistent_agent_service import parse_enterprise_query
from tools.book_inventory_tools import build_book_inventory_tools
from services.slim_review_presentation import SlimReviewPresentation


class SlimReviewQueries:
    def __init__(self, service, presentation=None):
        self.service = service
        self.tools = build_book_inventory_tools(service)
        self.presentation = presentation or SlimReviewPresentation(service.dataset)

    def run(self, message, context, history_loader):
        text = message.strip()
        if self.service.dataset.get("current_business"):
            from agents.business_language import conversational
            if conversational(text):
                return None  # Existing Orchestrator conversation path; no business calculation.
        try:
            if re.search(r"生产\s*(?:[-−]|负|\d+\.\d|\d+[eE][+-]?\d|\d+\s*/\s*\d+|[a-z]+\s*(?:片|个|件))", text, re.I):
                raise ValueError("Invalid quantity")
            parsed, _ = parse_enterprise_query(text, allow_llm=False)
        except ValueError:
            return EnterpriseBookQueries._invalid_quantity(context)
        dataset = self.service.dataset
        product = dataset["product"]
        if dataset.get("current_business") and re.search(r"历史BOM|归档BOM|旧版BOM|原始66|完整追溯", text, re.I):
            return OrchestratedTurn(dict(intent="clarification", status="data_unavailable",
                answer=f"当前产品BOM为{dataset['revision']}，共{dataset['retained_material_count']}项物料。请按当前产品BOM继续查询。",
                structured_data=dict(data=None, completed_steps=[]), provenance=None, warnings=[]), copy.deepcopy(context), [])
        if dataset.get("current_business") and re.search(r"(?<![A-Za-z0-9_-])V\d+(?:[-.][A-Za-z0-9]+)*(?![A-Za-z0-9_-])", text, re.I):
            requested_version = re.search(r"(?<![A-Za-z0-9_-])V\d+(?:[-.][A-Za-z0-9]+)*(?![A-Za-z0-9_-])", text, re.I)[0]
            if requested_version.upper() != dataset["revision"].upper():
                return OrchestratedTurn(dict(intent="clarification", status="data_unavailable",
                    answer=f"当前产品BOM版本为{dataset['revision']}。请使用产品目录中的版本继续查询。",
                    structured_data=dict(data=None, completed_steps=[]), provenance=None, warnings=[]), copy.deepcopy(context), [])
        # Aliases are disclosure identifiers bound to THIS dataset, not new item master entries.
        aliases = self.presentation.aliases
        requested_alias = next((a for a in aliases.values() if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(a) + r"(?![A-Za-z0-9_-])", text)), None)
        if parsed.material_code in aliases:
            requested_alias = aliases[parsed.material_code]
        # Explicit foreign products must not inherit the filtered product.
        if parsed.material_code and parsed.material_code not in (product, dataset.get("display_product_code")) and not requested_alias:
            if re.search(r"库存|料号|存量|物料", text) and not re.search(r"生产|投产|BOM|物料清单", text):
                # 料号在当前查询范围内查不到：明确说“未找到”，不能只提示产品范围，也不能当作零库存。
                return OrchestratedTurn(dict(intent="inventory", status="item_not_found",
                    answer=(f"未找到物料编码 {parsed.material_code} 的匹配记录，请核对料号。"
                            f" 当前查询范围（{product}）内未找到该料号；这不代表库存为零。"),
                    structured_data=dict(query_type="inventory", query=dict(material_code=parsed.material_code),
                        data=dict(material_code=parsed.material_code, record_found=False, scope="current_query_range"),
                        completeness={"record_found": False}, completed_steps=[]),
                    provenance=None, warnings=[]), copy.deepcopy(context), [])
            return OrchestratedTurn(dict(intent="clarification", status="data_unavailable",
                answer="当前分析范围只适用于所选产品。请选择对应产品后继续查询。",
                structured_data=dict(data=None, completed_steps=[]), provenance=None, warnings=[]), copy.deepcopy(context), [])
        quantity = parsed.quantity
        previous = context.get("slim_analysis", {})
        from agents.query_presentation import maximum_requested
        maximum = maximum_requested(text)
        if (quantity is None and re.search(r"生产|做", text) and not maximum
                and not re.search(r"最多|最大|上限|为什么|原因|限制|影响", text)):
            return self.service_needs_quantity(context, product, dataset["revision"])
        # “再生产20套”直接按 20 套核对库存，不再打断追问是否追加：
        # 用户意图是确认这个数量够不够，是否与上次数量叠加不影响本次核对。

        explanation = bool(re.search(r"为什么|原因|来源|哪里|哪些物料限制|哪些.*影响|哪些.*不足|限制在哪里", text))
        refresh = bool(re.search(r"重新计算|按最新|再查一次|重新查询", text))
        if explanation and previous and not refresh:
            desired = quantity if quantity is not None else previous.get("quantity") if not re.search(r"来源|哪里", text) else None
            for turn in reversed(history_loader()):
                for ref in turn["fact_references"]:
                    data = ref["result"].get("data") or {}
                    if ref["tool_name"] == "enterprise_book_demand_analysis" and data.get("slim_review") and (desired is None or data.get("requested_quantity") == desired):
                        return self._turn(ref["result"], context, message, reused=turn["turn_id"])
        produce_max = bool(re.search(r"生产(?:到)?(?:最大|最多)数量|按最大|生产上限", text))
        capacity = bool(re.search(r"最多|最大|限制产量", text)) or maximum
        if produce_max:
            maximum_result = self.tools["enterprise_book_demand_analysis"].invoke(dict(product=product, quantity=None))
            maximum = (maximum_result.get("data") or {}).get("theoretical_max_quantity")
            if maximum is None:
                return self._turn(maximum_result, context, message)
            if maximum == 0:
                return self._turn(maximum_result, context, message)
            quantity = maximum
        elif re.search(r"再多生产|再增加|多生产", text) and quantity is not None:
            baseline = previous.get("last_target", previous.get("quantity"))
            if baseline is None:
                baseline = previous.get("maximum")
            if baseline is None:
                return OrchestratedTurn(dict(intent="clarification", status="needs_input", answer="请先查询最大数量或指定生产数量，再调整数量。",
                    structured_data=dict(data=None, completed_steps=[]), provenance=None, warnings=[]), copy.deepcopy(context), [])
            quantity += baseline
        elif capacity:
            quantity = None
        # Zero is never converted to 1. The parser validation runs before Tool.
        result = self.tools["enterprise_book_demand_analysis"].invoke(dict(product=product,
            revision=dataset["revision"], quantity=quantity))
        if requested_alias:
            code = next(c for c, a in aliases.items() if a == requested_alias)
            rows = (result.get("data") or {}).get("materials", [])
            if not any(r["material_code"] == code for r in rows):
                return OrchestratedTurn(dict(intent="inventory", status="item_not_found", answer="该物料不在当前分析范围内。",
                    structured_data=dict(data=None, completed_steps=[]), provenance=None, warnings=[]), copy.deepcopy(context), [])
            # Retain the whole calculation Evidence; disclose only the explicitly requested row.
            turn = self._turn(result, context, message)
            data = turn.response["structured_data"]["data"]
            data["materials"] = [r for r in data["materials"] if r["material_code"] == requested_alias]
            row = data["materials"][0]
            turn.response["answer"] = f"{requested_alias}：BOM单耗{row['qty_per']}片，当前分析范围库存{row['recorded_stock_qty']}片。来源为已核验记录。"
            return turn
        return self._turn(result, context, message)

    def _turn(self, result, context, message, reused=None):
        data = result.get("data") or {}
        from agents.query_presentation import maximum_requested
        show_maximum = maximum_requested(message)
        kind = ("production_capacity" if show_maximum else
            "bom" if re.search(r"BOM|物料组成|参与计算|多少项物料|哪些物料参与|有哪些物料|全部.*物料", message, re.I)
            else "inventory" if re.search(r"库存|存量", message) and data.get("requested_quantity") is None
            else "production_capacity")
        saved = copy.deepcopy(context)
        if result["status"] == "ok":
            prior = saved.get("slim_analysis", {})
            saved["slim_analysis"] = dict(product=data["product"], revision=data["revision"],
                quantity=data["requested_quantity"] if data["requested_quantity"] is not None else prior.get("quantity"),
                maximum=data["theoretical_max_quantity"],
                last_target=data["requested_quantity"] if data["requested_quantity"] is not None
                    else data["theoretical_max_quantity"] if re.search(r"最多|最大", message)
                    else prior.get("last_target", prior.get("quantity")))
            saved["active_domain"] = "production"
            saved["production"] = dict(material_code=data["product"], revision=data["revision"],
                quantity=saved["slim_analysis"]["quantity"], query_type="production_capacity")
        original = result
        result = self.presentation.result(result)
        data = result.get("data") or {}
        if result["status"] != "ok":
            answer = "BOM需求已计算，库存数据暂不可核对，尚未生成物料满足结论。" if data.get("stock_read_failed") else "本次分析数据暂不可计算，请核对数据来源后重试。"
        else:
            maximum = data["theoretical_max_quantity"]
            current = self.service.dataset.get("current_business")
            scope = "当前产品" if current else "当前分析范围"
            count_text = f"当前BOM包含{data['total_material_count']}种物料。" if current else f"当前分析范围：{data['total_material_count']}项物料。"
            answer = f"已完成 {data['product']} 本次{'BOM查询' if kind == 'bom' else '库存记录查询' if kind == 'inventory' else '生产需求核对'}。" + count_text
            if show_maximum:
                answer += f"{'最大满足数量' if current else '当前范围最大满足数量'}为{maximum}片。"
            if data["synthetic"]:
                answer += "本次包含演示库存，仅用于数量模拟。"
            n = data["requested_quantity"]
            if n is not None:
                rows = data.get("materials") or []
                shortages = data["book_shortages"]
                ok_count = sum(1 for r in rows if r.get("quantity_status") == "sufficient")
                missing = sum(1 for r in rows if r.get("comparison_status") == "no_stock")
                verdict = "满足" if data["inventory_sufficient_for_requested"] else "不能满足"
                answer += (f"按账面数量逐项核对{len(rows)}种物料：{ok_count}种满足、{len(shortages)}种不足"
                           + (f"、{missing}种库存未查询到" if missing else "") + "；"
                           + f"{scope}{verdict}生产{n}片对应的物料需求。")
                if data["book_shortages"]:
                    # 结论只给数量级判断，缺口明细交由下方物料列表呈现，避免回答冗长。
                    answer = answer.rstrip("。") + "；不足物料详见下面列表。"
            if show_maximum or re.search(r"限制", message):
                answer += "限制产量的物料：" + "、".join(f"{r['name'] or r['material_code']}（{r['material_code']}）" for r in data["limiting_materials"][:5]) + "。"
            if re.search(r"来源|哪里", message):
                answer += "BOM单耗来自当前业务BOM文件，库存数量来自已核验业务库存文件；可在来源区查看本次依据。" if self.service.dataset.get("current_business") else "BOM单耗来自已发布记录，库存数量来自已核验主库存记录；可在来源区查看本次依据。"
                if data["synthetic"]:
                    answer += "演示库存补充逐项单独记录，不是原主库存数量。"
            if reused:
                answer = "依据本会话已有证据：" + answer
            if not current:
                answer += "结果仅适用于当前分析范围，不代表完整产品生产放行。"
        structured = dict(query_type=kind, query=result["query"], data=result["data"],
            completeness=result["completeness"], response_source="deterministic", analysis_level="slim_review",
            presentation=dict(show_maximum=show_maximum),
            completed_steps=[] if reused else [dict(operation="enterprise_book_demand_analysis", status=result["status"])])
        if self.service.dataset.get("current_business"):
            structured["analysis_level"] = "current_business"
        if reused:
            structured.update(operation="explain", reused_from_turn_id=reused)
        response = dict(intent=kind, status=result["status"], answer=answer,
            structured_data=structured, provenance=result["provenance"], warnings=result["warnings"])
        evidence = EvidenceCollector().collect("enterprise_book_demand_analysis", original["query"], original).reference()
        return OrchestratedTurn(response, saved, [evidence])

    @staticmethod
    def service_needs_quantity(context, product, revision):
        return OrchestratedTurn(dict(intent="clarification", status="needs_input",
            answer=f"已选择{product} / {revision}。请告诉我希望生产多少片，例如1片或50片。",
            structured_data=dict(query_type="production_capacity", data=None, completed_steps=[], missing_parameters=["quantity"]),
            provenance=None, warnings=[]), copy.deepcopy(context), [])
