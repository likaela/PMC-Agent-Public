"""Real Agent HTTP/Persistent/Book calculation over synthetic facts and a temp DB."""
import copy
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.enterprise_book_queries import EnterpriseBookQueries
from agents.orchestrator import AgentOrchestrator
from agents.planner import AgentPlanner
from agents.response_generator import ResponseGenerator
from agents.tool_registry import ToolRegistry
from repositories.agent_conversation_repository import AgentConversationRepository
from repositories.fact_store import SQLiteFactStore
from services.book_inventory_service import BookInventoryService
from services.persistent_agent_service import PersistentAgentService

PRODUCTS = ("KQ-SYN01", "KQ-SYN02", "TEST-PRODUCT-01")
SOURCE = dict(source_file="synthetic.json", content_sha256="synthetic-test-only", sheet_name="synthetic",
    record_count=1, unique_material_count=1, duplicate_observation_count=0, row_errors=[], excluded_sheets=[],
    unit_confirmation={"unit": "片"}, quantity_confirmation={"unit": "片", "product_scope": [
        {"product": p, "revision": "V01"} for p in PRODUCTS]}, records={"SYN-MAT-01": [dict(
        material_code="SYN-MAT-01", name="Synthetic material", recorded_stock_qty="30", inventory_unit="片",
        batch_no="SYN-BATCH", location="SYN-LOCATION", duplicate_observation=False,
        provenance={"source_file": "synthetic.json", "row_number": 1})]})


class SyntheticFacts:
    repository = None

    def __init__(self):
        self.repository = self

    def resolve_item(self, code):
        return {"material_code": code, "unit": "片", "name": "Synthetic product"} if code in PRODUCTS else None

    def list_published_bom_versions(self, code):
        return [{"version": "V01", "effective_from": "2026-01-01"}] if code in PRODUCTS else []

    def published_bom_parent_codes(self):
        return set(PRODUCTS)

    def query_bom(self, product, revision):
        return dict(status="ok", query={"parent_material_code": product, "revision": revision},
            data=dict(parent_item={"material_code": product, "unit": "片", "name": "Synthetic product"},
                revision=revision, effective_from="2026-01-01", line_count=1, lines=[dict(
                    child_item={"material_code": "SYN-MAT-01", "unit": "片", "name": "Synthetic material"},
                    qty_per="2", unit="片", provenance={"source_file": "synthetic.json", "row_number": 1})]),
            completeness={"unit": True}, provenance={"source_file": "synthetic.json"}, warnings=[])


@pytest.fixture
def api(tmp_path, monkeypatch):
    # The real published input files and runtime never participate in this fixture.
    monkeypatch.setenv("PMC_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("PMC_REVIEW_MODE", "1")
    monkeypatch.setenv("PMC_TRIAL_MODE", "0")
    monkeypatch.delenv("PMC_PUBLIC_DATA_ROOT", raising=False)
    store = SQLiteFactStore(tmp_path / "synthetic-sessions.sqlite3")
    store.migrate()
    repository = AgentConversationRepository(store)
    entries = [dict(product_code=p, product_name="Synthetic product", revision="V01", default_mode="full",
        aliases=[], synthetic=False, modes=[dict(mode="full", scenario_id=None, line_count=1)]) for p in PRODUCTS]
    calls = []
    services = {}

    def blocked(*args, **kwargs):
        raise AssertionError("No LLM or unbound business tool may run in this test")

    for product in PRODUCTS:
        book = BookInventoryService(SyntheticFacts(), SimpleNamespace(load=lambda: copy.deepcopy(SOURCE)))
        original = book.analyze

        def analyze(*args, _original=original, **kwargs):
            calls.append((args, kwargs))
            return _original(*args, **kwargs)

        book.analyze = analyze
        service = PersistentAgentService(repository, book_service=book)
        service.book_queries = EnterpriseBookQueries(book)
        service.orchestrator = AgentOrchestrator(ToolRegistry(resolver=blocked),
            planner=AgentPlanner(llm_factory=blocked, llm_status=lambda: {"status": "warning"}),
            response_generator=ResponseGenerator(llm_factory=blocked, llm_status=lambda: {"status": "warning"}))
        services[product] = service

    from api.routes import agent as routes
    from services import local_business_binding, local_reviewer_catalog, pmc_capability_service as capability

    binding = SimpleNamespace(repository=repository, catalog={e["product_code"]: e for e in entries})
    monkeypatch.setattr(local_business_binding, "local_business_binding", lambda: binding)
    monkeypatch.setattr(local_reviewer_catalog, "local_review_catalog", lambda store: copy.deepcopy(entries))
    monkeypatch.setattr(capability, "_catalog_rows", lambda: copy.deepcopy(entries))
    monkeypatch.setattr(capability, "load_master_records", lambda: copy.deepcopy(SOURCE["records"]))
    monkeypatch.setattr(capability, "load_product_inventory", lambda product: {})
    monkeypatch.setattr(capability, "load_alias_table", lambda: {})
    monkeypatch.setattr(routes, "create_fact_store", lambda: None)

    def current(product=None, session_id=None):
        if session_id:
            context = repository.get_session(session_id)["context"]
            product = (context.get("product_context") or {}).get("product_code")
        return services.get(product, services[PRODUCTS[0]])

    monkeypatch.setattr(routes, "_current_service", current)
    monkeypatch.setattr(routes, "_persistent_service", lambda scenario=None: services[PRODUCTS[0]])
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, repository=repository, calls=calls, services=services)


