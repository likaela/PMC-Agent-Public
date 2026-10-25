"""单位体系回归：统一解析、精确换算、维度隔离与缺失单位默认规则。

全部使用合成事实，不读取也不改写真实 Excel、企业库存或发布数据。
"""
from __future__ import annotations

import copy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from services import unit_system as units
from services.book_inventory_service import BookInventoryService


# ---------------------------------------------------------------- 纯单位模块

@pytest.mark.parametrize("raw,name,dimension", [
    ("片", "片", units.COUNT), ("块", "片", units.COUNT), ("个", "片", units.COUNT),
    ("件", "片", units.COUNT), ("套", "套", units.COUNT), ("台", "台", units.COUNT),
    ("公斤", "克", units.WEIGHT), ("KG", "克", units.WEIGHT), ("斤", "克", units.WEIGHT),
    ("克", "克", units.WEIGHT), ("米", "毫米", units.LENGTH), ("厘米", "毫米", units.LENGTH),
    ("毫米", "毫米", units.LENGTH), ("英寸", "毫米", units.LENGTH),
])
def test_units_parse_into_one_registry(raw, name, dimension):
    parsed = units.spec(raw)
    assert parsed is not None and parsed.name == name and parsed.dimension == dimension


def test_count_equivalents_are_business_confirmed_only_for_piece_family():
    for raw in ("片", "块", "个", "件", "PCS"):
        assert units.count_equivalent(raw) is True
    for raw in ("套", "台", "只"):
        assert units.count_equivalent(raw) is False
        # 同一维度但不是一比一等价：必须给出具体原因，而不是静默换算。
        assert units.dimension(raw) == units.COUNT
        assert "物料专属换算规则" in units.conflict_reason(raw, "片")
    for raw in ("箱", "包"):
        # 包装单位单独成维度，同样不得与片直接相加。
        assert units.dimension(raw) == units.PACKAGE
        assert units.conflict_reason(raw, "片")


@pytest.mark.parametrize("value,source,target,expected", [
    ("1", "公斤", "斤", "2"),
    ("1", "斤", "克", "500"),
    ("1", "公斤", "克", "1000"),
    ("2000", "克", "公斤", "2"),
    ("1", "米", "厘米", "100"),
    ("1", "厘米", "毫米", "10"),
    ("1", "米", "毫米", "1000"),
    ("2", "米", "厘米", "200"),
    ("50", "厘米", "米", "0.5"),
    ("100", "块", "片", "100"),
])
def test_weight_and_length_and_count_conversions_are_exact(value, source, target, expected):
    assert units.convert(value, source, target) == Decimal(expected)


def test_conversion_uses_decimal_not_float():
    # 浮点下 0.1+0.2 != 0.3；Decimal 换算必须精确。
    assert units.convert("0.1", "公斤", "克") + units.convert("0.2", "公斤", "克") == Decimal("300")
    assert units.difference("0.3", "公斤", "300", "克") == Decimal("0")


@pytest.mark.parametrize("left,right", [
    ("公斤", "片"), ("米", "个"), ("套", "公斤"), ("吨", "厘米"), ("升", "片"),
])
def test_cross_dimension_units_are_never_convertible(left, right):
    assert units.compatible(left, right) is False
    assert units.convert("1", left, right) is None
    assert units.difference("30", left, "200", right) is None
    assert units.conflict_reason(left, right)


def test_missing_unit_resolution_keeps_source_marker():
    assert units.resolve_missing(None) == ("片", "default_piece")
    assert units.resolve_missing("公斤") == ("公斤", "explicit")
    assert units.resolve_missing("盒装") == ("片", "default_piece")


# ---------------------------------------------------------------- 合成事实

class SyntheticFacts:
    """最小已发布 BOM 事实：单个成品 + 若干叶子物料。"""

    def __init__(self, product, parent_unit, lines):
        self.product, self.parent_unit = product, parent_unit
        self.lines = lines
        self.codes = {code for code, _, _ in lines}
        self.repository = self

    def resolve_item(self, code):
        if code == self.product:
            return {"material_code": code, "unit": self.parent_unit, "name": "合成成品"}
        if code in self.codes:
            return {"material_code": code, "name": code}
        return None

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


