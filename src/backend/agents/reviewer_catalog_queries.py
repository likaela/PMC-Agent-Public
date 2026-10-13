"""Product discovery from authorized, runtime-bound catalogs; no business math."""
import copy
import re

from agents.evidence import EvidenceCollector


class ReviewerOverviewQueries:
    """Bare selected-product lookup is a BOM overview, not a guessed stock fact."""
    def __init__(self, service, product, delegate=None):
        self.service, self.product, self.delegate = service, product, delegate

    def run(self, message, context, history_loader):
        if re.fullmatch(r"\s*(?:查询|查看|查一下)\s*" + re.escape(self.product) + r"[。！？?\s]*", message):
            return self.service.orchestrator.run("查询" + self.product + "的BOM", context, history_loader=history_loader)
        return self.delegate.run(message, context, history_loader) if self.delegate else None


def catalog_kind(message):
    if not re.search(r"产品|目录|已发布.*BOM|正式.*BOM", message, re.I):
        return None
    if not re.search(r"哪些|有哪些|目录|已发布|正式|可以使用|能查询|能查什么", message, re.I):
        return None
    return "enterprise" if re.search(r"只.*(?:企业|正式)|正式企业|企业正式", message) else "synthetic" if re.search(r"哪些.*Demo", message, re.I) else "all"


def catalog_query(service, session_id, message, entries):
    kind = catalog_kind(message)
    if kind is None:
        return None
    entries = [p for p in entries if kind == "all" or p["synthetic"] == (kind == "synthetic")]
    rows = [dict(product_code=p["product_code"], product_name=p.get("product_name"),
        published_bom_status="synthetic" if p["synthetic"] else "published", synthetic=p["synthetic"],
        revisions=[dict(revision=p["revision"])], modes=copy.deepcopy(p["modes"]), default_mode=p["default_mode"])
        for p in entries]
    output = dict(status="ok", query={"scope": kind}, data={"products": rows},
        completeness={"authorized_catalog": True}, provenance={"source_kind": "authorized_query_catalog"}, warnings=[])
    session = service.repository.get_session(session_id)
    answer = "当前可查询产品：" + "；".join(p["product_code"] + ("（演示产品）" if p["synthetic"] else "") for p in entries) + "。请选择产品和分析范围。"
    response = dict(intent="fact_query", status="ok", answer=answer,
        structured_data=dict(query_type="product_search", data=output["data"], completeness=output["completeness"],
            completed_steps=[dict(operation="published_product_search", status="ok")]),
        provenance=output["provenance"], warnings=[], session_id=session_id, conversation_id=session["conversation_id"])
    scenario_id = session.get("scenario_id") or session["context"].get("scenario_id")
    if scenario_id:
        response["scenario_id"] = scenario_id
    ref = EvidenceCollector().collect("published_product_search", output["query"], output).reference()
    response["turn_id"] = service.repository.append_turn(session, message, response, session["context"], [ref])
    return response


def overview_query(service, session_id, message, entry, scope):
    """Read the bound published BOM, without invoking inventory/capacity tools."""
    session = service.repository.get_session(session_id)
    args = dict(parent_material_code=entry["product_code"], revision=entry["revision"])
    output = service.orchestrator.registry.resolve("bom_query").invoke(args)
    data = dict(product=entry["product_code"], revision=entry["revision"], unit=entry.get("unit"),
        line_count=scope["line_count"], analysis_scope=scope["label"], capabilities=entry.get("capabilities", [])) if output["status"] == "ok" else None
    response = dict(intent="fact_query", status=output["status"],
        answer=f"{entry['product_code']} / {entry['revision']}，当前{scope['label']}包含{scope['line_count']}项物料。可继续查询BOM、库存记录或指定生产数量。" if data else "当前产品的已发布BOM暂不可读取。",
        structured_data=dict(query_type="product_overview", data=data, query=args, completeness=output["completeness"],
            completed_steps=[dict(operation="bom_query", status=output["status"])]),
        provenance=output["provenance"], warnings=output["warnings"], session_id=session_id, conversation_id=session["conversation_id"])
    if getattr(service, "presentation_binding", None):
        response["structured_data"]["product_context"] = copy.deepcopy(service.presentation_binding)
    scenario = session.get("scenario_id") or session["context"].get("scenario_id")
    if scenario:
        response["scenario_id"] = scenario
    response["turn_id"] = service.repository.append_turn(session, message, response, session["context"],
        [EvidenceCollector().collect("bom_query", args, output).reference()])
    return response