def session(api, product=PRODUCTS[0]):
    response = api.client.post("/api/v1/agent/sessions", json={"product_code": product})
    assert response.status_code == 200
    return response.json()["session_id"]


def query(api, sid, message):
    response = api.client.post("/api/v1/agent/query", json={"session_id": sid, "persist_session": True, "message": message})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("quantity", [1, 10, 50, 100])
def test_valid_production_and_purchase_use_exact_quantity(api, quantity):
    sid = session(api)
    result = query(api, sid, f"生产{quantity}片，生成采购清单")
    assert result["status"] == "ok"
    data = result["structured_data"]["data"]
    assert data["requested_quantity"] == quantity
    assert Decimal(data["materials"][0]["required_qty"]) == Decimal(2 * quantity)
    assert result["structured_data"]["demand_snapshot"]["quantity"] == quantity


@pytest.mark.parametrize("raw", ["1/2片", "1.5片", "-1片", "- 1片", "−1片", "－1片", "﹣1片", "＋1片", "0片", "零片", "负一片", "负 1片", "1e3片",
    "1abc片", "abc片", "NaN片", "100公斤", "100箱", "100桶", "100套", "100台", "一百二片", "一点五片", "三分之一片", "1万片", "½片", "1片或者2片",
    "1:2", "1：2", "1,000", "1，000", "1\\2", "1_000", "1×2", "1*2"])
@pytest.mark.parametrize("suffix", ["，生成采购清单", "并领料扣减库存"])
def test_invalid_quantity_or_unit_never_enters_any_calculation_or_issue(api, monkeypatch, raw, suffix):
    from services.pmc_capability_service import PmcCapabilityQueries
    monkeypatch.setattr(PmcCapabilityQueries, "_deduct", lambda *a, **k: pytest.fail("Invalid input reached inventory issue"))
    sid = session(api)
    before = len(api.calls)
    result = query(api, sid, "生产" + raw + suffix)
    assert result["status"] == "invalid_query"
    assert result["structured_data"]["data"] is None
    assert result["structured_data"]["completed_steps"] == []
    assert len(api.calls) == before
    assert api.repository.get_session(sid)["context"]["current_demand"]["status"] == "invalid"


@pytest.mark.parametrize("message", ["生产一百片", "生产100", "50个呢", "生产该产品10件"])
def test_explicit_supported_integer_speech(api, message):
    sid = session(api)
    result = query(api, sid, message)
    assert result["status"] == "ok"
    expected = 50 if "50" in message else 10 if "10件" in message else 100
    assert result["structured_data"]["data"]["requested_quantity"] == expected


def test_latest_ordinary_production_replaces_old_procurement_target(api):
    sid = session(api)
    query(api, sid, "生产100片，生成采购清单")
    previous = query(api, sid, "生产50片可以吗")
    result = query(api, sid, "帮我生成采购建议")
    assert result["structured_data"]["data"]["requested_quantity"] == 50
    assert result["structured_data"]["data"]["recommendations"][0]["suggested_purchase_qty"] == "70"
    snapshot = result["structured_data"]["demand_snapshot"]
    assert snapshot["generation"] == previous["structured_data"]["demand_snapshot"]["generation"] == 2
    assert snapshot["quantity"] == 50 and snapshot["status"] == "valid"
    history = api.repository.history(sid)["turns"]
    assert history[0]["response"]["structured_data"]["data"]["requested_quantity"] == 100
    assert history[-1]["response"]["structured_data"]["data"] == result["structured_data"]["data"]


