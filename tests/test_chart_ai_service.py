"""Synthetic-only regressions for the real chart interpretation boundary."""
import copy
import json

import pytest

from services.chart_ai_service import enhance_chart, chart_projection


def production(rows=None, *, quantity=10, total=None, result_type="production"):
    rows = rows if rows is not None else [material("TEST-MAT-A", "10", "12", "0")]
    return dict(intent="production_capacity", status="ok", answer="已完成数量核对。",
        structured_data=dict(query_type="production_capacity", presentation={"result_type": result_type},
            data=dict(product="TEST-PRODUCT-01", product_name="合成成品", product_unit="片",
                requested_quantity=quantity, total_material_count=total or len(rows), materials=rows,
                source_summary={"source_file": "PRIVATE-SOURCE.xlsx", "sha256": "f" * 64,
                                "path": "C:/PRIVATE/ONLY"}),
            audit_warnings=["INTERNAL-AUDIT-ONLY"]),
        provenance={"source_file": "PRIVATE-SOURCE.xlsx"}, warnings=[])


def material(code, demand, stock, gap, *, matched=True):
    return dict(material_code=code, name="合成物料", unit="片", inventory_unit="片",
        required_qty=demand, recorded_stock_qty=stock, book_shortage_qty=gap,
        comparison_status="comparable" if matched else "no_stock", record_found=matched,
        duplicate_free=True, stock_sources=[{"source_file": "DO-NOT-EXPORT.xlsx"}])


class Model:
    def __init__(self, mutate=None, error=None):
        self.mutate, self.error, self.calls = mutate, error, []

    def with_structured_output(self, schema, method):
        assert method == "json_mode"
        self.schema = schema
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        payload = json.loads(messages[-1][1])
        reply = dict(verdict=payload["verdict"], finding_ids=payload["required_finding_ids"],
            impact_id=payload["impact_id"], action_ids=[next(iter(payload["action_bank"]))])
        return self.mutate(reply, payload) if self.mutate else reply


def analyze(response, model=None):
    model = model or Model()
    return enhance_chart(response, llm_factory=lambda: model, llm_status=lambda: {"status": "ok"})


def test_sufficient_chart_has_real_model_call_and_four_sections_without_mutating_business():
    response, model = production(), Model()
    original = copy.deepcopy(response)
    result = analyze(response, model)
    assert len(model.calls) == 1
    assert response == original
    assert result["answer"] == response["answer"]
    assert result["structured_data"]["data"] == response["structured_data"]["data"]
    ai = result["structured_data"]["chart_ai"]
    assert ai["source"] == "llm" and ai["status"] == "ok"
    assert set(ai["sections"]) == {"conclusion", "findings", "impact", "actions"}
    assert "满足生产10片" in ai["sections"]["conclusion"]


def test_one_shortage_keeps_tool_numbers_and_stops_unsupported_purchase_ordering():
    result = analyze(production([material("TEST-MAT-A", "10", "6", "4")]))
    section = result["structured_data"]["chart_ai"]["sections"]
    assert "1项物料缺口" in section["conclusion"]
    assert "缺口4片" in "".join(section["findings"])
    assert "优先采购" not in json.dumps(section, ensure_ascii=False)
    assert "立即采购" not in json.dumps(section, ensure_ascii=False)
    assert all("核实" in text or "评估" in text for text in section["actions"])


def test_multiple_shortages_are_counted_without_inventing_prices_or_dates():
    result = analyze(production([material("TEST-MAT-A", "10", "6", "4"),
                                 material("TEST-MAT-B", "20", "1", "19")]))
    assert "2项物料缺口" in result["structured_data"]["chart_ai"]["sections"]["conclusion"]
    assert "价格" not in json.dumps(result["structured_data"]["chart_ai"], ensure_ascii=False)


def test_missing_inventory_stays_null_instead_of_becoming_zero_or_shortage():
    response = production([material("TEST-MAT-A", "10", "12", "0"),
                           material("TEST-MAT-B", "10", None, None, matched=False)])
    payload = chart_projection(response)
    assert payload["materials"][1]["stock_qty"] is None
    assert payload["materials"][1]["shortage_qty"] is None
    result = analyze(response)
    section = result["structured_data"]["chart_ai"]["sections"]
    assert "已核算1项" in section["conclusion"]
    assert "库存待匹配" in section["impact"]
    assert "物料缺口" not in section["conclusion"]


