"""Deployment-only entry: the existing Agent API over verified public inputs.

Development Excel/LangGraph HTTP routes are intentionally not mounted here.
Their original app remains unchanged for development and regression tests.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes.agent import router
from repositories.public_data_repository import public_root, release_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    if not public_root():
        raise RuntimeError("Start production with python -m scripts.run_public")
    release_settings()
    logger.info("Verified public business inputs; no private data fallback")
    yield


app = FastAPI(title="PMC-Agent", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


@app.middleware("http")
async def public_boundary(request: Request, call_next):
    # All request bodies still use the existing API schema. Reject legacy data
    # spaces at the deployment edge, before any scenario factory can load them.
    scenario = request.query_params.get("scenario_id")
    if request.method == "POST" and request.url.path.startswith("/api/v1/agent/"):
        try:
            payload = await request.json()
            if isinstance(payload, dict):
                scenario = payload.get("scenario_id", scenario)
        except ValueError:
            pass  # Let the existing schema produce the normal validation error.
    if scenario not in (None, "demo_scenario_50_vs_100"):
        return JSONResponse(status_code=422, content={"code": "INVALID_REQUEST", "user_message": "请选择当前可查询的产品。"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    release_settings()
    return {"status": "ok", "data_source": "public"}


@app.exception_handler(HTTPException)
async def request_error(request, error):
    return JSONResponse(status_code=error.status_code,
                        content={"code": "REQUEST_FAILED", "user_message": "请求无法完成，请检查输入后重试。"})


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    return JSONResponse(status_code=422, content={"code": "INVALID_REQUEST", "user_message": "请输入有效的查询条件。"})


@app.exception_handler(Exception)
async def internal_error(request, error):
    logger.exception("Public deployment request failed")
    return JSONResponse(status_code=500, content={"code": "ANALYSIS_UNAVAILABLE", "user_message": "查询暂时失败，请稍后重试。"})


app.include_router(router)
