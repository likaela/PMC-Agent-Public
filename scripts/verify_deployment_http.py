"""Real HTTP acceptance against any running deployment, without data writes.

Only normal conversation/session writes are performed. No import/publication.
"""
import argparse
import json
from urllib.request import Request, urlopen


def verify(base_url):
    base_url = base_url.rstrip("/")
    def request(path, body=None):
        req = Request(base_url + path, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=60) as response:
            return json.load(response)
    assert request("/health")["status"] == "ok"
    catalog = request("/api/v1/agent/catalog")["products"]
    assert {p["product_code"] for p in catalog} == {"KQ-Z112", "KQ-BHX3588M", "DEMO-PMC-01"}
    checks = 2
    for product, count, quantities in (("KQ-Z112",34,(1,2)), ("KQ-BHX3588M",91,(1,10)), ("DEMO-PMC-01",3,(50,75,76,100))):
        scenario = "demo_scenario_50_vs_100" if product == "DEMO-PMC-01" else None
        sid = request("/api/v1/agent/sessions", {"product_code":product, "scenario_id":scenario})["session_id"]
        def query(message):
            result = request("/api/v1/agent/query",dict(message=message,session_id=sid,scenario_id=scenario,persist_session=True))
            assert result["status"] == "ok", (product, message, result["status"])
            assert result.get("provenance"), (product, message, "no provenance")
            return result["structured_data"]["data"]
        bom = query(f"查询{product}的BOM")
        assert bom.get("line_count",bom.get("total_material_count")) == count
        checks += 1
        for qty in quantities:
            data = query(f"生产{qty}片可以吗")
            if product == "KQ-Z112":
                assert data["inventory_sufficient_for_requested"] is (qty == 1)
            elif scenario:
                assert data["inventory_sufficient_for_requested"] is (qty <= 75)
            else:
                assert data["comparable_material_count"] == 55
                assert len(data["materials"]) == 91
            checks += 1
        capacity = query("最多可以生产多少")
        if product == "KQ-Z112":
            assert capacity.get("theoretical_max_quantity", capacity.get("verified_material_capacity")) == 1
        elif scenario:
            assert str(capacity["theoretical_max_quantity"]) == "75"
        else:
            assert capacity["verified_material_capacity"] == 0
        checks += 1
        if scenario:
            purchase = query("生成采购建议")
            assert len(purchase["recommendations"]) == 4
            assert all(row["suggested_purchase_qty"] is not None for row in purchase["recommendations"])
            assert purchase["total_purchase_cost"] is not None
            checks += 1
        path = "/api/v1/agent/sessions/" + sid + ("?scenario_id=" + scenario if scenario else "")
        assert request(path)["turns"]
        checks += 1
    return {"http_checks":checks, "products":3, "status":"passed"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    print(json.dumps(verify(parser.parse_args().base_url)))
