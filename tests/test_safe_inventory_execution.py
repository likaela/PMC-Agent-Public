"""P1 execution regressions: synthetic facts and temporary databases only."""
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/backend"))

from api.routes.inventory import router
from repositories.inventory_adjustments import (LegacyInventoryError, apply_to_records,
    append_deduction, load_deductions, patch_last_deduction)
from repositories.public_data_repository import PublicInventoryRepository
from services.book_inventory_service import BookInventoryService
from services.inventory_execution_service import InventoryExecutionService
from services.safe_inventory_runtime import (InventoryPrincipal, SafeInventoryRuntime,
    demand_fingerprint, overlay_observations, preview_issue)


def lot(code, batch, qty, received="2024-01-01T00:00:00+00:00", **changes):
    return {"material_code": code, "batch_no": batch, "location": batch, "site": "TEST",
        "name": "Synthetic material", "inventory_unit": "片", "recorded_stock_qty": qty,
        "on_hand_qty": qty, "available_qty": qty, "reserved_qty": None,
        "received_at": received, "last_movement_at": received, "expiry_date": None,
        "quality_status": "normal", "duplicate_observation": False,
        "provenance": {"source_file": "synthetic.json", "row_number": 1}, **changes}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    monkeypatch.setenv("PMC_RUNTIME_ROOT", str(root))
    monkeypatch.delenv("PMC_DEMO_INVENTORY_WRITE_ENABLED", raising=False)
    source = {"source_file": "synthetic.json", "records": {
        "TEST-A": [lot("TEST-A", "OLD", "4"), lot("TEST-A", "NEW", "6", "2024-02-01T00:00:00+00:00")],
        "TEST-B": [lot("TEST-B", "B", "5")]},
        "quantity_confirmation": {"unit": "片", "product_scope": [{"product": "DEMO-PMC-01", "revision": "V01"}]}}
    active = SafeInventoryRuntime(root, source_provider=lambda namespace: copy.deepcopy(source), allow_demo_execution=True)
    active.ensure("demo")
    return root, source, active


def demands(a="6", b="2"):
    return [{"material_code": "TEST-A", "required_qty": a, "unit": "片"},
            {"material_code": "TEST-B", "required_qty": b, "unit": "片"}]


def preview(active, rows=None, owner="owner-A", sid="session-A", **options):
    return active.execution().preview("demo", owner, sid, "DEMO-PMC-01", 3,
        rows or demands(), {"source_file": "synthetic-bom.json"}, execution_authorized=True, **options)


def balance(active):
    return tuple(active.ledger.stock("demo", code)["on_hand_qty"] for code in ("TEST-A", "TEST-B"))


def test_preview_has_no_stock_movements_and_fifo_confirm_is_atomic(setup):
    _, _, active = setup
    result = preview(active)
    assert result["status"] == "ok" and result["data"]["can_execute"] is True
    assert result["data"]["executed"] is False and balance(active) == ("10", "5")
    assert active.ledger.movements("demo") == []
    allocations = result["data"]["materials"][0]["allocations"]
    assert [row["batch_no"] for row in allocations] == ["OLD", "NEW"]
    assert [row["allocated_qty"] for row in allocations] == ["4", "2"]
    executed = active.execution().execute(result["data"]["preview_id"], "owner-A", "session-A", confirmed=True)
    assert executed["status"] == "ok" and executed["data"]["executed"] is True
    assert balance(active) == ("4", "3") and len(active.ledger.movements("demo")) == 3


def test_repeat_restart_and_durable_receipt_do_not_debit_twice(setup):
    root, source, active = setup
    pid = preview(active)["data"]["preview_id"]
    first = active.execution().execute(pid, "owner-A", "session-A", confirmed=True)
    restarted = SafeInventoryRuntime(root, source_provider=lambda namespace: copy.deepcopy(source), allow_demo_execution=True)
    restarted.ensure("demo")
    assert restarted.execution().execute(pid, "owner-A", "session-A", confirmed=True) == first
    assert restarted.execution().receipt(pid, "owner-A", "session-A") == first
    assert balance(restarted) == ("4", "3")


