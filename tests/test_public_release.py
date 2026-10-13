"""Acceptance through the production API; real Services and released facts."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PMC_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("PMC_PUBLIC_DATA_ROOT", str(ROOT / "data/public"))
    from scripts.run_public import initialize
    initialize()
    from fastapi.testclient import TestClient
    from api.public_app import app
    with TestClient(app) as client:
        yield client


def test_clone_can_discover_three_products(client):
    response = client.get("/api/v1/agent/catalog")
    assert response.status_code == 200
    assert {p["product_code"] for p in response.json()["products"]} == {
        "KQ-Z112", "KQ-BHX3588M", "DEMO-PMC-01"}


def create_session(client, product):
    scenario = "demo_scenario_50_vs_100" if product == "DEMO-PMC-01" else None
    result = client.post("/api/v1/agent/sessions", json={"product_code": product, "scenario_id": scenario})
    assert result.status_code == 200
    return result.json()["session_id"], scenario


def query(client, sid, scenario, message):
    result = client.post("/api/v1/agent/query", json={"message": message, "session_id": sid,
        "scenario_id": scenario, "persist_session": True})
    assert result.status_code == 200
    return result.json()


@pytest.mark.parametrize("product,count", [("KQ-Z112", 34), ("KQ-BHX3588M", 91), ("DEMO-PMC-01", 3)])
def test_released_bom_queries(client, product, count):
    sid, scenario = create_session(client, product)
    result = query(client, sid, scenario, f"查询{product}的BOM")
    assert result["status"] == "ok" and result["provenance"]
    data = result["structured_data"]["data"]
    assert data.get("line_count", data.get("total_material_count")) == count


@pytest.mark.parametrize("product,quantity,sufficient", [("KQ-Z112", 1, True), ("KQ-Z112", 2, False),
    ("DEMO-PMC-01", 50, True), ("DEMO-PMC-01", 75, True), ("DEMO-PMC-01", 76, False), ("DEMO-PMC-01", 100, False)])
def test_production_boundaries_come_from_live_calculation(client, product, quantity, sufficient):
    sid, scenario = create_session(client, product)
    result = query(client, sid, scenario, f"生产{quantity}片可以吗")
    assert result["status"] == "ok" and result["provenance"]
    assert result["structured_data"]["data"]["inventory_sufficient_for_requested"] is sufficient


@pytest.mark.parametrize("product,maximum", [("KQ-Z112", 1), ("KQ-BHX3588M", 0), ("DEMO-PMC-01", 75)])
def test_capacity_is_product_specific(client, product, maximum):
    sid, scenario = create_session(client, product)
    result = query(client, sid, scenario, "最多可以生产多少")
    data = result["structured_data"]["data"]
    assert int(data.get("theoretical_max_quantity", data.get("verified_material_capacity"))) == maximum


def test_bhx_known_inventory_is_computed_without_filling_missing_rows(client):
    sid, scenario = create_session(client, "KQ-BHX3588M")
    result = query(client, sid, scenario, "生产10片可以吗")
    data = result["structured_data"]["data"]
    assert len(data["materials"]) == 91
    assert data["comparable_material_count"] == 55
    assert any(row["recorded_stock_qty"] is None for row in data["materials"])


def test_demo_purchase_advice_remains_available(client):
    sid, scenario = create_session(client, "DEMO-PMC-01")
    query(client, sid, scenario, "生产100台可以吗")
    result = query(client, sid, scenario, "生成采购建议")
    assert len(result["structured_data"]["data"]["recommendations"]) == 4


def test_invalid_zero_does_not_break_followup_or_history(client):
    sid, scenario = create_session(client, "KQ-Z112")
    query(client, sid, scenario, "生产2片可以吗")
    assert query(client, sid, scenario, "生产0片")["status"] == "invalid_query"
    valid = query(client, sid, scenario, "那生产1片呢")
    assert valid["structured_data"]["data"]["requested_quantity"] == 1
    history = client.get(f"/api/v1/agent/sessions/{sid}").json()
    assert history["turns"][-1]["response"]["structured_data"]["data"] == valid["structured_data"]["data"]


def test_file_download_and_other_data_spaces_are_rejected(client):
    assert client.get("/data/public/bom/published.db").status_code == 404
    assert client.get("/api/v1/pmc/analyze").status_code == 404
    result = client.post("/api/v1/agent/sessions", json={"scenario_id": "unapproved_space"})
    assert result.status_code == 422
