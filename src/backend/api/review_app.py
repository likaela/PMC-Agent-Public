"""Authenticated, product-scoped review API; never mounts development routes."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field

from api.schemas.agent import UnifiedAgentRequest
from agents.business_language import identifier_message, normalize_business_dates
from agents.fact_query_agent import _explicit_material_code
from repositories.agent_conversation_repository import SessionNotFound, SessionConflict
from services.reviewer_agent_service import (
    ReviewBinding, ENTERPRISE_PRODUCTS, DEMO_PRODUCT, DEMO_SCENARIO, public_review_payload,
)


class ReviewSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_code: str = Field(min_length=1, max_length=128)
    scenario_id: str | None = None
    analysis_mode: str | None = None


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None
    catalog_order: list[str] | None = None


def create_review_app(binding: ReviewBinding, proxy_secret: str, users: dict[str, list[str]]) -> FastAPI:
    if len(proxy_secret) < 32 or proxy_secret.startswith("REPLACE") or not users:
        raise ValueError("Private proxy secret and explicit review grants required")
    if any(not u or len(u) > 128 or not p or not set(p) <= set(binding.catalog) for u, p in users.items()):
        raise ValueError("Invalid review grants")
    app = FastAPI(title="PMC 授权远程评审", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.binding = binding
    store = binding.sessions
    with store.transaction(write_lock=True) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS review_session_owners (session_id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL, product_code TEXT NOT NULL)")
        columns = {r[1] for r in conn.execute("PRAGMA table_info(review_session_owners)")}
        if "analysis_mode" not in columns:
            conn.execute("ALTER TABLE review_session_owners ADD COLUMN analysis_mode TEXT NOT NULL DEFAULT 'full'")
        if "dataset_version" not in columns:
            conn.execute("ALTER TABLE review_session_owners ADD COLUMN dataset_version TEXT")
    lock, slots, rates = threading.Lock(), threading.BoundedSemaphore(8), {}

    def error(status, code, message):
        return JSONResponse(status_code=status, content={"code": code, "user_message": message})

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        key = request.headers.get("x-pmc-proxy-key", "")
        user = request.headers.get("x-pmc-user", "")
        if not hmac.compare_digest(key, proxy_secret) or user not in users:
            return error(401, "ACCESS_REQUIRED", "请使用分配的评审账号登录。")
        if request.method != "GET":
            origin, host = request.headers.get("origin"), request.headers.get("host", "")
            if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin not in {"http://"+host, "https://"+host}):
                return error(403, "ORIGIN_REJECTED", "请在评审页面内发送请求。")
        owner = hmac.new(proxy_secret.encode(), user.encode(), hashlib.sha256).hexdigest()
        request.state.owner, request.state.products = owner, tuple(users[user])
        with lock:
            now = time.monotonic()
            for identity in list(rates):
                rates[identity] = [t for t in rates[identity] if now-t < 60]
                if not rates[identity]:
                    del rates[identity]
            recent = rates.setdefault(owner, [])
            if len(recent) >= 60:
                return error(429, "RATE_LIMITED", "请求较频繁，请稍后重试。")
            recent.append(now)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def permit(request, product):
        if product not in request.state.products:
            raise HTTPException(404)

    def owner_product(request, sid):
        with store.transaction() as conn:
            row = conn.execute("SELECT owner_hash, product_code, analysis_mode, dataset_version FROM review_session_owners WHERE session_id=?", (sid,)).fetchone()
        if not row or not hmac.compare_digest(row[0], request.state.owner):
            raise HTTPException(404)
        permit(request, row[1])  # Revalidate grants even for historical sessions.
        mode = binding.mode(row[1], row[2])
        if mode is None or mode["dataset_version"] != row[3]:
            raise HTTPException(404)
        return row[1], row[2]

    def create(request, product, analysis_mode=None):
        permit(request, product)
        mode = binding.mode(product, analysis_mode)
        if mode is None:
            raise HTTPException(422)
        service = binding.service(product, mode["mode"])
        if product == DEMO_PRODUCT or mode["mode"] in ("slim", "current"):
            session = service.repository.create_session()
        else:
            task = dict(material_code=product, revision=binding.catalog[product]["revision"], quantity=None)
            session = service.repository.create_session(initial_context=dict(active_domain="fact",
                fact={**task, "query_type": "bom"}, production={**task, "query_type": "production_capacity"}))
        with store.transaction(write_lock=True) as conn:
            conn.execute("INSERT INTO review_session_owners (session_id, owner_hash, product_code, analysis_mode, dataset_version) VALUES (?, ?, ?, ?, ?)",
                (session["session_id"], request.state.owner, product, mode["mode"], mode["dataset_version"]))
        session = service.public_session(session) if hasattr(service, "public_session") else session
        return {**public_review_payload(session), "review_product": product, "review_mode": mode["mode"],
            "dataset_version": mode["dataset_version"], "scenario_id": mode["scenario_id"]}

    def scenario(product, analysis_mode, value):
        expected = binding.mode(product, analysis_mode)["scenario_id"]
        if value not in (None, expected):
            raise HTTPException(422)

    def with_binding(result, product, analysis_mode):
        mode = binding.mode(product, analysis_mode)
        result = public_review_payload(result)
        context = dict(product_code=product, analysis_mode=analysis_mode, scenario_id=mode["scenario_id"],
            dataset_version=mode["dataset_version"], revision=binding.catalog[product]["revision"])
        if "structured_data" in result:
            result.setdefault("structured_data", {})["product_context"] = context
        for turn in result.get("turns", []):
            turn["response"].setdefault("structured_data", {})["product_context"] = context
        return {**result, "review_product": product, "review_mode": analysis_mode,
            "dataset_version": mode["dataset_version"], "scenario_id": mode["scenario_id"]}

    def explicit_product(request, message):
        from agents.product_resolver import resolve_product
        result = resolve_product(message, [binding.catalog[p] for p in request.state.products])
        if result["status"] != "ok":
            raise HTTPException(422)
        return result["product_code"] if result.get("explicit") else None

    @app.exception_handler(HTTPException)
    @app.exception_handler(SessionNotFound)
    async def unavailable(request, exc):
        status = getattr(exc, "status_code", 404)
        return error(status, "REVIEW_UNAVAILABLE", "当前对象不可访问，请选择已授权产品或新建分析。")

    @app.exception_handler(SessionConflict)
    async def conflict(request, exc):
        return error(409, "SESSION_CONFLICT", "会话已有新消息，请恢复后重试。")

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return error(422, "INVALID_REQUEST", "请输入有效问题和已授权产品。")

    @app.exception_handler(Exception)
    async def failure(request, exc):
        # No query/evidence/path/exception body is exposed or logged here.
        return error(500, "REVIEW_FAILED", "分析服务暂时异常，请稍后重试。")

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "authorized_review", "model_egress": "disabled"}

    @app.get("/api/v1/review/catalog")
    def catalog(request: Request):
        return {"products": [binding.catalog[p] for p in request.state.products]}

    @app.get("/api/v1/review/products/{product}/materials")
    def materials(request: Request, product: str, analysis_mode: str | None = None):
        permit(request, product)
        mode = binding.mode(product, analysis_mode)
        if mode is None:
            raise HTTPException(422)
        return {"product_code": product, "analysis_mode": mode["mode"], "dataset_version": mode["dataset_version"],
            "materials": binding.materials(product, mode["mode"])}

    @app.post("/api/v1/review/resolve")
    def resolve(request: Request, body: ResolveRequest):
        from agents.product_resolver import resolve_product
        current = owner_product(request, body.session_id)[0] if body.session_id else None
        return resolve_product(body.message, [binding.catalog[p] for p in request.state.products],
            current_product=current, catalog_order=body.catalog_order)

    @app.post("/api/v1/agent/sessions")
    def sessions(request: Request, body: ReviewSessionCreate):
        permit(request, body.product_code)
        mode = binding.mode(body.product_code, body.analysis_mode)
        if mode is None:
            raise HTTPException(422)
        scenario(body.product_code, mode["mode"], body.scenario_id)
        return create(request, body.product_code, mode["mode"])

    @app.get("/api/v1/agent/sessions/{sid}")
    def history(request: Request, sid: str, after: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), scenario_id: str | None = None):
        product, analysis_mode = owner_product(request, sid)
        scenario(product, analysis_mode, scenario_id)
        service = binding.service(product, analysis_mode)
        result = service.repository.history(sid, after=after, limit=limit)
        if hasattr(service, "public_session"):
            result = service.public_session(result)
        mode = binding.mode(product, analysis_mode)
        return with_binding(result, product, analysis_mode)

    @app.post("/api/v1/agent/query")
    def query(request: Request, body: UnifiedAgentRequest):
        if len(body.message) > 2000 or body.workflow_id or body.fact_session_id or body.active_context or re.search(r"\.\.[\\/]|[A-Za-z]:[\\/]|data[\\/](?:raw|facts|normalized)", body.message):
            return error(422, "INVALID_REQUEST", "仅接受已授权产品的业务查询。")
        sid = body.session_id
        from agents.reviewer_catalog_queries import catalog_query, catalog_kind
        product, analysis_mode = owner_product(request, sid) if sid else (explicit_product(request, body.message), None)
        if product is None and catalog_kind(body.message):
            product = request.state.products[0]
        if product is None:
            return error(422, "PRODUCT_REQUIRED", "请先选择产品。")
        analysis_mode = analysis_mode or binding.mode(product)["mode"]
        scenario(product, analysis_mode, body.scenario_id)
        from agents.product_resolver import resolve_product, product_overview_requested
        resolution = resolve_product(body.message, [binding.catalog[p] for p in request.state.products],
            current_product=product, catalog_order=body.catalog_order)
        if resolution["status"] != "ok":
            return error(422, "PRODUCT_REQUIRED", resolution["answer"])
        explicit = resolution["product_code"] if resolution.get("explicit") else None
        if explicit and explicit != product:
            return error(409, "PRODUCT_SWITCH_REQUIRED", "请切换产品并新建分析，原会话仍保留。")
        if re.search(r"完整|全部66项|使用完整企业", body.message) and analysis_mode == "slim" and not catalog_kind(body.message):
            return error(409, "MODE_SWITCH_REQUIRED", "请切换到完整追溯范围并新建分析，当前会话仍保留。")
        execution_message = resolution.get("message", body.message)
        code_message = re.sub(r"\b\d{4}-\d{1,2}-\d{1,2}\b", "", normalize_business_dates(execution_message))
        code_message = re.sub(r"(?:\d+(?:\.\d+)?|[零〇一二三四五六七八九十百千万两]+)\s*(?:台|片|个|件|套)", "", code_message)
        code = _explicit_material_code(identifier_message(code_message))
        if code and code != product and not catalog_kind(body.message):
            if product == DEMO_PRODUCT:
                allowed = {p["material_code"] for p in binding.materials(product)}
                if code not in allowed:
                    raise HTTPException(404)
            elif analysis_mode in ("slim", "current"):
                if code not in {r["material_code"] for r in binding.materials(product, analysis_mode)}:
                    raise HTTPException(404)
            elif binding.facts[product].repository.resolve_item(code) is None:
                raise HTTPException(404)
        if not sid:
            sid = create(request, product, analysis_mode)["session_id"]
        if not slots.acquire(blocking=False):
            return error(429, "REVIEW_BUSY", "当前体验人数较多，请稍后重试。")
        try:
            service = binding.service(product, analysis_mode)
            result = catalog_query(service, sid, body.message, [binding.catalog[p] for p in request.state.products]) if resolution.get("catalog_query") else None
            if result is None:
                if product_overview_requested(execution_message, product):
                    from agents.reviewer_catalog_queries import overview_query
                    result = overview_query(service, sid, body.message, binding.catalog[product], binding.mode(product, analysis_mode))
                else:
                    result = service.query(sid, body.message, execution_message=execution_message)
            mode = binding.mode(product, analysis_mode)
            return with_binding(result, product, analysis_mode)
        finally:
            slots.release()
    return app


def configured_app():
    import json
    root = Path(__file__).resolve().parents[3]
    if os.getenv("PMC_REVIEW_MODE") != "1":
        raise RuntimeError("Use the dedicated review launcher")
    settings = json.loads(Path(os.environ["PMC_REVIEW_SETTINGS"]).read_text(encoding="utf-8"))
    required = {"facts_path", "inventory_path", "units_path", "quantity_path", "users"}
    if set(settings) - {"slim_dataset_path", "business_manifest_path"} != required:
        raise ValueError("Explicit private review settings required")
    if not settings.get("business_manifest_path") and not settings.get("slim_dataset_path"):
        raise ValueError("Explicit approved default filtered dataset required")
    binding = ReviewBinding(**{k: root / settings[k] for k in required - {"users"}},
        slim_dataset_path=root / settings["slim_dataset_path"] if settings.get("slim_dataset_path") else None,
        business_manifest_path=root / settings["business_manifest_path"] if settings.get("business_manifest_path") else None,
        runtime_root=root / ".runtime/authorized-review")
    return create_review_app(binding, os.environ["PMC_REVIEW_PROXY_SECRET"], settings["users"])