def record(code, qty, unit="片", *, batch="SYN-BATCH", location="SYN-LOC", duplicate=False, name=None):
    return dict(material_code=code, name=name or code, recorded_stock_qty=qty, inventory_unit=unit,
                batch_no=batch, location=location, duplicate_observation=duplicate,
                provenance={"source_file": "synthetic-inventory.json", "row_number": 1})


def source_of(records, *, unit_confirmation=None, quantity_confirmation=None):
    return dict(source_file="synthetic-inventory.json", content_sha256="synthetic-only", sheet_name="Synthetic",
                record_count=sum(len(v) for v in records.values()), unique_material_count=len(records),
                duplicate_observation_count=sum(1 for rows in records.values() for r in rows if r["duplicate_observation"]),
                row_errors=[], excluded_sheets=[], unit_confirmation=unit_confirmation,
                quantity_confirmation=quantity_confirmation, records=records)


def book(product, parent_unit, lines, source):
    facts = SyntheticFacts(product, parent_unit, lines)
    return BookInventoryService(facts, SimpleNamespace(load=lambda: copy.deepcopy(source)))


def confirmed(product, unit="片"):
    return {"unit": unit, "product_scope": [{"product": product, "revision": "V01"}]}


# ---------------------------------------------------------------- 计数单位

def test_hundred_pieces_computes_demand_and_shortage():
    source = source_of({"SYN-A": [record("SYN-A", "30")]}, unit_confirmation={"unit": "片"})
    result = book("SYN-P", "片", [("SYN-A", "2", "片")], source).analyze("SYN-P", quantity=100)
    data = result["data"]
    assert result["status"] == "ok"
    assert data["materials"][0]["required_qty"] == "200"
    assert data["materials"][0]["book_shortage_qty"] == "170"
    assert data["materials"][0]["suggested_purchase_qty"] == "170"
    assert data["comparable_material_count"] == 1


def test_hundred_blocks_uses_unified_counting_unit():
    source = source_of({"SYN-A": [record("SYN-A", "30")]}, unit_confirmation={"unit": "片"})
    result = book("SYN-P", "片", [("SYN-A", "2", "片")], source).analyze(
        "SYN-P", quantity=100, requested_unit="块")
    assert result["status"] == "ok"
    row = result["data"]["materials"][0]
    assert row["required_qty"] == "200" and row["book_shortage_qty"] == "170"
    assert result["data"]["requested_unit_source"] == "explicit"


def test_hundred_sets_is_rejected_without_item_conversion_rule():
    source = source_of({"SYN-A": [record("SYN-A", "30")]}, unit_confirmation={"unit": "片"})
    result = book("SYN-P", "片", [("SYN-A", "2", "片")], source).analyze(
        "SYN-P", quantity=100, requested_unit="套")
    assert result["status"] == "invalid_query" and result["data"] is None
    assert "物料专属换算规则" in result["warnings"][0]


def test_hundred_individuals_is_a_legal_count_unit():
    source = source_of({"SYN-A": [record("SYN-A", "30")]}, unit_confirmation={"unit": "片"})
    result = book("SYN-P", "片", [("SYN-A", "2", "片")], source).analyze(
        "SYN-P", quantity=100, requested_unit="个")
    assert result["status"] == "ok"
    assert result["data"]["materials"][0]["book_shortage_qty"] == "170"


# ---------------------------------------------------------------- 重量与长度

def test_weight_demand_in_kilogram_against_gram_stock_converts_exactly():
    source = source_of({"SYN-W": [record("SYN-W", "2000", "克")]}, unit_confirmation={"unit": "克"})
    result = book("SYN-P", "公斤", [("SYN-W", "1", "公斤")], source).analyze(
        "SYN-P", quantity=100, requested_unit="公斤")
    data = result["data"]
    row = data["materials"][0]
    assert result["status"] == "ok"
    assert row["required_qty"] == "100" and row["stock_qty_in_bom_unit"] == "2"
    assert row["book_shortage_qty"] == "98" and row["comparison_status"] == "comparable"


def test_length_demand_in_meter_against_centimeter_stock_converts_exactly():
    source = source_of({"SYN-L": [record("SYN-L", "50", "厘米")]}, unit_confirmation={"unit": "厘米"})
    result = book("SYN-P", "米", [("SYN-L", "1", "米")], source).analyze(
        "SYN-P", quantity=2, requested_unit="米")
    row = result["data"]["materials"][0]
    assert result["status"] == "ok"
    assert row["required_qty"] == "2" and row["stock_qty_in_bom_unit"] == "0.5"
    assert row["book_shortage_qty"] == "1.5"


