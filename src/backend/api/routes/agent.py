"""新统一入口：只分流，PMC/Fact Query 各自沿用现有受控实现。"""

from __future__ import annotations
import os

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from agents.fact_query_agent import run_fact_query_agent
from agents.production_feasibility_agent import run_production_feasibility_agent
from api.routes.chat import chat
from api.schemas.agent import (UnifiedAgentRequest, UnifiedAgentResponse, AgentSessionResponse, AgentSessionHistory,
                               AgentSessionCreate, ScenarioId)
from api.schemas.chat import ChatClarificationResponse, ChatRequest, ChatResponse
from services.agent_intent_router import route_agent_message
from services.fact_dialogue_context_service import (
    get_fact_dialogue_context,
    remember_fact_dialogue_context,
)
from workflows.pmc_graph import get_pmc_workflow_state
from repositories.agent_conversation_repository import AgentConversationRepository, SessionNotFound, SessionConflict
from repositories.fact_store import create_fact_store
from services.persistent_agent_service import PersistentAgentService
from pydantic import BaseModel, Field, ConfigDict
router = APIRouter(prefix="/api/v1/agent", tags=["Unified Agent"])


class ProductResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None
    scenario_id: ScenarioId | None = None
    catalog_order: list[str] | None = None


@router.post("/resolve")
def resolve_agent_product(request: ProductResolveRequest):
    from agents.product_resolver import resolve_product
    from services.local_reviewer_catalog import local_review_catalog
    current = None
    if request.session_id:
        try:
            service = (_current_service(session_id=request.session_id) if request.scenario_id is None else None) or _persistent_service(request.scenario_id)
            context = service.repository.get_session(request.session_id)["context"]
            current = _product_context(context).get("product_code")
        except SessionNotFound as error:
            return _session_error(error)
    return resolve_product(request.message, local_review_catalog(create_fact_store()), current_product=current,
        catalog_order=request.catalog_order)




@router.get("/catalog")
def reviewer_catalog():
    """Local authorized workstation; remote review has its own authenticated catalog."""
    from services.local_reviewer_catalog import local_review_catalog
    return {"products": local_review_catalog(create_fact_store())}


def _persistent_service(scenario_id: ScenarioId | None = None) -> PersistentAgentService:
    if scenario_id in ("kq_z112_review_slim", "kq_z112_review_slim_synthetic"):
        from services.slim_scenario_agent_service import build_slim_scenario_agent
        service = build_slim_scenario_agent(scenario_id)
        service.enable_orchestrator()
        return service
    if scenario_id == "review_real_bom_simulation":
        from services.review_scenario_agent_service import build_review_scenario_agent
        service = build_review_scenario_agent()
        service.enable_orchestrator()
        return service
    if scenario_id is not None:
        from services.demo_scenario_agent_service import build_demo_scenario_agent
        service = build_demo_scenario_agent()
        service.enable_orchestrator()
        return service
    # 部署时显式 migrate；问答请求不改变企业事实/数据库结构。
    service = PersistentAgentService(AgentConversationRepository(create_fact_store()))
    service.enable_orchestrator()
    return service


def _current_service(product=None, session_id=None):
    from services.local_business_binding import local_business_service
    return local_business_service(product=product, session_id=session_id)


def _session_error(error: Exception) -> JSONResponse:
    if isinstance(error, SessionNotFound):
        return JSONResponse(status_code=404, content={"code": "SESSION_NOT_FOUND", "user_message": "当前企业会话不存在，请重新创建会话。"})
    return JSONResponse(status_code=409, content={"code": "SESSION_CONFLICT", "user_message": "当前会话已有新消息，请刷新后重试。"})


def _product_context(context):
    """Activity belongs to a product, not to a subsequently queried child material."""
    binding = context.get("product_context")
    if binding:
        return binding
    task = context.get("production") or context.get("fact") or {}
    return dict(product_code=task.get("material_code"), revision=task.get("revision"),
        scenario_id=context.get("scenario_id"))