def test_duplicate_parallel_confirmation_only_executes_once(setup):
    _, _, active = setup
    pid = preview(active)["data"]["preview_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: active.execution().execute(pid, "owner-A", "session-A", confirmed=True), range(2)))
    assert results[0] == results[1] and balance(active) == ("4", "3")


def test_parallel_different_previews_reject_stale_version(setup):
    _, _, active = setup
    pids = [preview(active)["data"]["preview_id"] for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pid: active.execution().execute(pid, "owner-A", "session-A", confirmed=True), pids))
    assert sorted(row["status"] for row in results) == ["ok", "stale_preview"]
    assert balance(active) == ("4", "3")


def test_insufficient_one_material_deducts_nothing(setup):
    _, _, active = setup
    plan = preview(active, demands(b="20"))
    assert plan["data"]["executable"] is False
    result = active.execution().execute(plan["data"]["preview_id"], "owner-A", "session-A", confirmed=True)
    assert result["status"] == "data_unavailable" and balance(active) == ("10", "5")
    assert active.ledger.movements("demo") == []


def test_failure_mid_transaction_rolls_back_receipt_and_all_balances(setup):
    _, _, active = setup
    pid = preview(active)["data"]["preview_id"]
    with active.ledger.transaction() as connection:
        connection.execute("CREATE TRIGGER synthetic_failure BEFORE INSERT ON ledger_movements WHEN NEW.material_code='TEST-B' BEGIN SELECT RAISE(ABORT, 'synthetic rollback'); END")
    with pytest.raises(Exception, match="synthetic rollback"):
        active.execution().execute(pid, "owner-A", "session-A", confirmed=True)
    assert balance(active) == ("10", "5") and active.ledger.movements("demo") == []
    assert active.execution().receipt(pid, "owner-A", "session-A")["status"] == "data_unavailable"
    with active.ledger.transaction() as connection:
        connection.execute("DROP TRIGGER synthetic_failure")
    assert active.execution().execute(pid, "owner-A", "session-A", confirmed=True)["status"] == "ok"


@pytest.mark.parametrize("field,value", [
    ("expiry_date", "2020-99-99"), ("expiry_date", ""), ("expiry_date", True),
    ("received_at", None), ("received_at", "not-a-date"), ("received_at", "2024-01-01"),
    ("last_movement_at", "2023-12-01T00:00:00+00:00"),
    ("last_movement_at", "2099-01-01T00:00:00+00:00"),
])
def test_malformed_or_conflicting_dates_block_preview_and_confirmation(setup, field, value):
    _, source, active = setup
    source["records"]["TEST-A"][0][field] = value
    # A separate synthetic namespace seed, never a released data modification.
    with active.ledger.transaction() as connection:
        connection.execute("DELETE FROM ledger_lots")
        connection.execute("DELETE FROM ledger_namespaces")
    active.ensure("demo")
    plan = preview(active)
    assert plan["data"]["executable"] is False and plan["warnings"]
    result = active.execution().execute(plan["data"]["preview_id"], "owner-A", "session-A", confirmed=True)
    assert result["status"] == "data_unavailable" and balance(active) == ("10", "5")
    assert active.ledger.movements("demo") == []


def test_expired_batches_are_not_used_for_fifo(setup):
    _, source, active = setup
    source["records"]["TEST-A"][0]["expiry_date"] = "2024-03-01"
    with active.ledger.transaction() as connection:
        connection.execute("DELETE FROM ledger_lots")
        connection.execute("DELETE FROM ledger_namespaces")
    active.ensure("demo")
    plan = preview(active)
    assert plan["data"]["executable"] is True
    assert [row["batch_no"] for row in plan["data"]["materials"][0]["allocations"]] == ["NEW"]


def test_same_date_fifo_order_keeps_existing_location_batch_tiebreak(setup):
    _, source, active = setup
    source["records"]["TEST-A"][1]["received_at"] = source["records"]["TEST-A"][0]["received_at"]
    source["records"]["TEST-A"][1]["last_movement_at"] = source["records"]["TEST-A"][0]["last_movement_at"]
    with active.ledger.transaction() as connection:
        connection.execute("DELETE FROM ledger_lots")
        connection.execute("DELETE FROM ledger_namespaces")
    active.ensure("demo")
    first = preview(active, demands(a="8"))
    second = preview(active, demands(a="8"))
    def allocation_order(result):
        return [(row["inventory_lot"], row["allocated_qty"]) for row in result["data"]["materials"][0]["allocations"]]
    assert len(allocation_order(first)) == 2
    assert allocation_order(first) == allocation_order(second)
    assert [row["batch_no"] for row in first["data"]["materials"][0]["allocations"]] == ["NEW", "OLD"]


