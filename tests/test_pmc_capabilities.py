"""扩展问答能力验收：缺料识别、独立物料查询、投产核算/扣减、统计与产品联想。"""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PMC_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("PMC_PUBLIC_DATA_ROOT", str(ROOT / "data/public"))
    from services.local_business_binding import _binding
    _binding.cache_clear()
    from scripts.run_public import initialize
    initialize()
    from fastapi.testclient import TestClient
    from api.public_app import app
    with TestClient(app) as client:
        yield client


def create_session(client, product=None):
    body = {"product_code": product} if product else {}
    result = client.post("/api/v1/agent/sessions", json=body)
    assert result.status_code == 200
    return result.json()["session_id"]


def query(client, sid, message):
    result = client.post("/api/v1/agent/query", json={"message": message, "session_id": sid,
                                                      "persist_session": True})
    assert result.status_code == 200
    return result.json()


# ---------------------------------------------------------------- 需求1：产品编码附带名称
def test_catalog_products_carry_display_names(client):
    products = {p["product_code"]: p for p in client.get("/api/v1/agent/catalog").json()["products"]}
    assert products["KQ-Z112"]["product_name"] == "飞腾八核核心板"
    assert products["KQ-BHX3588M"]["product_name"] == "瑞芯微核心模块"
    # 运行时目录只保留两个业务产品；Demo 不再是可选择的第三个产品。
    assert set(products) == {"KQ-Z112", "KQ-BHX3588M"}


# ---------------------------------------------------------------- 需求6：自动联想
@pytest.mark.parametrize("text,product", [
    ("查询3588", "KQ-BHX3588M"), ("KQ-BHX3588", "KQ-BHX3588M"), ("KQ-BHX2588", "KQ-BHX3588M"),
    ("查询Z112", "KQ-Z112"), ("飞腾八核核心板缺哪些物料", "KQ-Z112"),
])
def test_product_fuzzy_association(client, text, product):
    result = client.post("/api/v1/agent/resolve", json={"message": text}).json()
    assert result["product_code"] == product


# ---------------------------------------------------------------- 需求2：脱离产品的物料独立查询
def test_standalone_material_query_without_product(client):
    sid = create_session(client)
    result = query(client, sid, "105C50000C101 库存多少")
    assert result["status"] == "ok"
    assert "105C50000C101" in result["answer"]
    assert "账面库存合计" in result["answer"]
    data = result["structured_data"]["data"]
    assert data["materials"][0]["recorded_stock_qty"] is not None


def test_standalone_material_shortage_check_without_product(client):
    sid = create_session(client)
    result = query(client, sid, "1654900600101 是否缺料")
    assert result["status"] == "ok"
    assert "结论" in result["answer"]
    assert "1654900600101" in result["answer"]


# ---------------------------------------------------------------- 需求3：缺料/急刻意图
def test_shortage_intent_lists_critical_materials_not_bom_summary(client):
    sid = create_session(client, "KQ-Z112")
    result = query(client, sid, "KQ-Z112 产品的哪一种物料现在是比较急缺的？")
    assert result["status"] == "ok"
    answer = result["answer"]
    assert "急缺物料分析" in answer
    assert "结论" in answer
    # 必须给出具体物料与数量级信息，而不是仅返回BOM台账基础信息
    assert "最多可支持" in answer or "无法投产" in answer
    assert "共 34 项物料" not in answer


def test_shortage_with_quantity_lists_gaps(client):
    sid = create_session(client, "KQ-Z112")
    result = query(client, sid, "生产100片时哪些物料是缺料的")
    assert result["status"] == "ok"
    answer = result["answer"]
    assert "缺料" in answer and "缺口" in answer
    assert "结论" in answer
    data = result["structured_data"]["data"]
    assert any((r.get("book_shortage_qty") or "0") not in ("0", "0.0") for r in data["materials"])


# ---------------------------------------------------------------- 需求4-1/4-2：统计类问答
def test_inventory_kind_count(client):
    sid = create_session(client)
    result = query(client, sid, "目前库存一共多少种类")
    assert result["status"] == "ok"
    assert "种物料" in result["answer"]


def test_bom_component_kind_counts(client):
    sid = create_session(client)
    result = query(client, sid, "支持的几个BOM，各自有多少元器件种类")
    assert result["status"] == "ok"
    answer = result["answer"]
    for token in ("KQ-Z112", "34", "KQ-BHX3588M", "91", "飞腾八核核心板", "瑞芯微核心模块"):
        assert token in answer


# ---------------------------------------------------------------- 需求4-3：投产核算 + 采购BOM表单
def test_production_plan_generates_purchase_bom_with_quantities(client):
    sid = create_session(client, "KQ-Z112")
    result = query(client, sid, "投产100片，生成需要采购的BOM表单")
    assert result["status"] == "ok"
    data = result["structured_data"]["data"]
    assert data["inventory_sufficient_for_requested"] is False
    rows = data["recommendations"]
    assert rows and all(r["suggested_purchase_qty"] for r in rows)
    assert "需要采购的BOM表单" in result["answer"]


def test_production_plan_sufficient_needs_no_purchase(client):
    sid = create_session(client, "KQ-Z112")
    result = query(client, sid, "投产1片，生成需要采购的BOM表单")
    assert result["status"] == "ok"
    assert result["structured_data"]["query_type"] == "production_capacity"
    assert result["structured_data"]["data"]["inventory_sufficient_for_requested"] is True


