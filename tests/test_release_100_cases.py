"""TC-001–TC-100：产品目录、单位、数量、库存质量、模拟执行、事务与安全的 100 条验收用例。

分组（与 docs/test_cases_100.md 一一对应）：
A TC-001–010 前后端产品目录与 Demo 移除
B TC-011–025 单位解析与换算
C TC-026–035 生产数量与上下文
D TC-036–050 库存质量与采购结论
E TC-051–065 领料预览、明确确认与模拟执行
F TC-066–075 库存守恒、批次分配与 FIFO
G TC-076–085 幂等、并发、事务与失败恢复
H TC-086–095 回执、会话隔离、重启与重置
I TC-096–100 权限、安全隔离与禁止真实写入

约束：全部使用合成库存与项目外临时数据库；真实企业库存、BOM、门店 Excel 与
`data/` 目录只读；不安装依赖、不修改既有测试预期。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "src/backend"), str(ROOT / "tests/support")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

#: 真实业务数据基线（本轮开始前采集，测试期间不得变化）。
BASELINE_SHA256 = {
    "data/public/inventory/observations.json": "ff7ed40c2b5e7d818462163b32a1a4ccda4879684cb9a688b05c24acecc5d2ab",
    "data/public/bom/published.db": "ffabe54407814f394d1f0a7c58c4d0e762dc6701f53c11416f32a4e37e11b973",
    "data/public/release.json": "7c1ae314860627613b9559d3025ff820b66bfcd1fe2d6ba4927ce5faa19655cc",
}
PRODUCTS = ("KQ-Z112", "KQ-BHX3588M")


# ---------------------------------------------------------------- 公共夹具

@pytest.fixture
def public_client(tmp_path, monkeypatch):
    """真实公开入口（只读企业数据）：用于目录与数量上下文用例。"""
    monkeypatch.setenv("PMC_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("PMC_PUBLIC_DATA_ROOT", str(ROOT / "data/public"))
    monkeypatch.delenv("PMC_ALLOW_DEMO_SCENARIO", raising=False)
    monkeypatch.delenv("PMC_SIMULATION_MODE", raising=False)
    from services.local_business_binding import _binding
    _binding.cache_clear()
    from scripts.run_public import initialize
    initialize()
    from fastapi.testclient import TestClient
    from api.public_app import app
    with TestClient(app) as client:
        yield client


@pytest.fixture
def simulation(tmp_path):
    """隔离模拟环境：真实 BOM 结构 + 合成库存 + 项目外账本。"""
    import simulation_server as sim
    snapshot = dict(os.environ)
    root = tmp_path / "simulation"
    app = sim.create_simulation_app(root)
    restore = app.state.simulation_restore
    try:
        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, app=app, root=root, module=sim)
    finally:
        restore()
        os.environ.clear()
        os.environ.update(snapshot)


@pytest.fixture
def sim_client(simulation):
    return simulation.client


# ---------------------------------------------------------------- 通用工具

def new_session(client, product, scenario=None):
    body = {"product_code": product}
    if scenario:
        body["scenario_id"] = scenario
    return client.post("/api/v1/agent/sessions", json=body)


def ask(client, sid, message):
    return client.post("/api/v1/agent/query",
                       json={"message": message, "session_id": sid, "persist_session": True}).json()


def data_of(answer):
    return (answer.get("structured_data") or {}).get("data") or {}


def preview_of(client, product="KQ-Z112", quantity=5):
    sid = new_session(client, product).json()["session_id"]
    answer = ask(client, sid, f"生产{quantity}片并扣减库存")
    return sid, answer, data_of(answer)


def stock(app, code):
    return app.state.inventory_runtime.ledger.stock("simulation", code)


def balances(app):
    codes = app.state.simulation_catalog and sorted(app.state.simulation_catalog)
    return {code: stock(app, code)["on_hand_qty"] for code in codes}


def execute(client, sid, pid, confirmed=True):
    return client.post("/api/v1/inventory/execute",
                       json={"session_id": sid, "preview_id": pid, "confirmed": confirmed})


def history(client, sid):
    return client.get(f"/api/v1/agent/sessions/{sid}").json()["turns"]


def execution_turns(client, sid):
    return [t for t in history(client, sid)
            if t["response"]["structured_data"].get("operation") == "inventory_execution"]


def sha(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- 合成 BOM/库存（D 组）

class SyntheticFacts:
    def __init__(self, product, parent_unit, lines):
        self.product, self.parent_unit, self.lines = product, parent_unit, lines
        self.codes = {code for code, _, _ in lines}
        self.repository = self

    def resolve_item(self, code):
        if code == self.product:
            return {"material_code": code, "unit": self.parent_unit, "name": "合成成品"}
        return {"material_code": code, "name": code} if code in self.codes else None

    def list_published_bom_versions(self, code):
        return [{"version": "V01", "effective_from": "2026-01-01"}] if code == self.product else []

    def published_bom_parent_codes(self):
        return {self.product}

    def query_bom(self, product, revision):
        return dict(status="ok", query=dict(parent_material_code=product, revision=revision),
            data=dict(parent_item={"material_code": product, "unit": self.parent_unit, "name": "合成成品"},
                revision=revision, effective_from="2026-01-01", line_count=len(self.lines),
                lines=[dict(child_item={"material_code": code, "unit": unit, "name": code},
                            qty_per=qty, unit=unit,
                            provenance={"source_file": "synthetic-bom.json", "row_number": index + 1})
                       for index, (code, qty, unit) in enumerate(self.lines)]),
            completeness={"unit": True}, provenance={"source_file": "synthetic-bom.json"}, warnings=[])


def lot(code, qty, unit="片", *, batch="B1", location="L1", duplicate=False):
    return dict(material_code=code, name=code, recorded_stock_qty=qty, inventory_unit=unit,
                batch_no=batch, location=location, duplicate_observation=duplicate,
                provenance={"source_file": "synthetic-inventory.json", "row_number": 1})


def source_of(records, *, unit_confirmation={"unit": "片"}, quantity_confirmation=None):
    return dict(source_file="synthetic-inventory.json", content_sha256="synthetic-only", sheet_name="Synthetic",
                record_count=sum(len(v) for v in records.values()), unique_material_count=len(records),
                duplicate_observation_count=sum(1 for rows in records.values() for r in rows if r["duplicate_observation"]),
                row_errors=[], excluded_sheets=[], unit_confirmation=unit_confirmation,
                quantity_confirmation=quantity_confirmation, records=records)


def synthetic_book(lines, records, **source):
    from services.book_inventory_service import BookInventoryService
    facts = SyntheticFacts("SYN-P", "片", lines)
    payload = copy.deepcopy(source_of(records, **source))
    return BookInventoryService(facts, SimpleNamespace(load=lambda: copy.deepcopy(payload)))


CONFIRMED = {"unit": "片", "product_scope": [{"product": "SYN-P", "revision": "V01"}]}


def synthetic_queries(book):
    from services.pmc_capability_service import PmcCapabilityQueries
    return PmcCapabilityQueries(SimpleNamespace(book_service=book, presentation=None))


# ---------------------------------------------------------------- 执行服务（F/G 组）

def make_lots(records):
    return {code: [dict(row) for row in rows] for code, rows in records.items()}


def execution_fixture(tmp_path, records, *, lifetime=900, allowed=("simulation",)):
    from repositories.inventory_ledger_repository import InventoryLedgerRepository
    from services.inventory_execution_service import InventoryExecutionService
    ledger = InventoryLedgerRepository(tmp_path / "ledger.sqlite3")
    ledger.seed("simulation", {"records": copy.deepcopy(records), "quantity_confirmation": None,
                               "source_file": "synthetic"}, quantity_basis="published_available")
    service = InventoryExecutionService(ledger, allow_demo_execution=True, allowed_namespaces=allowed,
                                        preview_lifetime_seconds=lifetime)
    return ledger, service


def ledger_lot(code, batch, qty, received="2024-01-01T00:00:00+00:00", **extra):
    return {"material_code": code, "batch_no": batch, "location": batch, "site": "SIM", "warehouse": "SIM",
            "name": code, "inventory_unit": "片", "recorded_stock_qty": qty, "on_hand_qty": qty,
            "available_qty": qty, "reserved_qty": None, "received_at": received,
            "last_movement_at": received, "expiry_date": None, "quality_status": "normal",
            "duplicate_observation": False,
            "provenance": {"source_file": "synthetic.json", "row_number": 1}, **extra}


def demands(*rows):
    return [{"material_code": code, "required_qty": qty, "unit": unit} for code, qty, unit in rows]


@contextmanager
def failing_transaction(ledger, marker):
    """在指定 SQL 写入处注入故障，用于验证事务回滚。"""
    original = ledger.transaction

    class Proxy:
        def __init__(self, raw):
            self.raw = raw

        def execute(self, sql, params=()):
            if marker in sql:
                raise RuntimeError("synthetic write failure")
            return self.raw.execute(sql, params)

    @contextmanager
    def patched():
        with original() as connection:
            yield Proxy(connection)

    with patch.object(ledger, "transaction", patched):
        yield


@contextmanager
def live_server(app):
    import uvicorn
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if getattr(server, "started", False):
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def http_json(url, payload=None, method=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method or ("POST" if body else "GET"),
                                     headers={"Content-Type": "application/json"})
    try:
        with opener.open(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


# ================================================================ A. 产品目录与 Demo 移除

def test_tc001_catalog_contains_exactly_two_products(public_client):
    products = public_client.get("/api/v1/agent/catalog").json()["products"]
    assert [p["product_code"] for p in products] == list(PRODUCTS)


def test_tc002_catalog_product_names_are_canonical(public_client):
    table = {p["product_code"]: p["product_name"] for p in
             public_client.get("/api/v1/agent/catalog").json()["products"]}
    assert table["KQ-Z112"] == "飞腾八核核心板"
    assert table["KQ-BHX3588M"] == "瑞芯微核心模块"


def test_tc003_demo_product_absent_from_catalog(public_client):
    codes = {p["product_code"] for p in public_client.get("/api/v1/agent/catalog").json()["products"]}
    assert "DEMO-PMC-01" not in codes
    assert all(p["synthetic"] is False for p in public_client.get("/api/v1/agent/catalog").json()["products"])


def test_tc004_demo_product_code_cannot_create_session(public_client):
    response = new_session(public_client, "DEMO-PMC-01")
    assert response.status_code == 422


def test_tc005_demo_scenario_is_rejected_by_default(public_client):
    response = public_client.post("/api/v1/agent/sessions", json={"scenario_id": "demo_scenario_50_vs_100"})
    assert response.status_code == 422
    assert response.json()["code"] == "DEMO_SCENARIO_DISABLED"


def test_tc006_demo_alias_resolves_to_item_not_found(public_client):
    resolved = public_client.post("/api/v1/agent/resolve", json={"message": "演示产品 生产50台"}).json()
    assert resolved["status"] == "item_not_found"
    assert resolved["product_code"] is None


def test_tc007_unknown_product_never_falls_back_to_demo(public_client):
    resolved = public_client.post("/api/v1/agent/resolve", json={"message": "KQ-NOT-EXIST 生产10片"}).json()
    assert resolved["status"] == "item_not_found" and resolved["product_code"] is None


def test_tc008_kq_z112_bom_query_still_works(public_client):
    sid = new_session(public_client, "KQ-Z112").json()["session_id"]
    answer = ask(public_client, sid, "查询KQ-Z112的BOM")
    data = data_of(answer)
    assert answer["status"] == "ok"
    assert data.get("line_count", data.get("total_material_count")) == 34


def test_tc009_kq_bhx3588m_bom_query_still_works(public_client):
    sid = new_session(public_client, "KQ-BHX3588M").json()["session_id"]
    answer = ask(public_client, sid, "查询KQ-BHX3588M的BOM")
    data = data_of(answer)
    assert answer["status"] == "ok"
    assert data.get("line_count", data.get("total_material_count")) == 91


def test_tc010_catalog_rows_match_frontend_filter_contract(public_client):
    products = public_client.get("/api/v1/agent/catalog").json()["products"]
    # 前端会丢弃 synthetic 行、并只保留 scenario_id 为 None 的模式；结果必须仍是这两个产品。
    kept = [p for p in products if p["synthetic"] is False
            and any(m["mode"] == p["default_mode"] and m["scenario_id"] is None for m in p["modes"])]
    assert [p["product_code"] for p in kept] == list(PRODUCTS)


# ================================================================ B. 单位解析与换算

def test_tc011_unit_piece_is_count_unit():
    from services import unit_system as units
    parsed = units.spec("片")
    assert parsed.dimension == units.COUNT and parsed.name == "片" and units.count_equivalent("片")


def test_tc012_unit_block_normalizes_to_piece():
    from services import unit_system as units
    assert units.compatible("块", "片") is True
    assert units.convert("100", "块", "片") == Decimal("100")


def test_tc013_unit_individual_normalizes_to_piece():
    from services import unit_system as units
    assert units.compatible("个", "片") and units.convert("30", "个", "片") == Decimal("30")


def test_tc014_unit_package_piece_normalizes_to_piece():
    from services import unit_system as units
    assert units.compatible("件", "片") and units.convert("7", "件", "片") == Decimal("7")


def test_tc015_unit_pcs_is_case_insensitive():
    from services import unit_system as units
    assert units.spec("PCS").name == "片" and units.compatible("pcs", "片")


def test_tc016_unit_set_requires_item_specific_rule():
    from services import unit_system as units
    assert units.compatible("套", "片") is False
    assert "物料专属换算规则" in units.conflict_reason("套", "片")


def test_tc017_unit_machine_requires_item_specific_rule():
    from services import unit_system as units
    assert units.compatible("台", "片") is False
    assert units.conflict_reason("台", "片")


def test_tc018_weight_kilogram_and_gram_convert_exactly():
    from services import unit_system as units
    assert units.convert("100", "公斤", "克") == Decimal("100000")
    assert units.convert("2000", "克", "公斤") == Decimal("2")
    assert units.difference("100", "公斤", "2000", "克") == Decimal("98000")


def test_tc019_weight_jin_conversion_is_exact():
    from services import unit_system as units
    assert units.convert("1", "斤", "克") == Decimal("500")
    assert units.convert("3", "斤", "公斤") == Decimal("1.5")
    assert units.convert("1", "公斤", "斤") == Decimal("2")


def test_tc020_weight_ton_conversion_is_exact():
    from services import unit_system as units
    assert units.convert("1", "吨", "克") == Decimal("1000000")
    assert units.convert("2", "吨", "公斤") == Decimal("2000")


def test_tc021_length_meter_and_millimeter():
    from services import unit_system as units
    assert units.convert("1", "米", "毫米") == Decimal("1000")
    assert units.convert("2", "米", "厘米") == Decimal("200")


def test_tc022_length_centimeter_and_millimeter():
    from services import unit_system as units
    assert units.convert("50", "厘米", "毫米") == Decimal("500")
    assert units.convert("50", "厘米", "米") == Decimal("0.5")


def test_tc023_length_inch_conversion_is_exact():
    from services import unit_system as units
    assert units.convert("2", "英寸", "毫米") == Decimal("50.8")
    assert units.convert("12", "英寸", "英尺") == Decimal("1")


def test_tc024_missing_unit_defaults_to_piece_with_marker():
    unit, source = _unit_module().resolve_missing(None)
    assert (unit, source) == ("片", "default_piece")
    assert _unit_module().resolve_missing("公斤") == ("公斤", "explicit")


def test_tc025_explicit_weight_survives_default_and_decimal_is_exact():
    from services import unit_system as units
    unit, source = units.resolve_missing("公斤")
    assert (unit, source) == ("公斤", "explicit")
    assert units.convert("0.1", "公斤", "克") + units.convert("0.2", "公斤", "克") == Decimal("300")
    assert units.difference("0.3", "公斤", "300", "克") == Decimal("0")


def _unit_module():
    from services import unit_system
    return unit_system


# ================================================================ C. 生产数量与上下文

def test_tc026_valid_quantity_hundred_pieces(public_client):
    sid = new_session(public_client, "KQ-Z112").json()["session_id"]
    answer = ask(public_client, sid, "生产100片可以吗")
    data = data_of(answer)
    assert answer["status"] == "ok"
    assert data["requested_quantity"] == 100 and data["requested_unit"] == "片"


def test_tc027_zero_quantity_is_rejected(public_client):
    sid = new_session(public_client, "KQ-Z112").json()["session_id"]
    answer = ask(public_client, sid, "生产0片")
    assert answer["status"] == "invalid_query" and data_of(answer) == {}


def test_tc028_negative_quantity_is_rejected(public_client):
    sid = new_session(public_client, "KQ-Z112").json()["session_id"]
    answer = ask(public_client, sid, "生产-5片")
    assert answer["status"] == "invalid_query"
    assert answer["structured_data"]["validation_error"] == "quantity_must_be_positive_integer"


def test_tc029_fraction_quantity_is_rejected(public_client):
    sid = new_session(public_client, "KQ-Z112").json()["session_id"]
    assert ask(public_client, sid, "生产1/2片")["status"] == "invalid_query"


def test_tc030_decimal_quantity_is_rejected(public_client):
    sid = new_session(public_client, "KQ-Z112").json()["session_id"]
    assert ask(public_client, sid, "生产1.5片")["status"] == "invalid_query"


def test_tc031_scientific_notation_is_rejected(public_client):
    sid = new_session(public_client, "KQ-Z112").json()["session_id"]
    assert ask(public_client, sid, "生产1e3片")["status"] == "invalid_query"


def test_tc032_missing_unit_defaults_to_piece(public_client):
    sid = new_session(public_client, "KQ-Z112").json()["session_id"]
    answer = ask(public_client, sid, "生产100")
    data = data_of(answer)
    assert answer["status"] == "ok"
    assert data["requested_unit"] == "片" and data["requested_unit_source"] == "default_piece"


def test_tc033_latest_quantity_replaces_previous_target(public_client):
    sid = new_session(public_client, "KQ-Z112").json()["session_id"]
    ask(public_client, sid, "生产100片可以吗")
    ask(public_client, sid, "生产50片可以吗")
    answer = ask(public_client, sid, "帮我生成采购建议")
    assert data_of(answer)["requested_quantity"] == 50


def test_tc034_invalid_new_demand_does_not_revive_old_quantity(public_client):
    sid = new_session(public_client, "KQ-Z112").json()["session_id"]
    ask(public_client, sid, "生产100片可以吗")
    assert ask(public_client, sid, "生产0片")["status"] == "invalid_query"
    answer = ask(public_client, sid, "帮我生成采购建议")
    assert answer["status"] == "needs_input"
    assert "quantity" in answer["structured_data"]["missing_parameters"]
    assert data_of(answer) == {}


def test_tc035_switching_product_does_not_inherit_other_demand(public_client):
    sid_a = new_session(public_client, "KQ-Z112").json()["session_id"]
    ask(public_client, sid_a, "生产100片可以吗")
    sid_b = new_session(public_client, "KQ-BHX3588M").json()["session_id"]
    answer = ask(public_client, sid_b, "生产2片可以吗")
    assert data_of(answer)["requested_quantity"] == 2
    assert data_of(answer)["product"] == "KQ-BHX3588M"


# ================================================================ D. 库存质量与采购

def test_tc036_zero_stock_is_computable_but_missing_record_is_unknown():
    book = synthetic_book([("SYN-A", "2", "片"), ("SYN-B", "1", "片")],
                          {"SYN-A": [lot("SYN-A", "0")]}, quantity_confirmation=CONFIRMED)
    data = book.analyze("SYN-P", quantity=10)["data"]
    zero = next(r for r in data["materials"] if r["material_code"] == "SYN-A")
    missing = next(r for r in data["materials"] if r["material_code"] == "SYN-B")
    assert zero["comparison_status"] == "comparable" and zero["book_shortage_qty"] == "20"
    assert missing["comparison_status"] == "no_stock" and missing["suggested_purchase_qty"] is None


def test_tc037_missing_record_never_becomes_definite_purchase():
    book = synthetic_book([("SYN-B", "1", "片")], {}, quantity_confirmation=CONFIRMED)
    response = synthetic_queries(book)._shortage("生产10片哪些物料缺料", {}, [], "SYN-P", 10).response
    assert "未生成确定采购数量" in response["answer"]
    assert "需按需求数量采购" not in response["answer"]
    assert response["structured_data"]["data"]["purchase_material_count"] == 0


def test_tc038_unknown_quantity_is_unverified():
    book = synthetic_book([("SYN-A", "1", "片")], {"SYN-A": [lot("SYN-A", None)]},
                          quantity_confirmation=CONFIRMED)
    row = book.analyze("SYN-P", quantity=10)["data"]["materials"][0]
    assert row["comparison_status"] == "unverified_quantity"
    assert row["suggested_purchase_qty"] is None


def test_tc039_verified_duplicates_merge_by_max():
    records = {"SYN-A": [lot("SYN-A", "10", duplicate=True, location="L1"),
                         lot("SYN-A", "5", duplicate=True, location="L1")]}
    book = synthetic_book([("SYN-A", "1", "片")], records, quantity_confirmation=CONFIRMED)
    row = book.analyze("SYN-P", quantity=20)["data"]["materials"][0]
    assert row["duplicate_verified"] is True and row["recorded_stock_qty"] == "10"
    assert row["book_shortage_qty"] == "10"


def test_tc040_unverified_duplicates_are_not_merged_or_purchased():
    records = {"SYN-A": [lot("SYN-A", "10", duplicate=True, location="L1"),
                         lot("SYN-A", "5", duplicate=True, location="L1")]}
    book = synthetic_book([("SYN-A", "1", "片")], records)
    data = book.analyze("SYN-P", quantity=20)["data"]
    row = data["materials"][0]
    assert row["comparison_status"] == "duplicate_unverified"
    assert row["recorded_stock_qty"] is None and row["suggested_purchase_qty"] is None
    assert data["purchase_material_count"] == 0


def test_tc041_conflicting_units_inside_one_material_block_calculation():
    records = {"SYN-A": [lot("SYN-A", "10", "片", batch="B1"), lot("SYN-A", "5", "公斤", batch="B2")]}
    book = synthetic_book([("SYN-A", "1", "片")], records, quantity_confirmation=CONFIRMED)
    row = book.analyze("SYN-P", quantity=10)["data"]["materials"][0]
    assert row["comparison_status"] == "unit_unknown"
    assert "不一致" in row["uncomputable_reason"]


def test_tc042_weight_stock_against_piece_demand_is_not_computed():
    book = synthetic_book([("SYN-A", "2", "片")], {"SYN-A": [lot("SYN-A", "30", "公斤")]},
                          quantity_confirmation=CONFIRMED)
    data = book.analyze("SYN-P", quantity=100)["data"]
    row = data["materials"][0]
    assert row["comparison_status"] == "unit_conflict"
    assert row["book_shortage_qty"] is None and row["suggested_purchase_qty"] is None
    assert data["unit_conflict_materials"] == ["SYN-A"]
    assert data["inventory_sufficient_for_requested"] is None


def test_tc043_missing_stock_unit_is_unknown():
    book = synthetic_book([("SYN-A", "2", "片")], {"SYN-A": [lot("SYN-A", "30", None)]},
                          unit_confirmation=None)
    data = book.analyze("SYN-P", quantity=10)["data"]
    assert data["materials"][0]["comparison_status"] == "unit_unknown"
    assert data["unit_unknown_materials"] == ["SYN-A"]
    assert data["purchase_material_count"] == 0


def test_tc044_partial_bom_purchases_only_computable_gaps():
    book = synthetic_book([("SYN-A", "1", "片"), ("SYN-B", "1", "片"), ("SYN-C", "1", "片")],
                          {"SYN-A": [lot("SYN-A", "2")], "SYN-C": [lot("SYN-C", "5", "公斤")]},
                          quantity_confirmation=CONFIRMED)
    response = synthetic_queries(book)._production_plan("投产10片生成采购BOM表单", {}, [], "SYN-P", 10).response
    data = response["structured_data"]["data"]
    assert [r["material"] for r in data["recommendations"]] == ["SYN-A"]
    assert data["recommendations"][0]["suggested_purchase_qty"] == "8"
    assert {r["material"] for r in data["unverified_purchase_materials"]} == {"SYN-B", "SYN-C"}


def test_tc045_purchase_count_matches_recommendation_rows():
    book = synthetic_book([("SYN-A", "1", "片")], {"SYN-A": [lot("SYN-A", "2")]},
                          quantity_confirmation=CONFIRMED)
    data = book.analyze("SYN-P", quantity=10)["data"]
    assert len(data["recommendations"]) == data["purchase_material_count"] == 1
    assert all(r["suggested_purchase_qty"] for r in data["recommendations"])


def test_tc046_answer_count_equals_unverified_count():
    book = synthetic_book([("SYN-A", "1", "片"), ("SYN-B", "1", "片")],
                          {"SYN-A": [lot("SYN-A", "2")]}, quantity_confirmation=CONFIRMED)
    response = synthetic_queries(book)._production_plan("投产10片生成采购BOM表单", {}, [], "SYN-P", 10).response
    data = response["structured_data"]["data"]
    count = len(data["unverified_purchase_materials"])
    assert count == data["unverified_purchase_count"] == 1
    assert f"{count}种物料未生成确定缺口与采购数量" in response["answer"]


def test_tc047_shortage_answer_never_claims_unknown_purchase():
    book = synthetic_book([("SYN-B", "1", "片")], {}, quantity_confirmation=CONFIRMED)
    answer = synthetic_queries(book)._shortage("生产10片哪些物料缺料", {}, [], "SYN-P", 10).response["answer"]
    assert "无记录" not in answer or "未生成确定采购数量" in answer
    assert "需按需求数量采购" not in answer


def test_tc048_recommendations_declare_missing_moq_fields():
    book = synthetic_book([("SYN-A", "1", "片")], {"SYN-A": [lot("SYN-A", "2")]},
                          quantity_confirmation=CONFIRMED)
    data = book.analyze("SYN-P", quantity=10)["data"]
    missing = set(data["recommendations"][0]["missing_fields"])
    assert {"MOQ", "order_multiple", "lead_time_days", "price"} <= missing


def test_tc049_quantity_check_status_reflects_shortage():
    book = synthetic_book([("SYN-A", "1", "片")], {"SYN-A": [lot("SYN-A", "2")]},
                          quantity_confirmation=CONFIRMED)
    data = book.analyze("SYN-P", quantity=10)["data"]
    assert data["quantity_check_status"] == "insufficient"
    assert data["inventory_sufficient_for_requested"] is False


def test_tc050_uncomputable_material_never_enters_recommendations():
    book = synthetic_book([("SYN-A", "1", "片"), ("SYN-C", "1", "片")],
                          {"SYN-A": [lot("SYN-A", "2")], "SYN-C": [lot("SYN-C", "5", "公斤")]},
                          quantity_confirmation=CONFIRMED)
    data = book.analyze("SYN-P", quantity=10)["data"]
    recommended = {r["material"] for r in data["recommendations"]}
    assert recommended == {"SYN-A"}
    assert {"SYN-C"} & recommended == set()
    assert data["uncomputable_material_count"] == 1


# ================================================================ E. 预览、确认与模拟执行

def test_tc051_preview_does_not_deduct(simulation):
    client, app = simulation.client, simulation.app
    sid, answer, data = preview_of(client, quantity=3)
    code = data["materials"][0]["material_code"]
    before = stock(app, code)["on_hand_qty"]
    assert answer["status"] == "ok" and data["preview_id"]
    assert data["executed"] is False and data["materials"]
    assert stock(app, code)["on_hand_qty"] == before


def test_tc052_preview_lists_materials_batches_and_expected_quantities(simulation):
    client = simulation.client
    sid, _, data = preview_of(client)
    row = data["materials"][0]
    assert Decimal(row["required_qty"]) > 0
    assert row["allocations"] and {"batch_no", "received_at", "allocated_qty", "available_before"} <= set(row["allocations"][0])
    assert row["fifo_status"] == "ok"


def test_tc053_unconfirmed_execution_is_rejected(simulation):
    client, app = simulation.client, simulation.app
    sid, _, data = preview_of(client)
    before = stock(app, data["materials"][0]["material_code"])["on_hand_qty"]
    response = execute(client, sid, data["preview_id"], confirmed=False)
    assert response.json()["status"] == "confirmation_required"
    assert stock(app, data["materials"][0]["material_code"])["on_hand_qty"] == before


def test_tc054_confirmed_execution_deducts_exactly_approved_quantity(simulation):
    client, app = simulation.client, simulation.app
    sid, _, data = preview_of(client, quantity=5)
    expected = {row["material_code"]: sum(Decimal(a["allocated_qty"]) for a in row["allocations"])
                for row in data["materials"] if row["allocations"]}
    before = {code: stock(app, code)["on_hand_qty"] for code in expected}
    response = execute(client, sid, data["preview_id"])
    result = response.json()["structured_data"]["data"]
    assert response.status_code == 200 and result["executed"] is True
    for code, amount in expected.items():
        assert Decimal(stock(app, code)["on_hand_qty"]) == Decimal(before[code]) - amount


def test_tc055_expired_preview_cannot_execute(tmp_path):
    ledger, service = execution_fixture(tmp_path, {"SYN-A": [ledger_lot("SYN-A", "B1", "10")]}, lifetime=-1)
    plan = service.preview("simulation", "owner", "session", "K2", 1, demands(("SYN-A", "2", "片")),
                           execution_authorized=True)
    result = service.execute(plan["data"]["preview_id"], "owner", "session", confirmed=True)
    assert result["status"] == "preview_expired"
    assert ledger.stock("simulation", "SYN-A")["on_hand_qty"] == "10"


def test_tc056_stale_preview_after_stock_version_change(simulation):
    client, app = simulation.client, simulation.app
    sid_a, _, data_a = preview_of(client, quantity=1)
    sid_b, _, data_b = preview_of(client, quantity=1)
    assert execute(client, sid_a, data_a["preview_id"]).json()["structured_data"]["data"]["executed"] is True
    stale = execute(client, sid_b, data_b["preview_id"])
    assert stale.json()["status"] == "stale_preview"
    assert stale.json()["structured_data"]["data"]["executed"] is False


def test_tc057_incompatible_unit_demand_cannot_execute(tmp_path):
    ledger, service = execution_fixture(tmp_path, {"SYN-A": [ledger_lot("SYN-A", "B1", "10", inventory_unit="片")]})
    plan = service.preview("simulation", "owner", "s", "K2", 1, demands(("SYN-A", "2", "公斤")),
                           execution_authorized=True)
    assert plan["data"]["executable"] is False and plan["data"]["can_execute"] is False
    result = service.execute(plan["data"]["preview_id"], "owner", "s", confirmed=True)
    assert result["status"] == "data_unavailable"
    assert ledger.stock("simulation", "SYN-A")["on_hand_qty"] == "10"


def test_tc058_request_without_trusted_identity_is_denied(sim_client):
    response = sim_client.post("/api/v1/inventory/execute",
                               json={"session_id": "unknown-session", "preview_id": "x", "confirmed": True})
    assert response.status_code == 403
    assert response.json()["code"] == "INVENTORY_ACCESS_DENIED"


def test_tc059_success_creates_durable_receipt(simulation):
    client, app = simulation.client, simulation.app
    sid, _, data = preview_of(client)
    result = execute(client, sid, data["preview_id"]).json()["structured_data"]["data"]
    receipt = client.get(f"/api/v1/inventory/receipts/{data['preview_id']}?session_id={sid}")
    assert receipt.status_code == 200
    assert receipt.json()["structured_data"]["data"]["executed"] is True
    assert receipt.json()["structured_data"]["data"]["task_id"] == result["task_id"]


def test_tc060_receipt_quantities_match_ledger_movements(simulation):
    client, app = simulation.client, simulation.app
    sid, _, data = preview_of(client)
    result = execute(client, sid, data["preview_id"]).json()["structured_data"]["data"]
    movements = app.state.inventory_runtime.ledger.movements("simulation")
    assert len(movements) == len(result["movements"])
    assert sum(Decimal(m["deducted_qty"]) for m in movements) == sum(
        Decimal(m["allocated_qty"]) for m in result["movements"])
    for movement in movements:
        assert Decimal(movement["before_qty"]) - Decimal(movement["deducted_qty"]) == Decimal(movement["after_qty"])


def test_tc061_repeat_confirmation_returns_same_receipt(simulation):
    client, app = simulation.client, simulation.app
    sid, _, data = preview_of(client)
    first = execute(client, sid, data["preview_id"])
    snapshot = {code: stock(app, code)["on_hand_qty"] for code in [r["material_code"] for r in data["materials"]]}
    second = execute(client, sid, data["preview_id"])
    assert second.json()["structured_data"]["data"] == first.json()["structured_data"]["data"]
    assert {code: stock(app, code)["on_hand_qty"] for code in snapshot} == snapshot
    assert len(execution_turns(client, sid)) == 1


def test_tc062_other_session_cannot_read_receipt(simulation):
    client = simulation.client
    sid, _, data = preview_of(client)
    execute(client, sid, data["preview_id"])
    other = new_session(client, "KQ-Z112").json()["session_id"]
    response = client.get(f"/api/v1/inventory/receipts/{data['preview_id']}?session_id={other}")
    assert response.status_code == 403


def test_tc063_unknown_preview_is_refused(sim_client):
    sid = new_session(sim_client, "KQ-Z112").json()["session_id"]
    response = execute(sim_client, sid, "no-such-preview")
    assert response.status_code == 403
    assert response.json()["code"] == "INVENTORY_ACCESS_DENIED"


def test_tc064_real_http_preview_execute_receipt(simulation):
    with live_server(simulation.app) as base:
        status, catalog = http_json(base + "/api/v1/agent/catalog")
        assert status == 200 and len(catalog["products"]) == 2
        status, created = http_json(base + "/api/v1/agent/sessions", {"product_code": "KQ-Z112"})
        sid = created["session_id"]
        status, answer = http_json(base + "/api/v1/agent/query",
                                   {"message": "生产3片并扣减库存", "session_id": sid, "persist_session": True})
        data = answer["structured_data"]["data"]
        assert data["can_execute"] is True and data["execution_available"] is True
        status, executed = http_json(base + "/api/v1/inventory/execute",
                                     {"session_id": sid, "preview_id": data["preview_id"], "confirmed": True})
        assert status == 200 and executed["structured_data"]["data"]["executed"] is True
        status, receipt = http_json(base + f"/api/v1/inventory/receipts/{data['preview_id']}?session_id={sid}")
        assert status == 200 and receipt["structured_data"]["data"]["executed"] is True


def test_tc065_restart_keeps_receipt_without_second_deduction(simulation):
    import simulation_server as sim
    client, app, root = simulation.client, simulation.app, simulation.root
    sid, _, data = preview_of(client, quantity=3)
    first = execute(client, sid, data["preview_id"]).json()["structured_data"]["data"]
    after = {row["material_code"]: stock(app, row["material_code"])["on_hand_qty"]
             for row in data["materials"]}
    restarted = sim.create_simulation_app(root)
    try:
        from fastapi.testclient import TestClient
        with TestClient(restarted) as again:
            receipt = again.get(f"/api/v1/inventory/receipts/{data['preview_id']}?session_id={sid}")
            assert receipt.status_code == 200
            assert receipt.json()["structured_data"]["data"] == first
            repeat = again.post("/api/v1/inventory/execute",
                                json={"session_id": sid, "preview_id": data["preview_id"], "confirmed": True})
            assert repeat.json()["structured_data"]["data"] == first
            now = {code: restarted.state.inventory_runtime.ledger.stock("simulation", code)["on_hand_qty"]
                   for code in after}
            assert now == after
    finally:
        restarted.state.simulation_restore()


# ================================================================ F. 守恒、批次与 FIFO

def test_tc066_single_lot_deduction(tmp_path):
    ledger, service = execution_fixture(tmp_path, {"SYN-A": [ledger_lot("SYN-A", "B1", "10")]})
    plan = service.preview("simulation", "o", "s", "K2", 1, demands(("SYN-A", "4", "片")), execution_authorized=True)
    assert service.execute(plan["data"]["preview_id"], "o", "s", confirmed=True)["status"] == "ok"
    assert ledger.stock("simulation", "SYN-A")["on_hand_qty"] == "6"


def test_tc067_multi_lot_material_deducts_across_batches(tmp_path):
    records = {"SYN-A": [ledger_lot("SYN-A", "B1", "3"), ledger_lot("SYN-A", "B2", "7", "2024-03-01T00:00:00+00:00")]}
    ledger, service = execution_fixture(tmp_path, records)
    plan = service.preview("simulation", "o", "s", "K2", 1, demands(("SYN-A", "5", "片")), execution_authorized=True)
    allocations = plan["data"]["materials"][0]["allocations"]
    assert [a["batch_no"] for a in allocations] == ["B1", "B2"]
    assert [a["allocated_qty"] for a in allocations] == ["3", "2"]
    service.execute(plan["data"]["preview_id"], "o", "s", confirmed=True)
    assert ledger.stock("simulation", "SYN-A")["on_hand_qty"] == "5"


def test_tc068_fifo_prefers_earlier_received_batch(tmp_path):
    records = {"SYN-A": [ledger_lot("SYN-A", "LATE", "5", "2024-09-01T00:00:00+00:00"),
                         ledger_lot("SYN-A", "EARLY", "5", "2024-02-01T00:00:00+00:00")]}
    _, service = execution_fixture(tmp_path, records)
    plan = service.preview("simulation", "o", "s", "K2", 1, demands(("SYN-A", "4", "片")), execution_authorized=True)
    allocations = plan["data"]["materials"][0]["allocations"]
    assert allocations[0]["batch_no"] == "EARLY"


def test_tc069_demand_above_stock_is_not_executable(tmp_path):
    ledger, service = execution_fixture(tmp_path, {"SYN-A": [ledger_lot("SYN-A", "B1", "4")]})
    plan = service.preview("simulation", "o", "s", "K2", 1, demands(("SYN-A", "10", "片")), execution_authorized=True)
    assert plan["data"]["executable"] is False
    assert service.execute(plan["data"]["preview_id"], "o", "s", confirmed=True)["status"] == "data_unavailable"
    assert ledger.stock("simulation", "SYN-A")["on_hand_qty"] == "4"


def test_tc070_duplicate_demands_merge_before_fifo(tmp_path):
    ledger, service = execution_fixture(tmp_path, {"SYN-A": [ledger_lot("SYN-A", "B1", "10")]})
    plan = service.preview("simulation", "o", "s", "K2", 1,
                           demands(("SYN-A", "2", "片"), ("SYN-A", "4", "片")), execution_authorized=True)
    assert len(plan["data"]["requirements"]) == 1
    assert plan["data"]["requirements"][0]["required_qty"] == "6"
    service.execute(plan["data"]["preview_id"], "o", "s", confirmed=True)
    assert ledger.stock("simulation", "SYN-A")["on_hand_qty"] == "4"


def test_tc071_total_deducted_equals_approved_demand(tmp_path):
    records = {"SYN-A": [ledger_lot("SYN-A", "B1", "4"), ledger_lot("SYN-A", "B2", "6", "2024-04-01T00:00:00+00:00")]}
    ledger, service = execution_fixture(tmp_path, records)
    plan = service.preview("simulation", "o", "s", "K2", 1, demands(("SYN-A", "6", "片")), execution_authorized=True)
    result = service.execute(plan["data"]["preview_id"], "o", "s", confirmed=True)
    assert sum(Decimal(m["allocated_qty"]) for m in result["data"]["movements"]) == Decimal("6")
    assert ledger.stock("simulation", "SYN-A")["on_hand_qty"] == "4"


def test_tc072_never_negative_stock(tmp_path):
    records = {"SYN-A": [ledger_lot("SYN-A", "B1", "5")]}
    ledger, service = execution_fixture(tmp_path, records)
    for quantity in ("5", "6"):
        plan = service.preview("simulation", "o", "s" + quantity, "K2", 1, demands(("SYN-A", quantity, "片")),
                               execution_authorized=True)
        service.execute(plan["data"]["preview_id"], "o", "s" + quantity, confirmed=True)
    assert Decimal(ledger.stock("simulation", "SYN-A")["on_hand_qty"]) >= 0


def test_tc073_invalid_batch_date_blocks_execution(tmp_path):
    records = {"SYN-A": [ledger_lot("SYN-A", "B1", "10", received="not-a-date")]}
    ledger, service = execution_fixture(tmp_path, records)
    plan = service.preview("simulation", "o", "s", "K2", 1, demands(("SYN-A", "2", "片")), execution_authorized=True)
    assert plan["data"]["executable"] is False
    assert service.execute(plan["data"]["preview_id"], "o", "s", confirmed=True)["status"] == "data_unavailable"
    assert ledger.stock("simulation", "SYN-A")["on_hand_qty"] == "10"


def test_tc074_fifo_allocation_is_traceable(tmp_path):
    records = {"SYN-A": [ledger_lot("SYN-A", "B1", "3"), ledger_lot("SYN-A", "B2", "7", "2024-05-01T00:00:00+00:00")]}
    _, service = execution_fixture(tmp_path, records)
    plan = service.preview("simulation", "o", "s", "K2", 1, demands(("SYN-A", "5", "片")), execution_authorized=True)
    allocations = plan["data"]["materials"][0]["allocations"]
    assert all(a.get("inventory_lot") for a in allocations)
    assert all(a.get("received_at") for a in allocations)
    assert [a["received_at"] for a in allocations] == sorted(a["received_at"] for a in allocations)


def test_tc075_stock_movements_and_receipt_are_consistent(tmp_path):
    records = {"SYN-A": [ledger_lot("SYN-A", "B1", "4"), ledger_lot("SYN-A", "B2", "6", "2024-07-01T00:00:00+00:00")],
               "SYN-B": [ledger_lot("SYN-B", "C1", "5")]}
    ledger, service = execution_fixture(tmp_path, records)
    plan = service.preview("simulation", "o", "s", "K2", 1,
                           demands(("SYN-A", "4", "片"), ("SYN-B", "2", "片")), execution_authorized=True)
    result = service.execute(plan["data"]["preview_id"], "o", "s", confirmed=True)
    movements = ledger.movements("simulation")
    assert len(movements) == len(result["data"]["movements"])
    for stock_row in result["data"]["stocks"]:
        deducted = sum(Decimal(m["deducted_qty"]) for m in movements if m["material_code"] == stock_row["material_code"])
        assert Decimal(stock_row["on_hand_qty"]) == Decimal(stock_row["recorded_stock_qty"])
        assert deducted >= 0


# ================================================================ G. 幂等、并发与事务

def test_tc076_same_request_twice_deducts_once(tmp_path):
    ledger, service = execution_fixture(tmp_path, {"SYN-A": [ledger_lot("SYN-A", "B1", "10")]})
    plan = service.preview("simulation", "o", "s", "K2", 1, demands(("SYN-A", "3", "片")), execution_authorized=True)
    pid = plan["data"]["preview_id"]
    first = service.execute(pid, "o", "s", confirmed=True)
    second = service.execute(pid, "o", "s", confirmed=True)
    assert first == second
    assert ledger.stock("simulation", "SYN-A")["on_hand_qty"] == "7"


def test_tc077_concurrent_same_preview_deducts_once(tmp_path):
    ledger, service = execution_fixture(tmp_path, {"SYN-A": [ledger_lot("SYN-A", "B1", "10")]})
    pid = service.preview("simulation", "o", "s", "K2", 1, demands(("SYN-A", "3", "片")),
                          execution_authorized=True)["data"]["preview_id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.execute(pid, "o", "s", confirmed=True), range(4)))
    assert all(r["status"] == "ok" for r in results)
    assert ledger.stock("simulation", "SYN-A")["on_hand_qty"] == "7"
    assert len(ledger.movements("simulation")) == 1


def test_tc078_concurrent_api_confirmation_keeps_one_audit_turn(simulation, monkeypatch):
    client, app = simulation.client, simulation.app
    sid, _, data = preview_of(client, quantity=2)
    repository = app.state.simulation_repository
    original = repository.get_session

    def slow(session_id):
        time.sleep(0.05)
        return original(session_id)

    monkeypatch.setattr(repository, "get_session", slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: execute(client, sid, data["preview_id"]), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert len(execution_turns(client, sid)) == 1
    assert {r.json()["turn_id"] for r in responses} == {execution_turns(client, sid)[0]["turn_id"]}


def test_tc079_two_previews_contending_for_stock_accepts_only_one(simulation):
    client, app = simulation.client, simulation.app
    sid_a, _, data_a = preview_of(client, quantity=3)
    sid_b, _, data_b = preview_of(client, quantity=3)
    first = execute(client, sid_a, data_a["preview_id"])
    second = execute(client, sid_b, data_b["preview_id"])
    assert first.json()["structured_data"]["data"]["executed"] is True
    assert second.json()["status"] == "stale_preview"
    assert second.json()["structured_data"]["data"]["executed"] is False


def test_tc080_mid_transaction_failure_rolls_back_balances(tmp_path):
    ledger, service = execution_fixture(tmp_path, {"SYN-A": [ledger_lot("SYN-A", "B1", "10")]})
    pid = service.preview("simulation", "o", "s", "K2", 1, demands(("SYN-A", "3", "片")),
                          execution_authorized=True)["data"]["preview_id"]
    original = ledger.stock
    calls = {"n": 0}

    def flaky(namespace, code, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("synthetic failure")
        return original(namespace, code, **kwargs)

    with patch.object(ledger, "stock", side_effect=flaky):
        with pytest.raises(RuntimeError):
            service.execute(pid, "o", "s", confirmed=True)
    assert ledger.stock("simulation", "SYN-A")["on_hand_qty"] == "10"
    assert ledger.movements("simulation") == []


def test_tc081_movement_write_failure_rolls_back_everything(tmp_path):
    ledger, service = execution_fixture(tmp_path, {"SYN-A": [ledger_lot("SYN-A", "B1", "10")]})
    pid = service.preview("simulation", "o", "s", "K2", 1, demands(("SYN-A", "3", "片")),
                          execution_authorized=True)["data"]["preview_id"]
    with failing_transaction(ledger, "INSERT INTO ledger_movements"):
        with pytest.raises(RuntimeError):
            service.execute(pid, "o", "s", confirmed=True)
    assert ledger.stock("simulation", "SYN-A")["on_hand_qty"] == "10"
    assert ledger.movements("simulation") == []
    with ledger.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ledger_executions").fetchone()[0] == 0


def test_tc082_history_failure_then_repair_keeps_single_execution(simulation, monkeypatch):
    client, app = simulation.client, simulation.app
    sid, _, data = preview_of(client, quantity=2)
    repository = app.state.simulation_repository
    original = repository.append_turn

    def broken(*args, **kwargs):
        raise RuntimeError("history unavailable")

    monkeypatch.setattr(repository, "append_turn", broken)
    failed = execute(client, sid, data["preview_id"])
    assert failed.status_code == 503
    assert failed.json()["code"] == "INVENTORY_RECEIPT_PENDING"
    receipt = client.get(f"/api/v1/inventory/receipts/{data['preview_id']}?session_id={sid}")
    assert receipt.json()["structured_data"]["data"]["executed"] is True
    monkeypatch.setattr(repository, "append_turn", original)
    repaired = execute(client, sid, data["preview_id"])
    assert repaired.status_code == 200
    assert len(execution_turns(client, sid)) == 1


def test_tc083_same_task_has_no_duplicate_success_audit(simulation):
    client, app = simulation.client, simulation.app
    sid, _, data = preview_of(client, quantity=2)
    for _ in range(3):
        execute(client, sid, data["preview_id"])
    turns = execution_turns(client, sid)
    assert len(turns) == 1
    task_ids = {t["response"]["structured_data"]["data"]["task_id"] for t in turns}
    assert task_ids == {data["preview_id"]}


def test_tc084_concurrent_different_tasks_do_not_over_deduct(simulation):
    client, app = simulation.client, simulation.app
    sessions = [new_session(client, "KQ-Z112").json()["session_id"] for _ in range(3)]
    previews, code = [], None
    for sid in sessions:
        answer = ask(client, sid, "生产1片并扣减库存")
        data = data_of(answer)
        code = code or data["materials"][0]["material_code"]
        previews.append((sid, data["preview_id"]))
    before = Decimal(stock(app, code)["on_hand_qty"])
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda pair: execute(client, pair[0], pair[1]), previews))
    executed = [r for r in results if r.status_code == 200
                and r.json()["structured_data"]["data"].get("executed") is True]
    after = Decimal(stock(app, code)["on_hand_qty"])
    assert before - after >= 0
    assert len(executed) >= 1
    deducted = sum(Decimal(m["deducted_qty"]) for m in app.state.inventory_runtime.ledger.movements("simulation")
                   if m["material_code"] == code)
    assert before - after == deducted


def test_tc085_failed_execution_leaves_no_execution_row(tmp_path):
    ledger, service = execution_fixture(tmp_path, {"SYN-A": [ledger_lot("SYN-A", "B1", "10")]})
    pid = service.preview("simulation", "o", "s", "K2", 1, demands(("SYN-A", "3", "片")),
                          execution_authorized=True)["data"]["preview_id"]
    with failing_transaction(ledger, "INSERT INTO ledger_executions"):
        with pytest.raises(RuntimeError):
            service.execute(pid, "o", "s", confirmed=True)
    with ledger.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ledger_executions").fetchone()[0] == 0
    assert ledger.stock("simulation", "SYN-A")["on_hand_qty"] == "10"
    assert service.receipt(pid, "o", "s")["status"] == "data_unavailable"


# ================================================================ H. 回执、会话与重置

def test_tc086_success_receipt_is_queryable(simulation):
    client = simulation.client
    sid, _, data = preview_of(client, quantity=2)
    execute(client, sid, data["preview_id"])
    receipt = client.get(f"/api/v1/inventory/receipts/{data['preview_id']}?session_id={sid}")
    payload = receipt.json()["structured_data"]
    assert receipt.status_code == 200 and payload["operation"] == "inventory_execution"
    assert payload["data"]["executed"] is True


def test_tc087_receipt_before_execution_is_data_unavailable(simulation):
    client = simulation.client
    sid, _, data = preview_of(client, quantity=2)
    receipt = client.get(f"/api/v1/inventory/receipts/{data['preview_id']}?session_id={sid}")
    assert receipt.status_code == 200
    assert receipt.json()["status"] == "data_unavailable"
    assert receipt.json()["structured_data"]["data"]["executed"] is False


def test_tc088_repeated_request_returns_original_receipt(simulation):
    client = simulation.client
    sid, _, data = preview_of(client)
    first = execute(client, sid, data["preview_id"]).json()
    second = execute(client, sid, data["preview_id"]).json()
    assert first["structured_data"]["data"] == second["structured_data"]["data"]
    assert first["turn_id"] == second["turn_id"]


def test_tc089_session_state_does_not_leak_to_another_session(simulation):
    client, app = simulation.client, simulation.app
    sid_a, _, data_a = preview_of(client, quantity=2)
    execute(client, sid_a, data_a["preview_id"])
    sid_b = new_session(client, "KQ-BHX3588M").json()["session_id"]
    answer = ask(client, sid_b, "生产1片并扣减库存")
    assert answer["status"] == "ok"
    assert data_of(answer)["session_id"] == sid_b
    assert data_of(answer)["product"] == "KQ-BHX3588M"


def test_tc090_original_session_keeps_execution_evidence(simulation):
    client = simulation.client
    sid, _, data = preview_of(client, quantity=2)
    execute(client, sid, data["preview_id"])
    turns = history(client, sid)
    assert [t["response"]["structured_data"]["operation"] for t in turns] == [
        "inventory_preview", "inventory_execution"]
    assert turns[-1]["response"]["structured_data"]["data"]["task_id"] == data["preview_id"]


def test_tc091_restart_recovers_receipt(simulation):
    import simulation_server as sim
    client, root = simulation.client, simulation.root
    sid, _, data = preview_of(client, quantity=2)
    original = execute(client, sid, data["preview_id"]).json()["structured_data"]["data"]
    restarted = sim.create_simulation_app(root)
    try:
        from fastapi.testclient import TestClient
        with TestClient(restarted) as again:
            receipt = again.get(f"/api/v1/inventory/receipts/{data['preview_id']}?session_id={sid}")
            assert receipt.status_code == 200
            assert receipt.json()["structured_data"]["data"] == original
    finally:
        restarted.state.simulation_restore()


def test_tc092_restart_does_not_double_deduct(simulation):
    import simulation_server as sim
    client, root = simulation.client, simulation.root
    sid, _, data = preview_of(client, quantity=2)
    execute(client, sid, data["preview_id"])
    code = data["materials"][0]["material_code"]
    before = stock(simulation.app, code)["on_hand_qty"]
    restarted = sim.create_simulation_app(root)
    try:
        from fastapi.testclient import TestClient
        with TestClient(restarted) as again:
            again.post("/api/v1/inventory/execute",
                       json={"session_id": sid, "preview_id": data["preview_id"], "confirmed": True})
            assert restarted.state.inventory_runtime.ledger.stock("simulation", code)["on_hand_qty"] == before
    finally:
        restarted.state.simulation_restore()


def test_tc093_new_task_after_restart_executes(simulation):
    import simulation_server as sim
    client, root = simulation.client, simulation.root
    sid, _, data = preview_of(client, quantity=1)
    execute(client, sid, data["preview_id"])
    restarted = sim.create_simulation_app(root)
    try:
        from fastapi.testclient import TestClient
        with TestClient(restarted) as again:
            answer = again.post("/api/v1/agent/query",
                                json={"message": "生产1片并扣减库存", "session_id": sid, "persist_session": True}).json()
            second = answer["structured_data"]["data"]
            assert second["preview_id"] != data["preview_id"]
            result = again.post("/api/v1/inventory/execute",
                                json={"session_id": sid, "preview_id": second["preview_id"], "confirmed": True})
            assert result.status_code == 200
            assert result.json()["structured_data"]["data"]["executed"] is True
    finally:
        restarted.state.simulation_restore()


def test_tc094_reset_only_clears_simulation_ledger(simulation):
    client, app, root = simulation.client, simulation.app, simulation.root
    sid, _, data = preview_of(client, quantity=2)
    code = data["materials"][0]["material_code"]
    initial = Decimal(stock(app, code)["on_hand_qty"])
    execute(client, sid, data["preview_id"])
    assert Decimal(stock(app, code)["on_hand_qty"]) < initial
    response = client.post("/api/v1/simulation/reset")
    assert response.status_code == 200 and response.json()["scope"] == "simulation-ledger"
    ledger_path = Path(response.json()["ledger"]).resolve()
    assert ledger_path.is_relative_to(root.resolve())
    # 重置后账本回到合成初始余额，且真实数据文件没有被触碰。
    assert Decimal(app.state.inventory_runtime.ledger.stock("simulation", code)["on_hand_qty"]) == initial
    assert all(sha(path) == digest for path, digest in BASELINE_SHA256.items())


def test_tc095_after_reset_products_remain_and_real_data_unchanged(simulation):
    client, app = simulation.client, simulation.app
    client.post("/api/v1/simulation/reset")
    products = client.get("/api/v1/agent/catalog").json()["products"]
    assert [p["product_code"] for p in products] == list(PRODUCTS)
    assert all(sha(path) == digest for path, digest in BASELINE_SHA256.items())
    answer = ask(client, new_session(client, "KQ-BHX3588M").json()["session_id"], "生产1片并扣减库存")
    assert data_of(answer)["execution_available"] is True


# ================================================================ I. 权限与安全隔离

def test_tc096_public_enterprise_execution_stays_denied(public_client):
    sid = new_session(public_client, "KQ-Z112").json()["session_id"]
    preview = public_client.post("/api/v1/inventory/preview",
                                 json={"session_id": sid, "product": "KQ-Z112", "quantity": 1})
    execute_response = public_client.post("/api/v1/inventory/execute",
                                          json={"session_id": sid, "preview_id": "any", "confirmed": True})
    assert preview.status_code == 403 and execute_response.status_code == 403
    receipt = public_client.get(f"/api/v1/inventory/receipts/any?session_id={sid}")
    assert receipt.status_code == 403


def test_tc097_simulation_mode_is_off_by_default(monkeypatch):
    from services import safe_inventory_runtime as runtime_module
    monkeypatch.delenv("PMC_SIMULATION_MODE", raising=False)
    monkeypatch.delenv("PMC_SIMULATION_SOURCE", raising=False)
    assert runtime_module.simulation_enabled() is False
    book = synthetic_book([("SYN-A", "1", "片")], {"SYN-A": [lot("SYN-A", "10")]})
    service = SimpleNamespace(book_service=book)
    session = {"session_id": "s1", "context": {"current_demand": {"status": "valid", "product": "SYN-P", "quantity": 2}}}
    result = runtime_module.preview_issue(service, session, "SYN-P", 2)
    assert result["data"]["write_policy"] == "enterprise_read_only"
    assert result["data"]["execution_available"] is False


def test_tc098_simulation_refuses_released_data_path(monkeypatch):
    from services.safe_inventory_runtime import SafeInventoryRuntime, SIMULATION_NAMESPACE
    monkeypatch.setenv("PMC_SIMULATION_MODE", "1")
    with pytest.raises(ValueError):
        SafeInventoryRuntime(ROOT / "data/runtime", namespace=SIMULATION_NAMESPACE)


def test_tc099_client_cannot_forge_trusted_identity(simulation):
    client, app = simulation.client, simulation.app
    sid, _, data = preview_of(client, quantity=1)
    forged = client.post("/api/v1/inventory/execute",
                         json={"session_id": sid, "preview_id": data["preview_id"], "confirmed": True},
                         headers={"X-PMC-Owner": "attacker", "X-PMC-User": "attacker",
                                  "X-Inventory-Write-Token": "shared-token"})
    assert forged.status_code == 200
    with app.state.inventory_runtime.ledger.transaction() as connection:
        owner = connection.execute("SELECT owner_id FROM ledger_executions").fetchone()[0]
    # 身份来自服务端模拟配置，客户端 Header 与共享令牌不参与。
    assert owner == "local-simulation-operator"
    denied = client.post("/api/v1/inventory/execute",
                         json={"session_id": "not-a-real-session", "preview_id": "x", "confirmed": True},
                         headers={"X-PMC-Owner": "attacker"})
    assert denied.status_code == 403
    # 额外的身份字段不被接受（请求模型禁止额外字段）。
    extra = client.post("/api/v1/inventory/execute",
                        json={"session_id": sid, "preview_id": data["preview_id"], "confirmed": True,
                              "owner_id": "attacker"})
    assert extra.status_code == 422


def test_tc100_only_synthetic_ledger_is_written(simulation):
    client, app, root = simulation.client, simulation.app, simulation.root
    sid, _, data = preview_of(client, quantity=1)
    execute(client, sid, data["preview_id"])
    ledger_path = (root / "inventory" / "ledger.sqlite3").resolve()
    assert ledger_path.is_file() and ledger_path.is_relative_to(root.resolve())
    assert not ledger_path.is_relative_to((ROOT / "data").resolve())
    assert app.state.inventory_runtime.namespace == "simulation"
    assert all(sha(path) == digest for path, digest in BASELINE_SHA256.items())
