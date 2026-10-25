"""库存质量与采购结论回归：真零库存、无记录、重复记录、单位冲突与回答一致性。

全部使用合成事实；不读取或改写真实企业库存、BOM 或发布文件。
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from services.book_inventory_service import BookInventoryService
from services.pmc_capability_service import PmcCapabilityQueries

PRODUCT = "SYN-P"


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


def record(code, qty, unit="片", *, batch="SYN-BATCH", location="SYN-LOC", duplicate=False):
    return dict(material_code=code, name=code, recorded_stock_qty=qty, inventory_unit=unit,
                batch_no=batch, location=location, duplicate_observation=duplicate,
                provenance={"source_file": "synthetic-inventory.json", "row_number": 1})


def source_of(records, *, unit_confirmation={"unit": "片"}, quantity_confirmation=None):
    return dict(source_file="synthetic-inventory.json", content_sha256="synthetic-only", sheet_name="Synthetic",
                record_count=sum(len(v) for v in records.values()), unique_material_count=len(records),
                duplicate_observation_count=sum(1 for rows in records.values() for r in rows if r["duplicate_observation"]),
                row_errors=[], excluded_sheets=[], unit_confirmation=unit_confirmation,
                quantity_confirmation=quantity_confirmation, records=records)


def build(lines, records, **source):
    facts = SyntheticFacts(PRODUCT, "片", lines)
    source_obj = copy.deepcopy(source_of(records, **source))
    return BookInventoryService(facts, SimpleNamespace(load=lambda: copy.deepcopy(source_obj)))


def queries(book):
    """只装配确定性问答层，不接触任何模型或真实业务库。"""
    return PmcCapabilityQueries(SimpleNamespace(book_service=book, presentation=None))


CONFIRMED = {"unit": "片", "product_scope": [{"product": PRODUCT, "revision": "V01"}]}


# ---------------------------------------------------------------- 真实零库存 vs 无记录

def test_zero_stock_differs_from_missing_record():
    book = build([("SYN-A", "2", "片"), ("SYN-B", "1", "片")],
                 {"SYN-A": [record("SYN-A", "0")]}, quantity_confirmation=CONFIRMED)
    data = book.analyze(PRODUCT, quantity=10)["data"]
    zero = next(r for r in data["materials"] if r["material_code"] == "SYN-A")
    missing = next(r for r in data["materials"] if r["material_code"] == "SYN-B")
    # 真实零库存：可核算，缺口等于需求。
    assert zero["comparison_status"] == "comparable" and zero["recorded_stock_qty"] == "0"
    assert zero["book_shortage_qty"] == "20" and zero["suggested_purchase_qty"] == "20"
    # 没有记录：保持未知，不补零、不生成确定采购数量。
    assert missing["comparison_status"] == "no_stock" and missing["recorded_stock_qty"] is None
    assert missing["book_shortage_qty"] is None and missing["suggested_purchase_qty"] is None
    assert data["missing_stock_materials"] == ["SYN-B"]
    assert data["purchase_material_count"] == 1


# ---------------------------------------------------------------- 重复记录

def duplicate_records():
    return {"SYN-A": [record("SYN-A", "10", duplicate=True, location="L1"),
                      record("SYN-A", "5", duplicate=True, location="L1")]}


def test_unverified_duplicates_are_not_max_merged_or_purchased():
    book = build([("SYN-A", "1", "片")], duplicate_records())
    data = book.analyze(PRODUCT, quantity=20)["data"]
    row = data["materials"][0]
    assert row["comparison_status"] == "duplicate_unverified"
    assert row["recorded_stock_qty"] is None and row["duplicate_basis"] == "unverified"
    assert row["book_shortage_qty"] is None and row["suggested_purchase_qty"] is None
    assert data["duplicate_unverified_materials"] == ["SYN-A"]
    assert data["purchase_material_count"] == 0 and data["book_shortages"] == []


def test_source_confirmed_caliber_may_merge_duplicates_by_max():
    book = build([("SYN-A", "1", "片")], duplicate_records(), quantity_confirmation=CONFIRMED)
    data = book.analyze(PRODUCT, quantity=20)["data"]
    row = data["materials"][0]
    assert row["comparison_status"] == "comparable" and row["duplicate_verified"] is True
    assert row["recorded_stock_qty"] == "10" and row["book_shortage_qty"] == "10"


def test_duplicate_rows_with_conflicting_quantities_never_pick_one_row():
    records = {"SYN-A": [record("SYN-A", "10", location="L1"), record("SYN-A", "3", location="L1")]}
    book = build([("SYN-A", "1", "片")], records)
    data = book.analyze(PRODUCT, quantity=5)["data"]
    row = data["materials"][0]
    assert row["recorded_stock_qty"] is None
    assert row["book_shortage_qty"] is None and row["suggested_purchase_qty"] is None


# ---------------------------------------------------------------- 部分可核算

def partial_book():
    return build(
        [("SYN-A", "1", "片"), ("SYN-B", "1", "片"), ("SYN-C", "1", "片")],
        {"SYN-A": [record("SYN-A", "2")], "SYN-C": [record("SYN-C", "5", "公斤")]},
        quantity_confirmation=CONFIRMED)


def test_partial_bom_keeps_known_gap_and_withholds_unverified():
    turn = queries(partial_book())._production_plan("投产10片生成采购BOM表单", {}, [], PRODUCT, 10)
    response = turn.response
    data = response["structured_data"]["data"]
    assert response["status"] == "ok"
    # 只有可核算物料生成确定采购量。
    assert [r["material"] for r in data["recommendations"]] == ["SYN-A"]
    assert data["recommendations"][0]["suggested_purchase_qty"] == "8"
    assert data["purchase_material_count"] == 1
    # 无记录与单位冲突都进入待核验清单，且不带采购数量。
    unverified = {row["material"]: row for row in data["unverified_purchase_materials"]}
    assert set(unverified) == {"SYN-B", "SYN-C"}
    assert all(row["suggested_purchase_qty"] is None and row["shortage_qty"] is None for row in unverified.values())
    assert "无记录" in unverified["SYN-B"]["reason"]
    assert "计量单位冲突" in unverified["SYN-C"]["reason"]
    # 已确认的缺口足以判定“不满足”，但未知/冲突部分仍未生成采购量。
    assert data["inventory_sufficient_for_requested"] is False
    assert data["quantity_check_status"] == "insufficient"
    answer = response["answer"]
    # 回答与结构化结果一致：不虚构未知物料的采购数量。
    assert "2种物料未生成确定缺口与采购数量" in answer
    assert "共 1 种物料需采购" in answer


def test_answer_never_turns_missing_record_into_definite_purchase():
    book = build([("SYN-B", "1", "片")], {}, quantity_confirmation=CONFIRMED)
    response = queries(book)._shortage("生产10片哪些物料缺料", {}, [], PRODUCT, 10).response
    answer = response["answer"]
    assert response["status"] == "ok"
    assert "未生成确定采购数量" in answer
    assert "需按需求数量采购" not in answer
    data = response["structured_data"]["data"]
    assert data["purchase_material_count"] == 0 and data["book_shortages"] == []


def test_shortage_answer_lists_unit_conflict_without_fake_gap():
    book = build([("SYN-C", "1", "片")], {"SYN-C": [record("SYN-C", "5", "公斤")]},
                 quantity_confirmation=CONFIRMED)
    response = queries(book)._shortage("生产10片哪些物料缺料", {}, [], PRODUCT, 10).response
    answer = response["answer"]
    assert "不可核算" in answer and "未生成确定缺口与采购数量" in answer
    assert "缺口合计" not in answer  # 没有可核算缺口时不得出现缺口合计


def test_standalone_material_without_record_does_not_claim_purchase():
    book = build([("SYN-B", "1", "片")], {})
    response = queries(book)._material_coverage(
        "SYN-B 是否缺料", {}, [], {"material_code": "SYN-B", "name": "SYN-B", "records": []}, None).response
    answer = response["answer"]
    assert "库存无记录" in answer
    assert "未生成确定采购数量" in answer
    assert "需按需求数量采购" not in answer
    assert response["structured_data"]["data"]["comparison_status"] == "no_stock"


# ---------------------------------------------------------------- 结构化结果自洽

@pytest.mark.parametrize("records,expected", [
    ({"SYN-A": [record("SYN-A", "5")]}, "insufficient"),
    ({"SYN-A": [record("SYN-A", "100000")]}, "sufficient"),
    ({}, "insufficient"),
    ({"SYN-A": [record("SYN-A", "30", "公斤")]}, "unverified"),
])
def test_purchase_list_matches_structured_counts(records, expected):
    book = build([("SYN-A", "2", "片")], records, quantity_confirmation=CONFIRMED)
    data = book.analyze(PRODUCT, quantity=10)["data"]
    assert data["quantity_check_status"] == expected
    purchase = [r for r in data["materials"] if r["suggested_purchase_qty"] is not None]
    assert len(purchase) == data["purchase_material_count"] == len(data["recommendations"])
    assert all(row["suggested_purchase_qty"] for row in data["recommendations"])
    # 不可核算物料绝不进入采购建议。
    for row in data["uncomputable_materials"]:
        assert row["status"] != "comparable"
    assert len(data["uncomputable_materials"]) == data["uncomputable_material_count"]
