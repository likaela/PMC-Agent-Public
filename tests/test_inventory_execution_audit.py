"""库存执行审计幂等与会话隔离回归（合成数据 + 项目外临时数据库）。

覆盖：预览不扣减、确认后准确扣减、重复/并发确认只扣一次且只有一条成功审计、
事务回滚、执行结果丢失后按原回执恢复、服务重建后状态恢复、跨会话隔离、
未授权执行仍受保护。
"""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/backend"))

from api.routes.inventory import router
from repositories.agent_conversation_repository import AgentConversationRepository
from repositories.fact_store import SQLiteFactStore
from services.book_inventory_service import BookInventoryService
from services.safe_inventory_runtime import InventoryPrincipal, SafeInventoryRuntime

PRODUCT = "DEMO-PMC-01"


def lot(code, batch, qty, received="2024-01-01T00:00:00+00:00"):
    return {"material_code": code, "batch_no": batch, "location": batch, "site": "TEST",
            "name": code, "inventory_unit": "片", "recorded_stock_qty": qty,
            "on_hand_qty": qty, "available_qty": qty, "reserved_qty": None,
            "received_at": received, "last_movement_at": received, "expiry_date": None,
            "quality_status": "normal", "duplicate_observation": False,
            "provenance": {"source_file": "synthetic.json", "row_number": 1}}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    monkeypatch.setenv("PMC_RUNTIME_ROOT", str(root))
    monkeypatch.delenv("PMC_DEMO_INVENTORY_WRITE_ENABLED", raising=False)
    source = {"source_file": "synthetic.json", "records": {
        "TEST-A": [lot("TEST-A", "OLD", "4"), lot("TEST-A", "NEW", "6", "2024-02-01T00:00:00+00:00")],
        "TEST-B": [lot("TEST-B", "B", "5")]}, "quantity_confirmation": None}
    active = SafeInventoryRuntime(root, source_provider=lambda namespace: copy.deepcopy(source),
                                  allow_demo_execution=True)
    active.ensure("demo")
    return root, source, active


def facts_service(active):
    class Facts:
        def __init__(self):
            self.repository = self

        def resolve_item(self, code):
            return {"material_code": code} if code in {PRODUCT, "TEST-A", "TEST-B"} else None

        def list_published_bom_versions(self, code):
            return [{"version": "V01"}] if code == PRODUCT else []

        def query_bom(self, code, revision):
            return {"status": "ok", "data": {
                "parent_item": {"material_code": PRODUCT, "unit": "片"},
                "lines": [{"child_item": {"material_code": code, "name": code}, "qty_per": qty,
                           "unit": "片", "provenance": {"source_file": "synthetic-bom.json"}}
                          for code, qty in (("TEST-A", "2"), ("TEST-B", "1"))]},
                "provenance": {"source_file": "synthetic-bom.json"}}
    observations = SimpleNamespace(load=lambda: active.ledger.load_observations("demo"))
    return SimpleNamespace(book_service=BookInventoryService(Facts(), observations))


def session(sid="session-A", quantity=3, status="valid"):
    return {"session_id": sid, "context": {"product_context": {"product_code": PRODUCT},
        "current_demand": {"generation": 1, "status": status, "product": PRODUCT,
                           "quantity": quantity, "unit": "片", "unit_source": "explicit"}}}


class Client:
    def __init__(self, active, service, sessions, principal=None):
        if not hasattr(service, "repository"):
            store = SQLiteFactStore(active.root / "synthetic-conversations.sqlite3")
            store.migrate()
            service.repository = AgentConversationRepository(store)
            for sid, value in sessions.items():
                with patch("repositories.agent_conversation_repository.uuid4",
                           side_effect=[sid, "conversation-" + sid]):
                    sessions[sid] = service.repository.create_session(initial_context=value["context"])
        app = FastAPI()
        app.include_router(router)
        app.state.inventory_runtime = active
        app.state.inventory_session_loader = lambda sid, scenario: (service, sessions[sid])
        if principal is not None:
            @app.middleware("http")
            async def trusted(request, call_next):
                request.state.inventory_principal = principal
                return await call_next(request)
        self.http = TestClient(app)
        self.active, self.service, self.sessions = active, service, sessions

    def preview(self, sid="session-A", quantity=3):
        return self.http.post("/api/v1/inventory/preview",
                              json={"session_id": sid, "product": PRODUCT, "quantity": quantity})

    def execute(self, pid, sid="session-A"):
        return self.http.post("/api/v1/inventory/execute",
                              json={"session_id": sid, "preview_id": pid, "confirmed": True})


def balance(active):
    return tuple(active.ledger.stock("demo", code)["on_hand_qty"] for code in ("TEST-A", "TEST-B"))


def turns(service, sid="session-A"):
    return service.repository.history(sid)["turns"]


def execution_turns(service, sid="session-A"):
    return [t for t in turns(service, sid)
            if t["response"]["structured_data"]["operation"] == "inventory_execution"]