def test_expired_preview_has_no_side_effect(setup):
    _, _, active = setup
    service = InventoryExecutionService(active.ledger, allow_demo_execution=True, preview_lifetime_seconds=-1)
    pid = service.preview("demo", "owner-A", "session-A", "DEMO-PMC-01", 3, demands())["data"]["preview_id"]
    assert service.execute(pid, "owner-A", "session-A", confirmed=True)["status"] == "preview_expired"
    assert balance(active) == ("10", "5")


def test_source_change_after_preview_refuses_confirmation_without_reset(setup):
    _, source, active = setup
    pid = preview(active)["data"]["preview_id"]
    source["records"]["TEST-A"][0]["received_at"] = None
    assert active.execution().execute(pid, "owner-A", "session-A", confirmed=True)["status"] == "stale_preview"
    assert balance(active) == ("10", "5")
    with pytest.raises(ValueError, match="seed changed"):
        active.ensure("demo")


@pytest.mark.parametrize("value", [0, -1, True, "1", 1.5])
def test_invalid_quantity_does_not_even_create_preview(setup, value):
    _, _, active = setup
    result = active.execution().preview("demo", "owner-A", "session-A", "DEMO-PMC-01", value, demands())
    assert result["status"] == "invalid_query"
    with active.ledger.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ledger_previews").fetchone()[0] == 0


def test_all_writes_default_disabled_and_enterprise_always_disabled(setup):
    _, source, active = setup
    disabled = InventoryExecutionService(active.ledger)
    pid = disabled.preview("demo", "owner-A", "session-A", "DEMO-PMC-01", 3, demands())["data"]["preview_id"]
    assert disabled.execute(pid, "owner-A", "session-A", confirmed=True)["status"] == "forbidden"
    active.ledger.seed("enterprise", source)
    plan = active.execution().preview("enterprise", "owner-A", "session-A", "TEST-PRODUCT", 3, demands())
    assert plan["data"]["can_execute"] is False
    assert active.execution().execute(plan["data"]["preview_id"], "owner-A", "session-A", confirmed=True)["status"] == "forbidden"
    assert balance(active) == ("10", "5")


def synthetic_service(active, source, product="DEMO-PMC-01", duplicate_a=False):
    class Facts:
        def __init__(self):
            self.repository = self
        def resolve_item(self, code):
            return {"material_code": code} if code in {product, "TEST-A", "TEST-B"} else None
        def list_published_bom_versions(self, code):
            return [{"version": "V01"}] if code == product else []
        def query_bom(self, code, revision):
            return {"status": "ok", "data": {"parent_item": {"material_code": product, "unit": "片"},
                "lines": [{"child_item": {"material_code": code, "name": code}, "qty_per": qty,
                           "unit": "片", "provenance": {"source_file": "synthetic-bom.json"}}
                          for code, qty in ((("TEST-A", "1"), ("TEST-A", "1"), ("TEST-B", "1")) if duplicate_a
                                            else (("TEST-A", "2"), ("TEST-B", "1")))]},
                "provenance": {"source_file": "synthetic-bom.json"}}
    observations = SimpleNamespace(load=lambda: active.ledger.load_observations("demo"))
    return SimpleNamespace(book_service=BookInventoryService(Facts(), observations))


def session(quantity=3, generation=1, status="valid", product="DEMO-PMC-01", sid="session-A"):
    return {"session_id": sid, "context": {"product_context": {"product_code": product},
        "current_demand": {"generation": generation, "status": status, "product": product, "quantity": quantity, "unit": "片"}}}


def api_client(active, service, sessions, principal=None):
    if not hasattr(service, "repository"):
        from repositories.agent_conversation_repository import AgentConversationRepository
        from repositories.fact_store import SQLiteFactStore
        store = SQLiteFactStore(active.root / "synthetic-conversations.sqlite3")
        store.migrate()
        service.repository = AgentConversationRepository(store)
        for sid, value in sessions.items():
            with patch("repositories.agent_conversation_repository.uuid4", side_effect=[sid, "conversation-" + sid]):
                created = service.repository.create_session(initial_context=value["context"])
            sessions[sid] = created
    app = FastAPI()
    app.include_router(router)
    app.state.inventory_runtime = active
    app.state.inventory_session_loader = lambda sid, scenario: (service, sessions[sid])
    if principal is not None:
        @app.middleware("http")
        async def trusted_test_middleware(request, call_next):
            request.state.inventory_principal = principal
            return await call_next(request)
    return TestClient(app)