def test_partial_coverage_never_reads_legacy_false_as_known_shortage():
    response = production([material("TEST-MAT-A", "10", "12", "0")], total=3)
    response["structured_data"]["data"]["inventory_sufficient_for_requested"] = False
    result = analyze(response)
    section = result["structured_data"]["chart_ai"]["sections"]
    assert "2项库存待匹配" in section["impact"]
    assert "当前库存满足" not in section["conclusion"]


def test_no_matched_rows_exposes_business_state_without_fabricated_zero_stock():
    result = analyze(production([material("TEST-MAT-A", "10", None, None, matched=False)]))
    section = result["structured_data"]["chart_ai"]["sections"]
    assert "库存待匹配" in section["impact"]
    assert "缺口0" not in json.dumps(section, ensure_ascii=False)


@pytest.mark.parametrize("kind", ["bom", "inventory", "catalog", "purchase", "maximum", "source"])
def test_unrelated_queries_do_not_call_chart_model(kind):
    model = Model(error=AssertionError("must not call"))
    result = analyze(production(result_type=kind), model)
    assert not model.calls
    assert result["structured_data"]["chart_ai"]["status"] == "not_applicable"


@pytest.mark.parametrize("quantity", [None, 0, -1, True, "10"])
def test_absent_or_invalid_target_never_calls_chart_model(quantity):
    model = Model(error=AssertionError("must not call"))
    result = analyze(production(quantity=quantity), model)
    assert not model.calls
    assert result["structured_data"]["chart_ai"]["status"] == "not_applicable"


@pytest.mark.parametrize("mutation", [
    lambda r, p: {**r, "verdict": "insufficient"},
    lambda r, p: {**r, "impact_id": "target_shortage"},
    lambda r, p: {**r, "finding_ids": ["invented-material"]},
    lambda r, p: {**r, "finding_ids": ["coverage", "coverage"]},
    lambda r, p: {**r, "action_ids": ["purchase_first"]},
    lambda r, p: {**r, "answer": "库存9999片"},
    lambda r, p: {**r, "quantity": 9999},
    lambda r, p: "not JSON",
])
def test_contradiction_unknown_reference_or_unconstrained_fact_is_rejected(mutation):
    response = production()
    result = analyze(response, Model(mutation))
    assert result["status"] == "ok"
    assert result["answer"] == response["answer"]
    assert result["structured_data"]["chart_ai"]["status"] == "unavailable"
    assert result["structured_data"]["chart_ai"]["sections"] is None


def test_timeout_preserves_the_completed_business_result():
    response = production()
    result = analyze(response, Model(error=TimeoutError("test timeout")))
    ai = result["structured_data"]["chart_ai"]
    assert ai["error_code"] == "MODEL_TIMEOUT"
    assert result["answer"] == response["answer"]
    assert result["structured_data"]["data"] == response["structured_data"]["data"]


def test_unconfigured_model_is_explicit_without_silent_llm_success():
    result = enhance_chart(production(), llm_status=lambda: {"status": "warning"},
        llm_factory=lambda: (_ for _ in ()).throw(AssertionError("must not construct")))
    assert result["structured_data"]["chart_ai"]["status"] == "unavailable"
    assert result["structured_data"]["chart_ai"]["source"] != "llm"


def test_repeated_analysis_numbers_are_stable():
    response = production([material("TEST-MAT-A", "10.50", "6.25", "4.25")])
    a, b = analyze(response), analyze(response)
    assert a["structured_data"]["chart_ai"]["sections"] == b["structured_data"]["chart_ai"]["sections"]
    assert "4.25片" in "".join(a["structured_data"]["chart_ai"]["sections"]["findings"])


def test_explicit_unmatched_stock_and_unconfirmed_duplicates_are_not_chart_quantities():
    rows = [material("TEST-MAT-A", "10", "100", "0", matched=False),
            material("TEST-MAT-B", "10", "100", "0")]
    rows[1]["duplicate_free"] = False
    projected = chart_projection(production(rows))
    assert all(row["stock_qty"] is None for row in projected["materials"])
    assert projected["verdict"] == "partial"


