"""User-facing prose stays factual while Services and Evidence keep full detail.

Synthetic fixtures exercise the real local parser -> Tool -> BookInventoryService
path. No external model requests or enterprise data writes are performed.
"""
import copy

import pytest

from agents.business_answer import bom_overview, incomplete_production
from agents.business_formatter import format_business_answer, format_purchase_email
from agents.enterprise_book_queries import EnterpriseBookQueries
from agents.query_presentation import present_query
from test_unit_normalization import book, confirmed, record, source_of


DEFENSIVE = ("仅供参考", "不保证准确", "无法保证", "AI可能产生幻觉", "不承担责任",
             "不代表正式", "不会自动", "暂时无法安全放行", "不将模拟交期", "这是模拟领用")


def assert_business_copy(text):
    for phrase in DEFENSIVE:
        assert phrase not in text


@pytest.fixture
def synthetic_queries():
    source = source_of(
        {"SYN-A": [record("SYN-A", "20")]},
        unit_confirmation={"unit": "片"},
        quantity_confirmation=confirmed("SYN-P"),
    )
    service = book("SYN-P", "片", [("SYN-A", "2", "片"), ("SYN-B", "1", "片")], source)
    return EnterpriseBookQueries(service)


def test_real_book_query_keeps_partial_scope_without_defensive_prose(synthetic_queries):
    context = {"active_domain": "production", "production": {"material_code": "SYN-P", "revision": "V01"}}
    turn = synthetic_queries.run("生产10片", context, lambda: [])
    assert turn.response["status"] == "ok"
    data = turn.response["structured_data"]["data"]
    assert data["requested_quantity"] == 10
    assert data["comparable_material_count"] == 1
    assert data["missing_stock_material_count"] == 1
    assert data["book_shortages"] == []
    missing = next(row for row in data["materials"] if row["material_code"] == "SYN-B")
    assert missing["recorded_stock_qty"] is None
    assert missing["book_shortage_qty"] is None
    assert "1项库存未匹配" in turn.response["answer"]
    assert "已核算物料满足" in turn.response["answer"]
    assert "按缺口补齐后方可齐套" not in turn.response["answer"]
    assert turn.references
    assert_business_copy(turn.response["answer"])


def test_real_book_query_preserves_true_shortage(synthetic_queries):
    context = {"active_domain": "production", "production": {"material_code": "SYN-P", "revision": "V01"}}
    turn = synthetic_queries.run("生产11片", context, lambda: [])
    rows = turn.response["structured_data"]["data"]["book_shortages"]
    assert len(rows) == 1
    assert rows[0]["material_code"] == "SYN-A"
    assert rows[0]["required_qty"] == "22"
    assert rows[0]["book_shortage_qty"] == "2"
    assert "1项不足" in turn.response["answer"]
    assert_business_copy(turn.response["answer"])


def test_bom_copy_only_presents_available_business_fields():
    answer = bom_overview(
        {"parent_item": {"material_code": "TEST-PRODUCT", "unit": "片"}, "revision": "V01",
         "line_count": 1, "lines": [{"child_item": {"name": "电阻"}, "unit": "片"}]},
        {"unit": True, "parent_unit": True},
    )
    assert "V01" in answer and "1项物料" in answer and "电阻" in answer
    assert "生效日期" not in answer
    assert_business_copy(answer)


def test_inventory_unknown_fields_are_omitted_but_valid_zero_is_shown():
    answer = format_business_answer(
        "inventory", {"status": "ok", "query": {"material_code": "TEST-MAT"},
                      "data": {"on_hand_qty": "0", "available_qty": None, "reserved_qty": None}}
    )
    assert "在库数量 0" in answer
    assert "可用数量" not in answer and "预留数量" not in answer
    assert_business_copy(answer)


def purchase_evidence():
    return {"status": "ok", "data": {"recommendations": [
        {"material": "TEST-MAT", "shortage": "12", "suggested_purchase_qty": "12", "unit": "片",
         "supplier": None, "purchase_cost": None, "risk_level": None,
         "missing_fields": ["lead_time_days", "minimum_order_quantity"]}],
        "total_purchase_cost": None}, "warnings": [], "query": {}}


def test_purchase_copy_uses_verification_actions_not_procurement_priority():
    evidence = purchase_evidence()
    original = copy.deepcopy(evidence)
    answer = format_business_answer("purchase_planning", evidence)
    assert evidence == original
    assert "TEST-MAT" in answer and "12片" in answer
    assert "建议核实" in answer and "交期" in answer and "未交付订单" in answer
    assert "优先采购" not in answer and "立即采购" not in answer
    assert "供应商 未提供" not in answer and "成本 未提供" not in answer
    assert_business_copy(answer)


def test_email_is_a_draft_without_unverified_supplier_or_date():
    answer = format_purchase_email("purchase_planning", purchase_evidence())
    assert "草稿" in answer and "TEST-MAT" in answer and "12片" in answer
    assert "供应商 未提供" not in answer
    assert "交付时间：" not in answer
    assert_business_copy(answer)


def test_incomplete_production_is_short_and_does_not_assume_a_unit():
    answer = incomplete_production(
        {"status": "data_unavailable", "query": {"product": "TEST-PRODUCT", "quantity": 10},
         "data": {"bom_structure": {"data": {"revision": "V01", "line_count": 2, "parent_item": {}},
                                    "completeness": {"unit": False, "parent_unit": False}}},
         "warnings": ["unit_missing"]}
    )
    assert "V01" in answer and "2项物料" in answer
    assert "单位待确认" in answer
    assert "10台" not in answer and "10片" not in answer
    assert len(answer) < 100
    assert_business_copy(answer)


def test_normal_production_projection_does_not_add_disclaimer_or_change_numbers():
    response = {"intent": "production_capacity", "status": "ok", "answer": "",
                "warnings": [], "structured_data": {"data": {
                    "product": "TEST-PRODUCT", "requested_quantity": 10, "product_unit": "片",
                    "inventory_sufficient_for_requested": False,
                    "materials": [{"material_code": "TEST-MAT", "is_ready": False, "shortage_qty": "12"}]}}}
    original = copy.deepcopy(response)
    projected = present_query(response, "生产10片")
    assert response == original
    assert projected["structured_data"]["data"] == original["structured_data"]["data"]
    assert "TEST-MAT缺12" in projected["answer"]
    assert_business_copy(projected["answer"])


def test_reused_response_is_explicitly_a_prior_analysis():
    response = {"intent": "production_capacity", "status": "ok", "answer": "", "warnings": [],
                "structured_data": {"reused_from_turn_id": "synthetic-turn", "data": {
                    "product": "TEST-PRODUCT", "requested_quantity": 10, "product_unit": "片",
                    "inventory_sufficient_for_requested": True, "materials": []}}}
    projected = present_query(response, "为什么")
    assert projected["answer"].startswith("沿用本会话分析结果")
    assert_business_copy(projected["answer"])