def test_real_api_preview_confirm_receipt_and_independent_client_isolation(setup):
    _, source, active = setup
    sessions = {"session-A": session(), "session-B": session(sid="session-B")}
    service = synthetic_service(active, source)
    pa = InventoryPrincipal("owner-A", frozenset({"session-A"}), can_execute_demo=True)
    pb = InventoryPrincipal("owner-B", frozenset({"session-B"}), can_execute_demo=True)
    a, b = api_client(active, service, sessions, pa), api_client(active, service, sessions, pb)
    plan = a.post("/api/v1/inventory/preview", json={"session_id": "session-A", "product": "DEMO-PMC-01", "quantity": 3})
    assert plan.status_code == 200
    data = plan.json()["structured_data"]["data"]
    assert data["can_execute"] is True and data["session_id"] == "session-A"
    assert [row["required_qty"] for row in data["materials"]] == ["6", "3"]
    pid = data["preview_id"]
    request = {"session_id": "session-A", "preview_id": pid, "confirmed": True}
    assert b.post("/api/v1/inventory/execute", json=request).status_code == 403
    assert b.get(f"/api/v1/inventory/receipts/{pid}?session_id=session-A").status_code == 403
    result = a.post("/api/v1/inventory/execute", json=request)
    assert result.status_code == 200 and result.json()["structured_data"]["data"]["executed"] is True
    assert balance(active) == ("4", "2")
    assert a.post("/api/v1/inventory/execute", json=request).json() == result.json()
    receipt = a.get(f"/api/v1/inventory/receipts/{pid}?session_id=session-A")
    assert receipt.json()["structured_data"]["data"] == result.json()["structured_data"]["data"]
    history = service.repository.history("session-A")
    assert len(history["turns"]) == 1
    assert history["turns"][0]["response"]["structured_data"]["operation"] == "inventory_execution"
    assert history["turns"][0]["fact_references"][0]["result"]["data"]["executed"] is True
    assert {row["owner_id"] for row in active.ledger.movements("demo") if "owner_id" in row} == set()
    with active.ledger.transaction() as connection:
        assert connection.execute("SELECT owner_id FROM ledger_executions").fetchall()[0][0] == "owner-A"


@pytest.mark.parametrize("status,quantity", [("valid", 2), ("invalid", None)])
def test_new_or_invalid_demand_invalidates_previous_preview_at_api(setup, status, quantity):
    _, source, active = setup
    sessions = {"session-A": session()}
    service = synthetic_service(active, source)
    principal = InventoryPrincipal("owner-A", frozenset({"session-A"}), can_execute_demo=True)
    client = api_client(active, service, sessions, principal)
    plan = client.post("/api/v1/inventory/preview", json={"session_id": "session-A", "product": "DEMO-PMC-01", "quantity": 3}).json()
    pid = plan["structured_data"]["data"]["preview_id"]
    sessions["session-A"] = session(quantity=quantity, generation=2, status=status)
    result = client.post("/api/v1/inventory/execute", json={"session_id": "session-A", "preview_id": pid, "confirmed": True})
    assert result.json()["status"] == "stale_preview" and balance(active) == ("10", "5")
    assert result.json()["structured_data"]["data"] == {"preview_id": pid, "session_id": "session-A", "executed": False}


def test_public_api_cannot_promote_headers_or_session_id_to_identity(setup):
    _, source, active = setup
    service = synthetic_service(active, source)
    client = api_client(active, service, {"session-A": session()})
    pid = preview(active)["data"]["preview_id"]
    response = client.post("/api/v1/inventory/execute", headers={"x-pmc-user": "owner-A", "x-inventory-write-token": "synthetic-shared-token-xxxxxxxxxxxx"},
        json={"session_id": "session-A", "preview_id": pid, "confirmed": True})
    assert response.status_code == 403 and balance(active) == ("10", "5")


def test_unauthorized_preview_cannot_be_promoted_by_later_confirmation(setup):
    _, _, active = setup
    result = active.execution().preview("demo", "owner-A", "session-A", "DEMO-PMC-01", 3, demands())
    assert result["data"]["execution_available"] is False
    executed = active.execution().execute(result["data"]["preview_id"], "owner-A", "session-A", confirmed=True)
    assert executed["status"] == "forbidden" and balance(active) == ("10", "5")


