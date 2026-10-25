"""Optional isolated Demo operations. Public requests have no write principal."""
from __future__ import annotations

import logging
import copy
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from repositories.agent_conversation_repository import SessionConflict, SessionNotFound
from services.safe_inventory_runtime import (InventoryPrincipal, demand_fingerprint,
    preview_issue, runtime, session_id_of)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/inventory", tags=["Inventory operations"])


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1, max_length=64)
    product: str = Field(min_length=1, max_length=128)
    quantity: int = Field(gt=0)
    scenario_id: Literal["demo_scenario_50_vs_100"] | None = None


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1, max_length=64)
    preview_id: str = Field(min_length=1, max_length=64)
    confirmed: bool
    scenario_id: Literal["demo_scenario_50_vs_100"] | None = None


def _denied():
    return JSONResponse(status_code=403, content={"code": "INVENTORY_ACCESS_DENIED", "user_message": "当前无权执行或读取此库存操作。"})


def _principal(request, session_id):
    # Only server middleware can set this typed value. HTTP headers are not used.
    value = getattr(request.state, "inventory_principal", None)
    return value if isinstance(value, InventoryPrincipal) and value.owns(session_id) else None


def _active(request):
    return getattr(request.app.state, "inventory_runtime", None) or runtime()


def _session(request, session_id, scenario_id):
    loader = getattr(request.app.state, "inventory_session_loader", None)
    if loader is not None:
        return loader(session_id, scenario_id)
    from api.routes.agent import _current_service, _persistent_service
    service = (_current_service(session_id=session_id) if scenario_id is None else None) or _persistent_service(scenario_id)
    return service, service.repository.get_session(session_id)


def _response(result, session_id, operation, preview_id=None):
    data = copy.deepcopy(result.get("data"))
    if data is None and preview_id and result["status"] in {
            "stale_preview", "preview_expired", "confirmation_required", "task_conflict", "data_unavailable"}:
        data = {"preview_id": preview_id, "executed": False}
    if isinstance(data, dict):
        data["session_id"] = session_id
    succeeded = result["status"] == "ok" and isinstance(data, dict) and data.get("executed") is True
    synthetic = isinstance(data, dict) and (data.get("namespace") == "simulation" or data.get("synthetic") is True)
    answer = ("本次库存扣减已完成，明细见执行回执。" + ("（合成账本，非企业实时库存）" if synthetic else "")
              if succeeded else
              "已生成领料预览，尚未扣减库存。" if operation == "inventory_preview" and data else
              "；".join(result.get("warnings") or ["本次未执行库存扣减。"]))
    return {"intent": "production_capacity", "status": result["status"], "answer": answer,
            "session_id": session_id,
            "structured_data": {"operation": operation, "data": data, "completed_steps": []},
            "provenance": result.get("provenance"), "warnings": result.get("warnings", [])}


def _save_execution_history(service, session, result):
    """A durable ledger receipt survives history failure; a retry only repairs history.

    These are two separate stores, not one distributed transaction. The audit turn
    is inserted through an atomic per-task dedupe inside the conversation write
    transaction, so concurrent confirmations cannot create two successful turns
    for the same task_id; a lost HTTP response only repairs missing history.
    """
    sid = session_id_of(session)
    repository = service.repository
    task_id = result["data"]["task_id"]
    preview_id = result["data"]["preview_id"]
    for attempt in range(3):
        current = repository.get_session(sid)
        context = copy.deepcopy(current.get("context") or {})
        if (context.get("inventory_preview") or {}).get("preview_id") == preview_id:
            context.pop("inventory_preview", None)
        context["last_inventory_execution"] = {key: result["data"][key] for key in
            ("task_id", "preview_id", "product", "requested_quantity", "stock_version")}
        for key in ("book_analysis", "slim_analysis"):
            context.pop(key, None)
        response = _response(result, sid, "inventory_execution")
        response["conversation_id"] = current.get("conversation_id")
        evidence = {**result, "query": {"preview_id": preview_id}, "completeness": {"executed": True}}
        try:
            return repository.append_execution_turn(current, "确认执行库存扣减", response, context,
                [{"tool_name": "inventory_issue_execute", "query": evidence["query"], "result": evidence}],
                task_id=task_id)
        except SessionConflict:
            # Another writer advanced the session: re-read, then the atomic dedupe
            # decides whether this is a repair or an already-saved audit turn.
            if attempt == 2:
                raise
    raise SessionConflict("会话历史写入冲突，请查询执行回执后重试")