def test_preview_does_not_deduct_and_confirmation_deducts_once(runtime):
    _, source, active = runtime
    sessions = {"session-A": session()}
    client = Client(active, facts_service(active), sessions,
                    InventoryPrincipal("owner-A", frozenset({"session-A"}), can_execute_demo=True))
    plan = client.preview().json()["structured_data"]["data"]
    assert plan["executed"] is False and balance(active) == ("10", "5")
    assert active.ledger.movements("demo") == [] and turns(client.service) == []
    first = client.execute(plan["preview_id"])
    assert first.status_code == 200
    data = first.json()["structured_data"]["data"]
    assert data["executed"] is True and balance(active) == ("4", "2")
    repeat = client.execute(plan["preview_id"])
    assert repeat.status_code == 200 and repeat.json()["structured_data"]["data"] == data
    assert balance(active) == ("4", "2")
    assert len(execution_turns(client.service)) == 1


def test_concurrent_confirmations_keep_one_audit_turn(runtime):
    _, source, active = runtime
    sessions = {"session-A": session()}
    client = Client(active, facts_service(active), sessions,
                    InventoryPrincipal("owner-A", frozenset({"session-A"}), can_execute_demo=True))
    pid = client.preview().json()["structured_data"]["data"]["preview_id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.execute(pid), range(4)))
    assert {r.status_code for r in responses} == {200}
    bodies = [r.json()["structured_data"]["data"] for r in responses]
    assert {body["task_id"] for body in bodies} == {pid}
    # 库存只扣一次，审计只写一条。
    assert balance(active) == ("4", "2")
    assert len(active.ledger.movements("demo")) == 3
    audit = execution_turns(client.service)
    assert len(audit) == 1
    assert {r.json()["turn_id"] for r in responses} == {audit[0]["turn_id"]}
    stored = audit[0]["response"]["structured_data"]["data"]
    assert stored["task_id"] == pid and stored["executed"] is True
    # 任务标识唯一：账本内不存在第二条执行任务。
    with active.ledger.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ledger_executions").fetchone()[0] == 1


def test_concurrent_confirmations_with_delayed_session_read_write_one_turn(runtime, monkeypatch):
    """确定性并发：两个确认在写事务前都已通过“查重”，仍只能产生一条成功审计。

    旧实现（查重后再追加、非原子）在本用例下会写出两条同 task_id 的成功记录，
    因此本用例能区分修复前后，而不是仅凭时序偶然通过。
    """
    _, source, active = runtime
    sessions = {"session-A": session()}
    client = Client(active, facts_service(active), sessions,
                    InventoryPrincipal("owner-A", frozenset({"session-A"}), can_execute_demo=True))
    pid = client.preview().json()["structured_data"]["data"]["preview_id"]
    original = client.service.repository.get_session

    def slow(session_id):
        time.sleep(0.05)
        return original(session_id)

    monkeypatch.setattr(client.service.repository, "get_session", slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.execute(pid), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    audit = execution_turns(client.service)
    assert len(audit) == 1
    assert {r.json()["turn_id"] for r in responses} == {audit[0]["turn_id"]}
    assert balance(active) == ("4", "2")
    assert len(active.ledger.movements("demo")) == 3


def test_history_failure_keeps_receipt_and_repair_stays_single(runtime, monkeypatch):
    _, source, active = runtime
    sessions = {"session-A": session()}
    client = Client(active, facts_service(active), sessions,
                    InventoryPrincipal("owner-A", frozenset({"session-A"}), can_execute_demo=True))
    pid = client.preview().json()["structured_data"]["data"]["preview_id"]
    append = client.service.repository.append_turn

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic history fault")

    monkeypatch.setattr(client.service.repository, "append_turn", broken)
    failed = client.execute(pid)
    assert failed.status_code == 503
    assert failed.json()["code"] == "INVENTORY_RECEIPT_PENDING"
    assert balance(active) == ("4", "2") and turns(client.service) == []
    # 执行结果“丢失”后通过原执行标识查询回执，不重新扣减。
    receipt = client.http.get(f"/api/v1/inventory/receipts/{pid}?session_id=session-A").json()
    assert receipt["structured_data"]["data"]["executed"] is True
    monkeypatch.setattr(client.service.repository, "append_turn", append)
    repaired = client.execute(pid)
    assert repaired.status_code == 200 and balance(active) == ("4", "2")
    assert len(execution_turns(client.service)) == 1
    assert client.execute(pid).json() == repaired.json()
    assert len(execution_turns(client.service)) == 1


def test_state_survives_runtime_rebuild_without_second_deduction(runtime):
    root, source, active = runtime
    sessions = {"session-A": session()}
    client = Client(active, facts_service(active), sessions,
                    InventoryPrincipal("owner-A", frozenset({"session-A"}), can_execute_demo=True))
    pid = client.preview().json()["structured_data"]["data"]["preview_id"]
    assert client.execute(pid).json()["structured_data"]["data"]["executed"] is True
    # 重建运行服务（等价于进程重启后重新连接同一账本）。
    rebuilt = SafeInventoryRuntime(root, source_provider=lambda namespace: copy.deepcopy(source),
                                   allow_demo_execution=True)
    rebuilt.ensure("demo")
    again = Client(rebuilt, client.service, sessions,
                   InventoryPrincipal("owner-A", frozenset({"session-A"}), can_execute_demo=True))
    restored = again.http.get(f"/api/v1/inventory/receipts/{pid}?session_id=session-A").json()
    assert restored["structured_data"]["data"]["executed"] is True
    assert balance(rebuilt) == ("4", "2")
    assert len(execution_turns(client.service)) == 1
    assert again.execute(pid).json()["structured_data"]["data"]["executed"] is True
    assert balance(rebuilt) == ("4", "2")


def test_unknown_state_in_one_session_does_not_block_another(runtime):
    _, source, active = runtime
    sessions = {"session-A": session(), "session-B": session("session-B", quantity=1)}
    service = facts_service(active)
    client_a = Client(active, service, sessions,
                      InventoryPrincipal("owner-A", frozenset({"session-A"}), can_execute_demo=True))
    # A 的执行结果未知（历史写入失败），不得影响 B 的正常查询与新建任务。
    pid_a = client_a.preview().json()["structured_data"]["data"]["preview_id"]
    append = service.repository.append_turn

    with patch.object(service.repository, "append_turn", side_effect=RuntimeError("lost")):
        assert client_a.execute(pid_a).status_code == 503
    assert turns(service) == [] and turns(service, "session-B") == []

    client_b = Client(active, service, sessions,
                      InventoryPrincipal("owner-B", frozenset({"session-B"}), can_execute_demo=True))
    plan_b = client_b.preview("session-B", quantity=1)
    assert plan_b.status_code == 200
    pid_b = plan_b.json()["structured_data"]["data"]["preview_id"]
    result_b = client_b.execute(pid_b, "session-B")
    assert result_b.status_code == 200
    assert result_b.json()["structured_data"]["data"]["executed"] is True
    # A 仍可通过原执行标识查询回执；不会因为 B 的执行被覆盖或重复扣减。
    receipt_a = client_a.http.get(f"/api/v1/inventory/receipts/{pid_a}?session_id=session-A").json()
    assert receipt_a["structured_data"]["data"]["executed"] is True
    with patch.object(service.repository, "append_turn", append):
        repaired = client_a.execute(pid_a)
    assert repaired.status_code == 200
    assert len(execution_turns(service, "session-A")) == 1
    assert len(execution_turns(service, "session-B")) == 1
    # A 扣 TEST-A 6（4+2）与 TEST-B 3；B 扣 TEST-A 2 与 TEST-B 1：无重复扣减。
    assert balance(active) == ("2", "1")


@pytest.mark.parametrize("case", ["no-principal", "foreign-session"])
def test_unauthorized_execution_stays_protected(runtime, case):
    _, source, active = runtime
    sessions = {"session-A": session(), "session-B": session("session-B")}
    service = facts_service(active)
    authorized = Client(active, service, sessions,
                        InventoryPrincipal("owner-A", frozenset({"session-A"}), can_execute_demo=True))
    pid = authorized.preview().json()["structured_data"]["data"]["preview_id"]
    if case == "no-principal":
        anonymous = Client(active, service, sessions)
        assert anonymous.preview().status_code == 403
        assert anonymous.execute(pid).status_code == 403
        assert anonymous.http.get(
            f"/api/v1/inventory/receipts/{pid}?session_id=session-A").status_code == 403
    else:
        other = Client(active, service, sessions,
                       InventoryPrincipal("owner-B", frozenset({"session-B"}), can_execute_demo=True))
        assert other.execute(pid).status_code == 403
        assert other.http.get(
            f"/api/v1/inventory/receipts/{pid}?session_id=session-A").status_code == 403
    assert balance(active) == ("10", "5") and turns(service) == []


def test_mid_transaction_failure_rolls_back_everything(runtime):
    _, source, active = runtime
    sessions = {"session-A": session()}
    client = Client(active, facts_service(active), sessions,
                    InventoryPrincipal("owner-A", frozenset({"session-A"}), can_execute_demo=True))
    pid = client.preview().json()["structured_data"]["data"]["preview_id"]
    original = client.active.ledger.stock
    calls = {"n": 0}

    def flaky(namespace, code, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("synthetic write fault")
        return original(namespace, code, **kwargs)

    with patch.object(client.active.ledger, "stock", side_effect=flaky):
        assert client.execute(pid).status_code == 503
    assert balance(active) == ("10", "5")
    assert active.ledger.movements("demo") == []
    assert turns(client.service) == []
    # 回滚后可正常重试，且只成功一次。
    assert client.execute(pid).status_code == 200
    assert balance(active) == ("4", "2")
    assert len(execution_turns(client.service)) == 1