def test_weight_stock_against_piece_demand_is_not_calculated():
    source = source_of({"SYN-A": [record("SYN-A", "30", "公斤")]}, unit_confirmation={"unit": "公斤"})
    result = book("SYN-P", "片", [("SYN-A", "2", "片")], source).analyze(
        "SYN-P", quantity=100, requested_unit="片")
    data, row = result["data"], result["data"]["materials"][0]
    assert result["status"] == "ok"
    assert row["comparison_status"] == "unit_conflict"
    assert row["book_difference"] is None and row["book_shortage_qty"] is None
    assert row["suggested_purchase_qty"] is None
    assert data["unit_conflict_materials"] == ["SYN-A"]
    assert data["purchase_material_count"] == 0 and data["book_shortages"] == []
    assert data["inventory_sufficient_for_requested"] is None
    assert "不同计量维度" in data["uncomputable_materials"][0]["reason"]


# ---------------------------------------------------------------- 缺失单位

def test_missing_unit_defaults_to_piece_and_keeps_traceable_marker():
    source = source_of({"SYN-A": [record("SYN-A", "30")]}, unit_confirmation={"unit": "片"})
    result = book("SYN-P", "片", [("SYN-A", "2", "片")], source).analyze("SYN-P", quantity=100)
    data = result["data"]
    assert data["requested_unit"] == "片"
    assert data["requested_unit_source"] == "default_piece"
    assert data["demand_quantity"] == "100"
    assert data["materials"][0]["book_shortage_qty"] == "170"


def test_explicit_weight_unit_is_never_replaced_by_piece_default():
    source = source_of({"SYN-W": [record("SYN-W", "500", "克")]}, unit_confirmation={"unit": "克"})
    result = book("SYN-P", "公斤", [("SYN-W", "1", "公斤")], source).analyze(
        "SYN-P", quantity=2, requested_unit="公斤")
    data = result["data"]
    assert data["requested_unit"] == "公斤" and data["requested_unit_source"] == "explicit"
    assert data["requested_quantity"] == 2 and data["demand_quantity"] == "2"
    assert data["materials"][0]["book_shortage_qty"] == "1.5"


def test_weight_product_defaults_to_its_own_unit_not_piece():
    source = source_of({"SYN-W": [record("SYN-W", "500", "克")]}, unit_confirmation={"unit": "克"})
    result = book("SYN-P", "公斤", [("SYN-W", "1", "公斤")], source).analyze("SYN-P", quantity=3)
    data = result["data"]
    assert data["requested_unit"] == "公斤"
    assert data["requested_unit_source"] == "inherited_product_unit"


# ---------------------------------------------------------------- 库存侧单位冲突

def test_missing_stock_unit_without_confirmation_is_uncomputable():
    source = source_of({"SYN-A": [record("SYN-A", "30", None)]})
    result = book("SYN-P", "片", [("SYN-A", "2", "片")], source).analyze("SYN-P", quantity=100)
    row = result["data"]["materials"][0]
    assert row["comparison_status"] == "unit_unknown"
    assert row["book_shortage_qty"] is None and row["suggested_purchase_qty"] is None
    assert result["data"]["unit_unknown_materials"] == ["SYN-A"]


def test_row_level_unit_confirmation_makes_missing_cell_verifiable():
    rec = record("SYN-A", "30", None)
    rec["provenance"] = {**rec["provenance"], "unit_confirmation": {"unit": "片"}}
    source = source_of({"SYN-A": [rec]})
    result = book("SYN-P", "片", [("SYN-A", "2", "片")], source).analyze("SYN-P", quantity=100)
    row = result["data"]["materials"][0]
    assert row["comparison_status"] == "comparable"
    assert row["stock_unit"] == "片" and row["stock_unit_basis"] == "source_confirmed"


def test_conflicting_units_inside_one_material_are_uncomputable():
    source = source_of({"SYN-A": [record("SYN-A", "10", "片", batch="B1"),
                                  record("SYN-A", "5", "公斤", batch="B2")]})
    result = book("SYN-P", "片", [("SYN-A", "2", "片")], source).analyze("SYN-P", quantity=10)
    row = result["data"]["materials"][0]
    assert row["comparison_status"] == "unit_unknown"
    assert "不一致" in row["uncomputable_reason"]
    assert row["book_shortage_qty"] is None