def test_normalized_stock_wins_over_incompatible_raw_quantity():
    row = material("TEST-MAT-A", "2", "2000", "0")
    row.update(unit="公斤", inventory_unit="克", stock_qty_in_bom_unit="2")
    projected = chart_projection(production([row]))
    assert projected["materials"][0]["stock_qty"] == "2"
    assert projected["verdict"] == "sufficient"


def test_outbound_projection_excludes_source_metadata_history_and_whole_evidence():
    response, model = production(), Model()
    response["structured_data"]["evidence"] = [{"private": "FULL-EVIDENCE-ONLY"}]
    response["structured_data"]["conversation"] = ["HISTORY-ONLY"]
    analyze(response, model)
    exported = json.dumps(model.calls, ensure_ascii=False)
    for forbidden in ("PRIVATE-SOURCE", "DO-NOT-EXPORT", "C:/PRIVATE", "f" * 64,
                      "INTERNAL-AUDIT-ONLY", "FULL-EVIDENCE-ONLY", "HISTORY-ONLY"):
        assert forbidden not in exported
    payload = json.loads(model.calls[0][-1][1])
    assert payload["chart"]["product_code"] == "TEST-PRODUCT-01"
    assert payload["chart"]["materials"][0]["required_qty"] == "10"
    assert set(payload["chart"]["materials"][0]) == {"id", "material_code", "name", "unit", "required_qty",
        "stock_qty", "stock_basis", "shortage_qty", "matched", "comparison_status"}
    assert "JSON Schema" in model.calls[0][0][1]


def test_largest_same_unit_gap_must_be_included_without_claiming_purchase_priority():
    response = production([material("TEST-MAT-A", "10", "6", "4"),
                           material("TEST-MAT-B", "20", "1", "19")])
    result = analyze(response)
    findings = "".join(result["structured_data"]["chart_ai"]["sections"]["findings"])
    assert "TEST-MAT-B" in findings and "缺口19片" in findings
    assert "优先" not in findings
    rejected = analyze(response, Model(lambda r, p: {**r, "finding_ids": ["coverage", "m0"]}))
    assert rejected["structured_data"]["chart_ai"]["status"] == "unavailable"


def test_partial_with_a_known_shortage_keeps_both_business_states():
    response = production([material("TEST-MAT-A", "10", "6", "4"),
                           material("TEST-MAT-B", "10", None, None, matched=False)])
    result = analyze(response)
    section = result["structured_data"]["chart_ai"]["sections"]
    assert "1项物料缺口" in section["conclusion"]
    assert "1项库存待匹配" in section["impact"]
    assert "暂不参与核算" in "".join(section["findings"])


def test_missing_demand_row_cannot_turn_partial_coverage_into_all_ready():
    rows = [material("TEST-MAT-A", "10", "12", "0"), material("TEST-MAT-B", None, "12", None)]
    payload = chart_projection(production(rows))
    assert len(payload["materials"]) == 1
    assert payload["total_material_count"] == 2
    assert payload["unmatched_material_count"] == 1
    assert payload["verdict"] == "partial"


def test_quantity_difference_is_not_recomputed_from_raw_source_stock():
    row = material("TEST-MAT-A", "2", "2000", "0")
    row.update(unit="公斤", inventory_unit="克", stock_qty_in_bom_unit="2")
    result = analyze(production([row]))
    assert result["structured_data"]["chart_ai"]["status"] == "ok"
    assert result["structured_data"]["data"]["materials"][0]["recorded_stock_qty"] == "2000"


def test_no_data_or_failed_business_query_does_not_produce_model_success():
    response, model = production(), Model()
    response["status"] = "query_failed"
    result = analyze(response, model)
    assert not model.calls and result["status"] == "query_failed"
    assert result["structured_data"]["chart_ai"]["source"] != "llm"
    response = production()
    response["structured_data"]["data"]["materials"] = []
    assert analyze(response)["structured_data"]["chart_ai"]["status"] == "not_applicable"


def test_json_schema_response_is_validated_even_when_provider_returns_json_text():
    result = analyze(production(), Model(lambda r, p: json.dumps(r)))
    assert result["structured_data"]["chart_ai"]["status"] == "ok"