@pytest.mark.parametrize("invalid", ["生产0片", "生产1/2片", "生产100公斤", "我要生产KQ-SYN01"])
def test_invalid_or_incomplete_new_task_blocks_stale_purchase_target(api, invalid):
    sid = session(api)
    query(api, sid, "生产100片，生成采购清单")
    old = copy.deepcopy(api.repository.get_session(sid)["context"])
    result = query(api, sid, invalid)
    assert result["status"] in {"invalid_query", "needs_input"}
    before = len(api.calls)
    purchase = query(api, sid, "帮我生成采购建议")
    assert purchase["status"] == "needs_input" and purchase["structured_data"]["data"] is None
    assert len(api.calls) == before
    # The old successful task remains historical; an invalid target is never written as zero.
    context = api.repository.get_session(sid)["context"]
    assert context["production"]["quantity"] == old["production"]["quantity"] == 100
    assert context["current_demand"]["status"] != "valid"
    assert query(api, sid, "生产1片可以吗")["structured_data"]["data"]["requested_quantity"] == 1


def test_greeting_preserves_active_demand_but_new_session_does_not(api):
    sid = session(api)
    query(api, sid, "生产100片，生成采购清单")
    assert query(api, sid, "你好")["status"] == "ok"
    assert query(api, sid, "帮我生成采购建议")["structured_data"]["data"]["requested_quantity"] == 100
    fresh = session(api)
    assert query(api, fresh, "帮我生成采购建议")["status"] == "needs_input"


def test_product_switch_keeps_existing_api_boundary_and_uses_new_demand(api):
    first = session(api)
    query(api, first, "生产100片，生成采购清单")
    denied = api.client.post("/api/v1/agent/query", json={"session_id": first, "persist_session": True,
        "message": "生产50片KQ-SYN02，生成采购清单"})
    assert denied.status_code == 409
    second = session(api, PRODUCTS[1])
    assert query(api, second, "生成采购建议")["status"] == "needs_input"
    query(api, second, "生产50片可以吗")
    result = query(api, second, "生成采购建议")
    assert result["structured_data"]["data"]["product"] == PRODUCTS[1]
    assert result["structured_data"]["data"]["requested_quantity"] == 50


def test_direct_book_entry_rejects_unconfirmed_unit_without_stock_comparison(api):
    result = api.services[PRODUCTS[0]].book_service.analyze(PRODUCTS[0], quantity=100, requested_unit="公斤")
    assert result["status"] == "invalid_query" and result["data"] is None


def test_unitless_quantity_before_a_catalog_product_is_not_a_hardcoded_prefix(api):
    sid = session(api, PRODUCTS[2])
    result = query(api, sid, "生产100 TEST-PRODUCT-01，生成采购清单")
    assert result["status"] == "ok"
    assert result["structured_data"]["data"]["product"] == PRODUCTS[2]
    assert result["structured_data"]["data"]["requested_quantity"] == 100


@pytest.mark.parametrize("message", ["生产1，生成采购清单", "生产1,生成采购清单", "生产1:生成采购清单"])
def test_sentence_punctuation_after_a_valid_unitless_target_is_not_a_number_separator(api, message):
    sid = session(api)
    result = query(api, sid, message)
    assert result["status"] == "ok"
    assert result["structured_data"]["data"]["requested_quantity"] == 1


def test_missing_inventory_preserves_demand_but_cannot_invent_purchase_gap(api, monkeypatch):
    facts = api.services[PRODUCTS[0]].book_service.facts
    original = facts.query_bom

    def bom(product, revision):
        result = original(product, revision)
        result["data"]["lines"].append(dict(child_item={"material_code": "SYN-UNKNOWN", "name": "Unknown stock"},
            qty_per="3", unit="片", provenance={"source_file": "synthetic.json", "row_number": 2}))
        result["data"]["line_count"] = 2
        return result

    monkeypatch.setattr(facts, "query_bom", bom)
    sid = session(api)
    result = query(api, sid, "生产100片，生成采购清单")
    data = result["structured_data"]["data"]
    assert data["requested_quantity"] == 100
    unknown = next(row for row in data["materials"] if row["material_code"] == "SYN-UNKNOWN")
    assert unknown["required_qty"] == "300"
    assert unknown["recorded_stock_qty"] is None
    assert unknown["book_shortage_qty"] is None and unknown["suggested_purchase_qty"] is None
    assert len(data["recommendations"]) == 1
    recommendation = data["recommendations"][0]
    assert recommendation["material"] == "SYN-MAT-01"
    assert recommendation["shortage_qty"] == recommendation["suggested_purchase_qty"] == "170"
    unverified = data["unverified_purchase_materials"][0]
    assert unverified["material"] == "SYN-UNKNOWN"
    assert unverified["required_qty"] == "300" and unverified["shortage_qty"] is None
    assert unverified["suggested_purchase_qty"] is None


