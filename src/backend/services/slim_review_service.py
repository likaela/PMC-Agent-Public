"""Versioned, private filtered review datasets. Never rewrites enterprise facts."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path

from services.book_inventory_service import BookInventoryService

REAL_SLIM = "kq_z112_review_slim"
SYNTHETIC_SLIM = "kq_z112_review_slim_synthetic"
SLIM_SCENARIOS = (REAL_SLIM, SYNTHETIC_SLIM)


def select_dataset(service, product, revision):
    """Retain verified aggregated demands supporting at least one finished unit."""
    result = service.analyze(product, revision, 1)
    if result["status"] != "ok" or not result["data"].get("quantity_review"):
        raise ValueError("Requires source-bound actual quantity and unit confirmations")
    bom = service.facts.query_bom(product, revision)
    source = service.observations.load()
    kept, excluded, decisions = [], [], []
    for row in result["data"]["materials"]:
        reason, capacity, coverage = None, None, None
        try:
            per = Decimal(row["qty_per"])
        except (InvalidOperation, TypeError, ValueError):
            per = Decimal("NaN")
        try:
            stock = Decimal(row["recorded_stock_qty"]) if row["recorded_stock_qty"] is not None else None
        except (InvalidOperation, TypeError, ValueError):
            stock = None
        if not row["material_code"] or not per.is_finite() or per <= 0:
            reason = "invalid_material_or_bom_usage"
        elif not row["record_found"]:
            reason = "no_main_inventory_record"
        elif not row["duplicate_free"]:
            reason = "unresolved_duplicate_observations"
        elif stock is None or not stock.is_finite():
            reason = "invalid_stock_quantity"
        elif row["unit"] != "片" or row["inventory_unit"] != "片":
            reason = "unit_not_confirmed_as_pian"
        elif stock < 0:
            reason = "negative_stock_quantity"
        else:
            # BookInventoryService has already combined all same-item BOM usages
            # and only summed source records whose identities are verified.
            coverage = stock / per
            capacity = int(coverage.to_integral_value(rounding=ROUND_FLOOR))
            if capacity < 1:
                reason = "zero_stock_quantity" if stock == 0 else "insufficient_stock_for_one_unit"
        audit = dict(material_code=row["material_code"], qty_per=row["qty_per"],
            stock_qty=row["recorded_stock_qty"], capacity=capacity,
            coverage_ratio=None if coverage is None else format(coverage, "f"),
            retained=reason is None, reason=reason,
            bom_sources=row["bom_line_sources"], stock_sources=row["stock_sources"])
        decisions.append(audit)
        if reason:
            excluded.append({**audit, "name": row["name"]})
        else:
            kept.append(copy.deepcopy(row))
    if not kept:
        raise ValueError("No verified materials remain; capacity cannot be calculated")
    codes = {r["material_code"] for r in kept}
    filtered_bom = copy.deepcopy(bom)
    filtered_bom["data"]["lines"] = [l for l in bom["data"]["lines"] if l["child_item"]["material_code"] in codes]
    filtered_bom["data"]["line_count"] = len(filtered_bom["data"]["lines"])
    filtered_source = copy.deepcopy(source)
    filtered_source["records"] = {c: copy.deepcopy(source["records"][c]) for c in codes}
    aliases = {code: f"MAT-{i:03d}" for i, code in enumerate(sorted(r["material_code"] for r in result["data"]["materials"]), 1)}
    kept_decisions = [r for r in decisions if r["retained"]]
    return dict(format_version=2, product=product, revision=revision, original_material_count=len(result["data"]["materials"]),
        retained_material_count=len(kept), excluded=excluded, bom=filtered_bom, observations=filtered_source,
        synthetic=False, scenario_id=REAL_SLIM, scenario_name=f"{product} 精简评审子集（真实库存）",
        selection_policy="verified_aggregate_capacity_gte_one", selection_audit=decisions, material_aliases=aliases,
        selection_max_quantity=min(r["capacity"] for r in kept_decisions),
        minimum_coverage_ratio=min((r["coverage_ratio"] for r in kept_decisions), key=Decimal),
        zero_stock_retained=[r["material_code"] for r in kept if Decimal(r["recorded_stock_qty"]) == 0])


def synthetic_dataset(dataset, target_quantity=None):
    if target_quantity is None:
        target_quantity = dataset["selection_max_quantity"] * 2
    if isinstance(target_quantity, bool) or not isinstance(target_quantity, int) or target_quantity <= 0:
        raise ValueError("Synthetic target must be a positive integer")
    result = copy.deepcopy(dataset)
    result.update(synthetic=True, scenario_id=SYNTHETIC_SLIM, scenario_name=f"{dataset['product']} 精简评审子集（合成补充）",
        synthetic_target_quantity=target_quantity)
    usage = {}
    for line in result["bom"]["data"]["lines"]:
        code = line["child_item"]["material_code"]
        usage[code] = usage.get(code, Decimal("0")) + Decimal(line["qty_per"])
    supplements = []
    for code, per in usage.items():
        original = BookInventoryService._stock(code, result["observations"])
        amount = max(per * target_quantity - Decimal(original["recorded_stock_qty"]), Decimal("0"))
        if amount > 0:
            supplements.append(dict(material_code=code, original_stock_qty=original["recorded_stock_qty"],
                synthetic_added_qty=format(amount, "f"), target_quantity=target_quantity))
            result["observations"]["records"][code].append(dict(material_code=code, name=original["name"],
                recorded_stock_qty=format(amount, "f"), inventory_unit="片", location="合成精简补充",
                batch_no="SYNTHETIC-SLIM", site=None, duplicate_observation=False,
                provenance=dict(source_file="synthetic-slim-supplement", source_kind="synthetic_review", synthetic=True,
                    scenario_id=SYNTHETIC_SLIM, original_stock_qty=original["recorded_stock_qty"],
                    synthetic_added_qty=format(amount, "f"), snapshot_at=None)))
    result["supplements"] = supplements
    return result


class DatasetFacts:
    def __init__(self, facts, dataset):
        self.repository, self.dataset = facts.repository, dataset

    def query_bom(self, product, revision):
        if product != self.dataset["product"] or revision != self.dataset["revision"]:
            raise ValueError("Product outside filtered dataset")
        return copy.deepcopy(self.dataset["bom"])


class DatasetObservations:
    def __init__(self, dataset):
        self.dataset = dataset

    def load(self):
        if os.getenv("PMC_TRIAL_MODE") == "1":
            raise PermissionError("Private filtered scenarios are not public trial data")
        result = copy.deepcopy(self.dataset["observations"])
        result["review_scenario"] = dict(scenario_id=self.dataset["scenario_id"], synthetic=self.dataset["synthetic"],
            filtered=True, dataset_version=self.dataset.get("dataset_version"), label=self.dataset["scenario_name"])
        return result


class SlimReviewService(BookInventoryService):
    def __init__(self, facts, dataset):
        self.dataset = dataset
        super().__init__(DatasetFacts(facts, dataset), DatasetObservations(dataset))

    def analyze(self, product, revision=None, quantity=None, requested_unit=None):
        if product not in (self.dataset["product"], self.dataset.get("display_product_code")) or revision not in (None, self.dataset["revision"]):
            return self._result("data_unavailable", dict(product=product, revision=revision), None, {}, None,
                ["当前为独立精简评审场景；请切回企业空间查询完整产品。"])
        result = super().analyze(self.dataset["product"], self.dataset["revision"], quantity, requested_unit)
        if result["status"] != "ok":
            return result
        data = result["data"]
        rows = data["materials"]
        limits = []
        for row in rows:
            per, stock = Decimal(row["qty_per"]), row["recorded_stock_qty"]
            if per <= 0 or stock is None or not per.is_finite() or row["inventory_unit"] != row["unit"]:
                return self._result("data_unavailable", result["query"], data, {}, result["provenance"], ["精简数据存在不可比较记录，未计算产能。"])
            # Integer quotient, no float/LLM rounding, a real zero remains zero.
            limit = int(Decimal(stock) // per)
            row["max_supported_quantity"] = limit
            limits.append(limit)
        if not limits:
            return self._result("data_unavailable", result["query"], data, {}, result["provenance"], ["没有有效物料，不能计算最大数量。"])
        maximum = min(limits)
        data.update(theoretical_max_quantity=maximum, max_producible_quantity=maximum,
            limiting_materials=[r for r in rows if r["max_supported_quantity"] == maximum],
            slim_review=True, scenario_name=self.dataset["scenario_name"], scenario_id=self.dataset["scenario_id"],
            scenario_product_code=self.dataset.get("display_product_code"), synthetic=self.dataset["synthetic"],
            original_material_count=self.dataset["original_material_count"], excluded_material_count=len(self.dataset["excluded"]),
            dataset_version=self.dataset.get("dataset_version"), formal_production_approved=False)
        result["provenance"]["filtered_dataset"] = dict(scenario_id=data["scenario_id"],
            dataset_version=data["dataset_version"], content_sha256=self.dataset.get("dataset_sha256"),
            retained_material_count=len(rows), original_material_count=data["original_material_count"], synthetic=data["synthetic"])
        result["warnings"] = ["仅为筛选后物料集合的齐套结论，不是原始完整BOM生产放行。"]
        return result


def load_dataset(path):
    path = Path(path)
    raw = path.read_bytes()
    data = json.loads(raw)
    if data.get("scenario_id") not in SLIM_SCENARIOS or data.get("format_version") not in (1, 2):
        raise ValueError("Invalid filtered dataset")
    data["dataset_sha256"] = hashlib.sha256(raw).hexdigest()
    return data