@pytest.mark.parametrize("owner,sid", [("owner-B", "session-A"), ("owner-A", "session-B")])
def test_preview_and_receipt_are_bound_to_owner_and_session(setup, owner, sid):
    _, _, active = setup
    pid = preview(active)["data"]["preview_id"]
    assert active.execution().execute(pid, owner, sid, confirmed=True)["status"] == "forbidden"
    assert active.execution().receipt(pid, owner, sid)["status"] == "forbidden"
    assert balance(active) == ("10", "5")


@pytest.mark.parametrize("case", ["unknown", "duplicate", "unit_conflict"])
def test_unknown_stock_duplicate_rows_and_unit_conflicts_never_execute(setup, case):
    _, source, active = setup
    if case == "unknown":
        source["records"]["TEST-A"][0].update(recorded_stock_qty=None, on_hand_qty=None, available_qty=None)
    elif case == "duplicate":
        source["records"]["TEST-A"].append(copy.deepcopy(source["records"]["TEST-A"][0]))
    else:
        source["records"]["TEST-A"][0]["inventory_unit"] = "kg"
    with active.ledger.transaction() as connection:
        connection.execute("DELETE FROM ledger_lots")
        connection.execute("DELETE FROM ledger_namespaces")
    active.ensure("demo")
    plan = preview(active)
    assert plan["data"]["executable"] is False
    assert active.execution().execute(plan["data"]["preview_id"], "owner-A", "session-A", confirmed=True)["status"] == "data_unavailable"
    assert active.ledger.movements("demo") == []


def test_false_confirmation_is_not_a_write(setup):
    _, _, active = setup
    pid = preview(active)["data"]["preview_id"]
    assert active.execution().execute(pid, "owner-A", "session-A", confirmed=False)["status"] == "confirmation_required"
    assert balance(active) == ("10", "5")


def test_committed_receipt_survives_history_failure_and_retry_only_repairs_history(setup, monkeypatch):
    _, source, active = setup
    service = synthetic_service(active, source)
    sessions = {"session-A": session()}
    principal = InventoryPrincipal("owner-A", frozenset({"session-A"}), can_execute_demo=True)
    client = api_client(active, service, sessions, principal)
    plan = client.post("/api/v1/inventory/preview", json={"session_id": "session-A", "product": "DEMO-PMC-01", "quantity": 3}).json()
    pid = plan["structured_data"]["data"]["preview_id"]
    body = {"session_id": "session-A", "preview_id": pid, "confirmed": True}
    append = service.repository.append_turn
    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic history fault")
    monkeypatch.setattr(service.repository, "append_turn", unavailable)
    result = client.post("/api/v1/inventory/execute", json=body)
    assert result.status_code == 503 and result.json()["code"] == "INVENTORY_RECEIPT_PENDING"
    assert balance(active) == ("4", "2") and service.repository.history("session-A")["turns"] == []
    assert client.get(f"/api/v1/inventory/receipts/{pid}?session_id=session-A").json()["structured_data"]["data"]["executed"] is True
    monkeypatch.setattr(service.repository, "append_turn", append)
    repaired = client.post("/api/v1/inventory/execute", json=body)
    assert repaired.status_code == 200 and balance(active) == ("4", "2")
    assert len(service.repository.history("session-A")["turns"]) == 1
    assert client.post("/api/v1/inventory/execute", json=body).json() == repaired.json()
    assert len(service.repository.history("session-A")["turns"]) == 1


def test_enterprise_preview_uses_existing_book_view_without_seeding_enterprise(setup):
    _, source, active = setup
    service = synthetic_service(active, source, product="TEST-PRODUCT")
    current = session(product="TEST-PRODUCT")
    result = preview_issue(service, current, "TEST-PRODUCT", 3, inventory_runtime=active)
    data = result["data"]
    assert data["write_policy"] == "enterprise_read_only" and data["execution_available"] is False
    assert data["can_execute"] is False and data["executed"] is False
    assert len(data["materials"]) == 2 and data["materials"][0]["required_qty"] == "6"
    with active.ledger.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ledger_namespaces WHERE namespace='enterprise'").fetchone()[0] == 0


def write_legacy(root, entries):
    root.mkdir(parents=True, exist_ok=True)
    path = root / "inventory_adjustments.json"
    path.write_text(json.dumps({"format": "pmc_inventory_adjustments_v1", "deductions": entries}), encoding="utf-8")
    return path