# ---------------------------------------------------------------- 企业只读预览：自然语言不得直接扣减
def test_deduction_request_only_previews_and_preserves_enterprise_stock(client, tmp_path):
    sid = create_session(client, "KQ-Z112")
    before = query(client, sid, "1654900600101 库存多少")
    before_qty = before["structured_data"]["data"]["materials"][0]["recorded_stock_qty"]
    result = query(client, sid, "投产1片并扣减库存，更新库存文件")
    assert result["status"] == "ok"
    assert result["structured_data"]["operation"] == "inventory_preview"
    preview = result["structured_data"]["data"]
    assert preview["requested_quantity"] == 1
    assert preview["materials"]
    assert preview["executed"] is False
    assert preview["can_execute"] is False
    assert preview["execution_available"] is False
    assert "inventory_deduction" not in result["structured_data"]
    restored = client.get(f"/api/v1/agent/sessions/{sid}").json()["turns"][-1]["response"]
    assert restored["structured_data"]["operation"] == "inventory_preview"
    assert restored["structured_data"]["data"]["preview_id"] == preview["preview_id"]
    assert restored["structured_data"]["data"]["executed"] is False
    after = query(client, sid, "1654900600101 库存多少")
    after_qty = after["structured_data"]["data"]["materials"][0]["recorded_stock_qty"]
    assert after_qty == before_qty
    journal = tmp_path / "runtime/inventory_adjustments.json"
    assert not journal.exists()
    assert not list((tmp_path / "runtime").rglob("*updated_inventory*"))


def test_enterprise_preview_does_not_invent_fifo_execution(client):
    sid = create_session(client, "KQ-Z112")
    result = query(client, sid, "投产1片并扣减库存")
    assert result["structured_data"]["operation"] == "inventory_preview"
    data = result["structured_data"]["data"]
    assert data["write_policy"] == "enterprise_read_only"
    assert data["executable"] is False and data["executed"] is False
    assert "movements" not in data and "task_id" not in data
    # 严格FIFO扣减另由合成批次、临时账本及真实执行服务测试覆盖。


def test_batch_query_orders_fifo(client):
    sid = create_session(client)
    result = query(client, sid, "105C50000C101 的批次明细")
    details = result["structured_data"]["data"]["materials"][0]["details"]
    from services.pmc_capability_service import batch_fifo_key
    keys = [batch_fifo_key(d.get("batch_no")) for d in details]
    assert keys == sorted(keys)


# ---------------------------------------------------------------- 产品指代与口语单位回归
def test_product_abbreviation_and_pointer_in_session(client):
    sid = create_session(client, "KQ-BHX3588M")
    result = query(client, sid, "核验3588")
    assert result["status"] == "ok"
    assert result["structured_data"]["query_type"] == "product_overview"
    assert "KQ-BHX3588M" in result["answer"]
    result = query(client, sid, "核验该产品")
    assert result["structured_data"]["query_type"] == "product_overview"


def test_count_unit_synonym_and_sufficiency_breakdown(client):
    sid = create_session(client, "KQ-BHX3588M")
    result = query(client, sid, "生产该产品50套够不够")
    assert result["status"] == "invalid_query"
    assert result["structured_data"].get("data") is None
    # 没有“套→片”的可信换算；修正为正式单位后仍须完成原业务核算。
    result = query(client, sid, "生产该产品50片够不够")
    assert result["status"] == "ok"
    data = result["structured_data"]["data"]
    assert data["requested_quantity"] == 50
    assert data["total_material_count"] == 91
    # 新总库存表下 58 项有记录；同批次多条记录按最大值合并，58 项全部可核算。
    assert data["comparable_material_count"] == 58
    assert data["inventory_sufficient_for_requested"] is False
    answer = result["answer"]
    assert "项满足" in answer and "不足物料" in answer and "缺口" in answer


# ---------------------------------------------------------------- 全库存口径与物料个数
@pytest.mark.parametrize("product", [None, "KQ-Z112", "KQ-BHX3588M"])
def test_inventory_kind_count_is_global_for_every_session(client, product):
    """“库存一共多少种物料”必须回答整个库存表口径，不受会话绑定产品影响。"""
    sid = create_session(client, product)
    result = query(client, sid, "库存一共多少种物料")
    assert result["status"] == "ok"
    data = result["structured_data"]["data"]
    assert data["inventory_scope"] == "entire_inventory_table"
    from services.pmc_capability_service import load_master_records
    assert data["inventory_kind_count"] == len(load_master_records())
    # 不能退化成任一产品的 BOM 物料数
    assert "total_material_count" not in data


def test_inventory_kind_count_scope_when_product_named(client):
    sid = create_session(client)
    result = query(client, sid, "KQ-Z112 库存有多少种物料")
    data = result["structured_data"]["data"]
    assert data["inventory_scope"] == "product_inventory"
    assert data["product"] == "KQ-Z112"
    assert data["inventory_kind_count"] == 34


@pytest.mark.parametrize("text", [
    "105C50000C101 有多少", "105C50000C101 库存数量是多少", "105C50000C101 还剩多少个",
    "查一下 105C50000C101 的个数", "105C50000C101 结存多少", "105C50000C101 数量",
])
def test_single_material_quantity_synonyms(client, text):
    sid = create_session(client, "KQ-Z112")
    result = query(client, sid, text)
    assert result["status"] == "ok"
    data = result["structured_data"]["data"]
    assert data["materials"][0]["recorded_stock_qty"] == "7694"
    assert data["materials"][0]["material_code"] == "105C50000C101"


def test_material_quantity_by_chinese_name(client):
    sid = create_session(client, "KQ-Z112")
    result = query(client, sid, "查一下贴片电阻的个数")
    assert result["status"] == "ok"
    data = result["structured_data"]["data"]
    # 同名多个料号分别列示，不合并数量
    assert len(data["materials"]) >= 2
    assert data["matched_material_count"] == len(data["materials"])
    assert {row["material_code"] for row in data["materials"]} >= {
        "105C50000C101", "105A751034101", "105A510000101"}