def test_old_session_restores_latest_structured_demand_not_old_capability_field(api):
    sid = session(api)
    query(api, sid, "生产100片，生成采购清单")
    query(api, sid, "生产50片可以吗")
    saved = api.repository.get_session(sid)
    old_context = copy.deepcopy(saved["context"])
    old_context.pop("current_demand")
    assert old_context["pmc_capability"]["quantity"] == 100
    api.repository.append_turn(saved, "你好", dict(intent="clarification", status="ok", answer="你好",
        structured_data={"data": None}, provenance=None, warnings=[]), old_context, [])
    result = query(api, sid, "生成采购建议")
    assert result["structured_data"]["data"]["requested_quantity"] == 50


def test_preview_demand_generation_is_stable_then_new_or_invalid_target_invalidates_it(api, monkeypatch):
    from services.pmc_capability_service import PmcCapabilityQueries
    captured = []

    def preview(self, text, context, rows, product, quantity, session=None):
        captured.append(copy.deepcopy(session["context"]["current_demand"]))
        return self._turn("production_capacity", "ok", "Synthetic preview only", query_type="production_capacity",
            query={"product": product, "quantity": quantity}, data={"product": product, "production_quantity": quantity},
            completeness={}, context=context)

    monkeypatch.setattr(PmcCapabilityQueries, "_deduct", preview)
    sid = session(api)
    result = query(api, sid, "生产10片并领料")
    assert captured[-1] == api.repository.get_session(sid)["context"]["current_demand"]
    assert result["structured_data"]["demand_snapshot"] == captured[-1]
    old = captured[-1]
    query(api, sid, "生产50片可以吗")
    assert api.repository.get_session(sid)["context"]["current_demand"]["generation"] == old["generation"] + 1
    query(api, sid, "生产0片")
    blocked = api.repository.get_session(sid)["context"]["current_demand"]
    assert blocked["generation"] == old["generation"] + 2 and blocked["status"] == "invalid"


def test_old_missing_quantity_clarification_blocks_earlier_demand_during_restore(api):
    sid = session(api)
    query(api, sid, "生产100片，生成采购清单")
    saved = api.repository.get_session(sid)
    old_context = copy.deepcopy(saved["context"])
    old_context.pop("current_demand")
    api.repository.append_turn(saved, "我要生产KQ-SYN01", dict(intent="clarification", status="needs_input",
        answer="请提供生产数量", structured_data={"data": None, "missing_parameters": ["quantity"]},
        provenance=None, warnings=[]), old_context, [])
    before = len(api.calls)
    result = query(api, sid, "生成采购建议")
    assert result["status"] == "needs_input" and result["structured_data"]["data"] is None
    assert len(api.calls) == before


def test_standalone_material_incompatible_unit_never_generates_shortage(api):
    sid = session(api)
    result = query(api, sid, "SYN-MAT-01需求100公斤，还差多少")
    assert result["status"] == "invalid_query"
    assert result["structured_data"]["data"] is None
    assert api.calls == []


def test_material_quantity_question_is_not_a_missing_production_target(api):
    sid = session(api)
    result = query(api, sid, "SYN-MAT-01 数量")
    assert result["status"] == "ok"
    assert result["intent"] == "inventory"
    assert not api.repository.get_session(sid)["context"].get("current_demand")


def test_bound_revision_without_product_context_is_not_lost_after_production(api):
    sid = session(api)
    saved = api.repository.get_session(sid)
    context = copy.deepcopy(saved["context"])
    context.pop("product_context")
    api.repository.append_turn(saved, "你好", dict(intent="clarification", status="ok", answer="你好",
        structured_data={"data": None}, provenance=None, warnings=[]), context, [])
    result = query(api, sid, "生产100片可以吗")
    assert result["status"] == "ok"
    latest = api.repository.get_session(sid)["context"]
    assert latest["current_demand"]["revision"] == latest["production"]["revision"] == "V01"
    assert query(api, sid, "生成采购建议")["status"] == "ok"