def test_legacy_history_blocks_new_ledger_and_file_is_not_rewritten(setup):
    root, _, _ = setup
    path = write_legacy(root, [{"consumptions": [{"material_code": "TEST-A", "batch_no": "OLD", "location": "OLD", "consumed_qty": "1"}]}])
    before = path.read_bytes()
    with pytest.raises(LegacyInventoryError):
        SafeInventoryRuntime(root)
    for action in (lambda: append_deduction({}), lambda: patch_last_deduction(test=True)):
        with pytest.raises(LegacyInventoryError):
            action()
    assert path.read_bytes() == before


def test_legacy_corruption_fails_closed_instead_of_restoring_seed(setup):
    root, _, _ = setup
    root.mkdir(parents=True, exist_ok=True)
    (root / "inventory_adjustments.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(LegacyInventoryError):
        load_deductions()


def test_legacy_read_only_subtraction_conserves_and_rejects_ambiguous_rows(setup):
    root, source, _ = setup
    write_legacy(root, [{"consumptions": [{"material_code": "TEST-A", "batch_no": "OLD", "location": "OLD", "consumed_qty": "1"}]}])
    records = source["records"]
    result = apply_to_records(records)
    assert result["TEST-A"][0]["recorded_stock_qty"] == "3" and result["TEST-A"][1]["recorded_stock_qty"] == "6"
    assert records["TEST-A"][0]["recorded_stock_qty"] == "4"
    repeated = copy.deepcopy(records)
    repeated["TEST-A"].append(copy.deepcopy(repeated["TEST-A"][0]))
    with pytest.raises(LegacyInventoryError):
        apply_to_records(repeated)


def test_repository_caches_only_seed_and_observes_new_legacy_read_only_history(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    monkeypatch.setenv("PMC_RUNTIME_ROOT", str(root))
    source = {"records": {"TEST-A": [lot("TEST-A", "OLD", "4")]}}
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps({"format": "pmc_excel_observations_v1", "observations": source}), encoding="utf-8")
    repository = PublicInventoryRepository(path)
    assert repository.load()["records"]["TEST-A"][0]["recorded_stock_qty"] == "4"
    write_legacy(root, [{"consumptions": [{"material_code": "TEST-A", "batch_no": "OLD", "location": "OLD", "consumed_qty": "1"}]}])
    assert repository.load()["records"]["TEST-A"][0]["recorded_stock_qty"] == "3"
    assert repository.load()["records"]["TEST-A"][0]["recorded_stock_qty"] == "3"


def test_actual_synthetic_demo_tools_read_the_same_persistent_balances(tmp_path, monkeypatch):
    """Published Demo facts are synthetic; every write stays in tmp_path."""
    from services.demo_scenario_agent_service import build_demo_scenario_agent
    root = tmp_path / "runtime"
    monkeypatch.setenv("PMC_RUNTIME_ROOT", str(root))
    monkeypatch.setenv("PMC_PUBLIC_DATA_ROOT", str(ROOT / "data/public"))
    source_path = ROOT / "data/public/demo/demo_scenario_50_vs_100/inventory_facts.json"
    original = source_path.read_bytes()
    service = build_demo_scenario_agent(runtime_root=tmp_path / "synthetic-demo-facts")
    current = service.repository.create_session()
    sid = current["session_id"]
    current["context"]["current_demand"] = {"generation": 1, "status": "valid", "product": "DEMO-PMC-01", "quantity": 50, "unit": "台"}
    active = SafeInventoryRuntime(root, allow_demo_execution=True)
    principal = InventoryPrincipal("synthetic-owner", frozenset({sid}), can_execute_demo=True)
    manifest = service.repository.manifest
    arguments = {"product": manifest.product, "bom_version": manifest.revision,
        "effective_date": manifest.effective_date.isoformat(), "as_of": manifest.snapshot_at.isoformat()}
    before = service.tool_resolver("production_capacity").invoke(arguments)
    assert before["data"]["theoretical_max_quantity"] == 75
    plan = preview_issue(service, current, "DEMO-PMC-01", 50, principal=principal, inventory_runtime=active)
    assert plan["data"]["can_execute"] is True
    result = active.execution().execute(plan["data"]["preview_id"], principal.owner_id, sid, confirmed=True,
        current_demand_fingerprint=demand_fingerprint(current))
    assert result["status"] == "ok" and result["data"]["executed"] is True
    after = service.tool_resolver("production_capacity").invoke(arguments)
    assert after["data"]["theoretical_max_quantity"] == 25
    purchase = service.tool_resolver("purchase_planning").invoke({"product": manifest.product, "revision": manifest.revision,
        "effective_date": manifest.effective_date.isoformat(), "quantity": 100,
        "start_date": manifest.start_date.isoformat(), "as_of": manifest.snapshot_at.isoformat()})
    assert purchase["status"] == "ok" and len(purchase["data"]["recommendations"]) == 4
    restarted = build_demo_scenario_agent(runtime_root=tmp_path / "synthetic-demo-facts")
    assert restarted.tool_resolver("production_capacity").invoke(arguments)["data"]["theoretical_max_quantity"] == 25
    assert source_path.read_bytes() == original


def test_duplicate_bom_lines_merge_once_through_real_book_and_execute_api(setup):
    _, source, active = setup
    service = synthetic_service(active, source, duplicate_a=True)
    sessions = {"session-A": session()}
    principal = InventoryPrincipal("owner-A", frozenset({"session-A"}), can_execute_demo=True)
    client = api_client(active, service, sessions, principal)
    result = client.post("/api/v1/inventory/preview", json={"session_id": "session-A", "product": "DEMO-PMC-01", "quantity": 3})
    data = result.json()["structured_data"]["data"]
    assert len(data["requirements"]) == 2
    assert data["requirements"][0] == {"material_code": "TEST-A", "required_qty": "6", "unit": "片"}
    pid = data["preview_id"]
    receipt = client.post("/api/v1/inventory/execute", json={"session_id": "session-A", "preview_id": pid, "confirmed": True}).json()
    assert receipt["status"] == "ok" and balance(active) == ("4", "2")
    movements = [row for row in receipt["structured_data"]["data"]["movements"] if row["material_code"] == "TEST-A"]
    assert [row["allocated_qty"] for row in movements] == ["4", "2"]
    assert sum(Decimal(row["allocated_qty"]) for row in movements) == Decimal("6")
    assert sum(Decimal(row["deducted_qty"]) for row in active.ledger.movements("demo") if row["material_code"] == "TEST-A") == Decimal("6")


def test_duplicate_demands_in_execution_service_merge_before_fifo(setup):
    _, _, active = setup
    result = preview(active, [{"material_code": "TEST-A", "required_qty": "2", "unit": "片"},
        {"material_code": "TEST-A", "required_qty": "4", "unit": "片"},
        {"material_code": "TEST-B", "required_qty": "2", "unit": "片"}])
    assert result["data"]["requirements"][0]["required_qty"] == "6"
    assert len(result["data"]["materials"]) == 2
    receipt = active.execution().execute(result["data"]["preview_id"], "owner-A", "session-A", confirmed=True)
    assert receipt["status"] == "ok" and balance(active) == ("4", "3")


@pytest.mark.parametrize("same_site", [False, True])
def test_demo_projection_uses_full_lot_identity_or_rejects_ambiguity(setup, monkeypatch, same_site):
    from repositories.published_purchase_repository import PublishedPurchaseRepository
    from services.safe_inventory_runtime import OperationalDemoRepository
    _, source, active = setup
    extra = copy.deepcopy(source["records"]["TEST-A"][0])
    extra.update(site="TEST" if same_site else "OTHER", recorded_stock_qty="9", on_hand_qty="9", available_qty="9")
    source["records"]["TEST-A"].append(extra)
    with active.ledger.transaction() as connection:
        connection.execute("DELETE FROM ledger_lots")
        connection.execute("DELETE FROM ledger_namespaces")
    active.ensure("demo")
    facts = [{**copy.deepcopy(row), "raw_material_code": "TEST-A"} for row in source["records"]["TEST-A"]]
    monkeypatch.setattr(PublishedPurchaseRepository, "get_inventory_snapshot", lambda *args: {"facts": facts, "snapshot": {}})
    adapter = object.__new__(OperationalDemoRepository)
    adapter.ledger = active.ledger
    if same_site:
        with pytest.raises(ValueError, match="Ambiguous Demo lot identity"):
            adapter.get_inventory_snapshot("TEST-A", datetime.now(UTC))
    else:
        result = adapter.get_inventory_snapshot("TEST-A", datetime.now(UTC))
        assert [(row["site"], row["on_hand_qty"]) for row in result["facts"]] == [
            ("TEST", Decimal("4")), ("TEST", Decimal("6")), ("OTHER", Decimal("9"))]