@router.post("/preview")
def preview_inventory(body: PreviewRequest, request: Request):
    principal = _principal(request, body.session_id)
    if principal is None:
        return _denied()
    try:
        service, session = _session(request, body.session_id, body.scenario_id)
        if session_id_of(session) != body.session_id:
            return _denied()
        bound = (session.get("context", {}).get("product_context") or {}).get("product_code")
        if bound and bound != body.product:
            return _denied()
        result = preview_issue(service, session, body.product, body.quantity, principal=principal,
                               inventory_runtime=_active(request))
        return _response(result, body.session_id, "inventory_preview")
    except SessionNotFound:
        return _denied()
    except Exception:
        logger.exception("Inventory preview unavailable")
        return JSONResponse(status_code=503, content={"code": "INVENTORY_UNAVAILABLE", "user_message": "库存预览暂不可用；未执行扣减。"})


@router.post("/execute")
def execute_inventory(body: ExecuteRequest, request: Request):
    active = _active(request)
    principal = _principal(request, body.session_id)
    # 路由层只做粗粒度门禁；具体预览所属命名空间由 execute() 在事务内再次校验。
    if principal is None or not principal.can_execute_demo:
        return _denied()
    if not (set(active.allowed_namespaces) & set(principal.allowed_namespaces)):
        return _denied()
    try:
        service, session = _session(request, body.session_id, body.scenario_id)
        if session_id_of(session) != body.session_id:
            return _denied()
        if not active.allow_demo_execution:
            return _denied()
        result = active.execution().execute(body.preview_id, principal.owner_id, body.session_id,
            confirmed=body.confirmed, current_demand_fingerprint=demand_fingerprint(session),
            demand_validator=lambda: demand_fingerprint(_session(request, body.session_id, body.scenario_id)[1]))
        if result["status"] == "forbidden":
            return _denied()
        if result["status"] == "ok" and (result.get("data") or {}).get("executed") is True:
            try:
                return _save_execution_history(service, session, result)
            except Exception:
                logger.exception("Committed inventory receipt awaits conversation synchronization")
                return JSONResponse(status_code=503, content={"code": "INVENTORY_RECEIPT_PENDING", "user_message": "库存操作已记录，历史同步未完成；请查询回执并重试同一预览，不会重复扣减。"})
        return _response(result, body.session_id, "inventory_execution", body.preview_id)
    except SessionNotFound:
        return _denied()
    except Exception:
        logger.exception("Inventory execution failed; use durable receipt before retrying")
        return JSONResponse(status_code=503, content={"code": "INVENTORY_UNAVAILABLE", "user_message": "操作未能完成，请查询本次预览回执后重试。"})


@router.get("/receipts/{preview_id}")
def inventory_receipt(preview_id: str, session_id: str, request: Request):
    principal = _principal(request, session_id)
    if principal is None:
        return _denied()
    try:
        result = _active(request).execution().receipt(preview_id, principal.owner_id, session_id)
        if result["status"] == "forbidden":
            return _denied()
        return _response(result, session_id, "inventory_execution", preview_id)
    except Exception:
        logger.exception("Inventory receipt unavailable")
        return JSONResponse(status_code=503, content={"code": "INVENTORY_UNAVAILABLE", "user_message": "执行回执暂不可用。"})
