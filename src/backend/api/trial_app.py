"""Fail-closed remote trial entrypoint: synthetic facts, proxy identity, no LLM.

Run ONLY on loopback behind the supplied authenticated Caddy configuration.
Never mounts the enterprise, legacy Chat, workflow or upload routes.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.schemas.agent import AgentSessionCreate, UnifiedAgentRequest, UnifiedAgentResponse
from repositories.agent_conversation_repository import SessionConflict, SessionNotFound
from repositories.demo_scenario_repository import SCENARIO_ID
from services.demo_scenario_agent_service import build_demo_scenario_agent


def create_trial_app(runtime_root: Path, proxy_secret: str) -> FastAPI:
    if os.getenv("PMC_TRIAL_MODE") != "1" or len(proxy_secret) < 32 or proxy_secret.startswith("REPLACE"):
        raise RuntimeError("Trial mode and a private proxy secret of at least 32 characters are required")
    service = build_demo_scenario_agent(runtime_root=runtime_root)
    service.enable_orchestrator()
    service.intent_presentation = True
    repo = service.repository
    with repo.store.transaction(write_lock=True) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS trial_session_owners (session_id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL)")
    app = FastAPI(title="PMC 合成数据受控试用", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    lock, slots, rates = threading.Lock(), threading.BoundedSemaphore(8), {}

    def error(status, code, message):
        return JSONResponse(status_code=status, content={"code": code, "user_message": message})

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        key, user = request.headers.get("x-pmc-proxy-key", ""), request.headers.get("x-pmc-user", "")
        if not hmac.compare_digest(key, proxy_secret) or not user or len(user) > 128:
            return error(401, "ACCESS_REQUIRED", "请使用分配的试用账号登录。")
        if request.method == "POST":
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin not in {"https://" + host, "http://" + host}):
                return error(403, "ORIGIN_REJECTED", "请在试用页面内发送请求。")
        owner = hmac.new(proxy_secret.encode(), user.encode(), hashlib.sha256).hexdigest()
        request.state.owner = owner
        with lock:
            now = time.monotonic()
            for identity in list(rates):
                rates[identity] = [t for t in rates[identity] if now-t < 60]
                if not rates[identity]:
                    del rates[identity]
            recent = rates.setdefault(owner, [])
            if len(recent) >= 60:
                return error(429, "RATE_LIMITED", "请求较频繁，请稍后再试。")
            recent.append(now)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def own(session_id, owner):
        with repo.store.transaction() as connection:
            row = connection.execute("SELECT owner_hash FROM trial_session_owners WHERE session_id=?", (session_id,)).fetchone()
        if row is None or not hmac.compare_digest(row[0], owner):
            raise HTTPException(404)

    def create(owner):
        session = repo.create_session()
        with repo.store.transaction(write_lock=True) as connection:
            connection.execute("INSERT INTO trial_session_owners VALUES (?, ?)", (session["session_id"], owner))
        return {**session, "scenario_id": SCENARIO_ID}

    @app.exception_handler(HTTPException)
    @app.exception_handler(SessionNotFound)
    async def missing(request, exc):
        return error(404, "SESSION_NOT_FOUND", "当前会话不存在或不属于您的账号，请新建分析。")

    @app.exception_handler(SessionConflict)
    async def conflict(request, exc):
        return error(409, "SESSION_CONFLICT", "当前会话已有新消息，请恢复会话后重试。")

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return error(422, "INVALID_REQUEST", "请输入有效的合成演示问题。")

    @app.exception_handler(Exception)
    async def failure(request, exc):
        # Do not log raw queries, facts, exception bodies or tokens.
        return error(500, "TRIAL_UNAVAILABLE", "试用服务暂时异常，请稍后重试。")

    @app.get("/health")
    def health():
        return {"status": "ok", "synthetic": True, "model_egress": "disabled"}

    @app.post("/api/v1/agent/sessions")
    def sessions(request: Request, body: AgentSessionCreate | None = None):
        if body and body.scenario_id not in {None, SCENARIO_ID}:
            return error(422, "INVALID_SCENARIO", "远程试用仅支持独立合成演示数据。")
        return create(request.state.owner)

    @app.get("/api/v1/agent/sessions/{session_id}")
    def history(request: Request, session_id: str, after: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), scenario_id: str | None = None):
        if scenario_id not in {None, SCENARIO_ID}:
            raise HTTPException(404)
        own(session_id, request.state.owner)
        return repo.history(session_id, after=after, limit=limit)

    @app.post("/api/v1/agent/query", response_model=UnifiedAgentResponse)
    def query(request: Request, body: UnifiedAgentRequest):
        if body.scenario_id not in {None, SCENARIO_ID}:
            return error(422, "INVALID_SCENARIO", "远程试用仅支持独立合成演示数据。")
        if len(body.message) > 2000 or body.workflow_id or body.fact_session_id or body.active_context:
            return error(422, "INVALID_REQUEST", "仅支持合成演示会话，问题请控制在2000字内。")
        if not slots.acquire(blocking=False):
            return error(429, "TRIAL_BUSY", "当前体验人数较多，请稍后重试。")
        try:
            session_id = body.session_id or create(request.state.owner)["session_id"]
            own(session_id, request.state.owner)
            return service.query(session_id, body.message)
        finally:
            slots.release()

    return app


def configured_app():
    # Never use the enterprise URL or the local Demo runtime as the trial store.
    root = Path(__file__).resolve().parents[3] / ".runtime" / "remote-trial"
    return create_trial_app(root, os.getenv("PMC_TRIAL_PROXY_SECRET", ""))