@router.post("/sessions", response_model=AgentSessionResponse)
def create_agent_session(request: AgentSessionCreate | None = None):
    """创建企业会话或显式独立场景会话；不初始化或重建旧 Demo Workflow。"""
    from services.local_business_binding import local_business_binding
    if local_business_binding() and request and request.scenario_id in ("kq_z112_review_slim", "kq_z112_review_slim_synthetic", "review_real_bom_simulation"):
        return JSONResponse(status_code=422, content={"code":"ARCHIVED_CONFIGURATION", "user_message":"旧版本配置已归档，请新建当前产品分析。"})
    product = request.product_code if request else None
    service = _current_service(product=product) if not (request and request.scenario_id) else None
    service = service or _persistent_service(request.scenario_id if request else None)
    entry = None
    if product:
        from services.local_reviewer_catalog import local_review_catalog
        entry = next((p for p in local_review_catalog(create_fact_store()) if p["product_code"] == product), None)
        if not entry or not any(m["scenario_id"] == request.scenario_id for m in entry["modes"]):
            return JSONResponse(status_code=422, content={"code":"PRODUCT_REQUIRED", "user_message":"请选择可访问的产品和分析范围。"})
    initial = dict(active_domain="fact", fact=dict(material_code=product, revision=entry["revision"], quantity=None, query_type="bom"),
        production=dict(material_code=product, revision=entry["revision"], quantity=None, query_type="production_capacity")) if product and request.scenario_id is None else None
    if initial:
        initial["product_context"] = dict(product_code=product, scenario_id=None, revision=entry["revision"])
    session = service.repository.create_session(**({"initial_context":initial} if initial else {}))
    result = service.public_session(session) if hasattr(service, "public_session") else session
    if product:
        result.setdefault("product_context", dict(product_code=product, scenario_id=request.scenario_id, revision=entry["revision"]))
    return result


@router.get("/sessions/{session_id}", response_model=AgentSessionHistory)
def get_agent_session(session_id: str, after: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                      scenario_id: ScenarioId | None = Query(None)):
    """分页恢复问答、上下文及每次调用的来源证据；不重新查询/计算历史事实。"""
    try:
        service = (_current_service(session_id=session_id) if scenario_id is None else None) or _persistent_service(scenario_id)
        history = service.repository.history(session_id, after=after, limit=limit)
        result = service.public_session(history) if hasattr(service, "public_session") else history
        binding = _product_context(history["context"])
        if binding["product_code"]:
            result["product_context"] = binding
        return result
    except SessionNotFound as error:
        return _session_error(error)


@router.post("/query", response_model=UnifiedAgentResponse)
def unified_agent_query(request: UnifiedAgentRequest) -> UnifiedAgentResponse:
    """统一自然语言入口，不改变旧 Chat、Demo 或 LangGraph 执行链。"""
    from services.local_business_binding import local_business_binding
    current_binding = local_business_binding() if (request.session_id or request.persist_session or request.scenario_id or os.getenv("PMC_LOCAL_REVIEW") == "1") else None
    if current_binding and request.scenario_id in ("kq_z112_review_slim", "kq_z112_review_slim_synthetic", "review_real_bom_simulation"):
        return JSONResponse(status_code=422, content={"code":"ARCHIVED_CONFIGURATION", "user_message":"旧版本配置已归档，请新建当前产品分析。"})
    if request.session_id or request.persist_session or request.scenario_id or os.getenv("PMC_LOCAL_REVIEW") == "1":
        service = (_current_service(session_id=request.session_id) if request.scenario_id is None else None) or _persistent_service(request.scenario_id)
        session_id = request.session_id or service.repository.create_session()["session_id"]
        try:
            from agents.reviewer_catalog_queries import catalog_query, catalog_kind
            from pathlib import Path
            from repositories.fact_store import DEFAULT_SQLITE_PATH
            database = getattr(create_fact_store(), "database_path", None)
            if current_binding or os.getenv("PMC_LOCAL_REVIEW") == "1":
                service.intent_presentation = True
                from services.local_reviewer_catalog import local_review_catalog
                from agents.product_resolver import resolve_product, product_overview_requested
                entries = local_review_catalog(create_fact_store())
                context = service.repository.get_session(session_id)["context"]
                service.presentation_binding = _product_context(context)
                current = _product_context(context).get("product_code")
                resolved = resolve_product(request.message, entries, current_product=current,
                    catalog_order=request.catalog_order)
                if resolved["status"] != "ok":
                    return UnifiedAgentResponse(intent="clarification", status=resolved["status"], answer=resolved["answer"],
                        structured_data={"data":None,"completed_steps":[]}, provenance=None, warnings=[], session_id=session_id)
                code = resolved.get("product_code")
                message = resolved.get("message", request.message)
                if code and current and code != current:
                    return JSONResponse(status_code=409, content={"code":"PRODUCT_SWITCH_REQUIRED", "user_message":"请为新产品创建会话；原分析已保留。"})
                entry = next((p for p in entries if p["product_code"] == code), None)
                if entry and entry["default_mode"] == "current" and not hasattr(service, "presentation"):
                    if current:
                        return JSONResponse(status_code=409, content={"code":"ARCHIVED_CONFIGURATION", "user_message":"该会话属于已归档BOM版本，请新建当前产品分析；历史结果未修改。"})
                    service = _current_service(product=code)
                    session_id = service.repository.create_session()["session_id"]
                    service.presentation_binding = _product_context(service.repository.get_session(session_id)["context"])
                if entry and product_overview_requested(message, code):
                    from agents.reviewer_catalog_queries import overview_query
                    scope = next(m for m in entry["modes"] if m["scenario_id"] == request.scenario_id)
                    return UnifiedAgentResponse.model_validate(overview_query(service, session_id, request.message, entry, scope))
                if not resolved.get("catalog_query"):
                    return UnifiedAgentResponse.model_validate(service.query(session_id, request.message, execution_message=message))
            if catalog_kind(request.message) and database and (current_binding or Path(database).resolve() == DEFAULT_SQLITE_PATH.resolve() or os.getenv("PMC_LOCAL_REVIEW") == "1"):
                from services.local_reviewer_catalog import local_review_catalog
                result = catalog_query(service, session_id, request.message, local_review_catalog(create_fact_store()))
                return UnifiedAgentResponse.model_validate(result)
            return UnifiedAgentResponse.model_validate(service.query(session_id, request.message))
        except (SessionNotFound, SessionConflict) as error:
            return _session_error(error)
    fact_context = get_fact_dialogue_context(request.fact_session_id)
    fact_active = bool(fact_context and request.active_context != "pmc_analysis")
    pmc_active = bool(request.workflow_id and request.active_context == "pmc_analysis")
    if fact_active or pmc_active:
        decision = route_agent_message(
            request.message,
            fact_context_active=fact_active,
            pmc_context_active=pmc_active,
        )
    else:
        decision = route_agent_message(request.message)
    if decision["intent"] == "clarification":
        return UnifiedAgentResponse(
            intent="clarification",
            status="needs_input",
            answer="请明确您要进行生产分析，还是查询某个物料的库存、批次、BOM 或在途信息。",
            structured_data={"parser_source": decision["parser_source"]},
            provenance=None,
            warnings=[],
        )

    if decision["intent"] == "fact_query":
        # 显式事实查询可继续复用独立事实会话，即使上一轮显示的是 PMC；
        # 只有省略意图的追问才依据 active_context 分流。
        result = (
            run_fact_query_agent(request.message, context=fact_context)
            if fact_context else run_fact_query_agent(request.message)
        )

        query = result["query"]
        provenance = result["provenance"] if isinstance(result["provenance"], dict) else {}
        material_code = (
            query.get("resolved_material_code")
            or query.get("resolved_parent_material_code")
            or query.get("material_code")
            or query.get("parent_material_code")
        )
        session_id = request.fact_session_id if fact_context else None
        if isinstance(material_code, str) and material_code and result["query_type"]:
            session_id = remember_fact_dialogue_context(
                session_id,
                material_code=material_code,
                query_type=result["query_type"],
                revision=query.get("revision") or provenance.get("bom_version"),
                as_of=query.get("as_of") or provenance.get("snapshot_at") or provenance.get("observed_at"),
            )
        return UnifiedAgentResponse(
            intent="fact_query",
            status=result["status"],
            answer=result["answer"],
            structured_data={
                "query_type": result["query_type"],
                "query": result["query"],
                "data": result["data"],
                "completeness": result["completeness"],
                "tool_name": result["tool_name"],
                "parser_source": result["parser_source"],
                "fact_session_id": session_id,
            },
            provenance=result["provenance"],
            warnings=result["warnings"],
        )

    if decision["intent"] == "production_capacity":
        task_context = None
        if request.active_context == "fact_query" and fact_context and fact_context.query_type == "bom":
            task_context = {"product": fact_context.material_code, "bom_version": fact_context.revision}
        elif request.workflow_id:
            try:
                state = get_pmc_workflow_state(request.workflow_id)
            except Exception:
                state = {}
            if isinstance(state.get("product"), str) and isinstance(state.get("bom_version"), str):
                task_context = {
                    "product": state["product"],
                    "bom_version": state["bom_version"],
                    "quantity": state.get("quantity"),
                }
        result = run_production_feasibility_agent(request.message, task_context=task_context)
        return UnifiedAgentResponse(
            intent="production_capacity",
            status=result["status"],
            answer=result["answer"],
            structured_data={
                "query_type": "production_capacity",
                "query": result["query"],
                "data": result["data"],
                "completeness": result["completeness"],
            },
            provenance=result["provenance"],
            warnings=result["warnings"],
        )

    # 复用既有 Chat 路由函数，保留 workflow_id 上下文、Scenario 和 Explanation。
    chat_response = chat(ChatRequest(message=request.message, workflow_id=request.workflow_id))
    if isinstance(chat_response, ChatClarificationResponse):
        response_status = "needs_input"
        warnings = ["生产任务缺少必要参数，请按提示补充"]
    else:
        assert isinstance(chat_response, ChatResponse)
        if chat_response.public_error:
            response_status = "unavailable"
        elif chat_response.missing_purchase_parameters:
            response_status = "needs_input"
        else:
            response_status = "ok"
        warnings = list(chat_response.follow_up_questions)
    return UnifiedAgentResponse(
        intent="pmc_analysis",
        status=response_status,
        answer=chat_response.agent_message,
        structured_data=chat_response.model_dump(mode="json"),
        provenance=None,
        warnings=warnings,
    )


__all__ = ["router", "unified_agent_query"]
