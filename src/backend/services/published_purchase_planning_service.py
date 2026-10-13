"""v1.12 已发布事实采购建议。复用 BOM/供给服务，不写订单或生产决策。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
from math import gcd, lcm
from typing import Any

from fact_layer.query_models import FactQueryResult
from repositories.published_purchase_repository import PublishedPurchaseRepository
from services.fact_query_service import FactQueryService
from services.published_inventory_analysis_service import AvailabilityBasis, PublishedInventoryAnalysisService
from services.published_planning_service import PublishedPlanningService


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("数量格式无效")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("数量格式无效") from error
    if not number.is_finite() or number < 0:
        raise ValueError("数量必须有限且非负")
    return number


def _text(number: Decimal | None) -> str | None:
    return format(number, "f") if number is not None else None


def _at(value: object) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("缺少时区")
    return result.astimezone(UTC)


def _day(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _active(row: dict[str, Any], day: date) -> bool:
    first = _day(row["effective_from"])
    last = _day(row["effective_to"]) if row["effective_to"] is not None else None
    if last is not None and last < first:
        raise ValueError("生效期无效")
    return first <= day and (last is None or day <= last)


def _source(row: dict[str, Any] | None) -> dict[str, Any] | None:
    return {key: row[key] for key in (
        "source_file", "content_sha256", "import_batch_id", "sheet_name", "row_number",
    ) if key in row} if row else None


def _common_multiple(multiples: list[Decimal]) -> Decimal | None:
    if not multiples:
        return None
    fractions = [Fraction(number) for number in multiples]
    numerator, denominator = fractions[0].numerator, fractions[0].denominator
    for number in fractions[1:]:
        numerator = lcm(numerator, number.numerator)
        denominator = gcd(denominator, number.denominator)
    return Decimal(numerator) / Decimal(denominator)


def _round_quantity(net: Decimal, moq: Decimal, multiple: Decimal | None) -> Decimal:
    if net == 0:
        return Decimal("0")
    base = max(net, moq)
    if multiple is None:
        return base
    fraction = Fraction(base) / Fraction(multiple)
    count = (fraction.numerator + fraction.denominator - 1) // fraction.denominator
    return Decimal(count) * multiple


class PublishedPurchasePlanningService:
    def __init__(self, repository: PublishedPurchaseRepository) -> None:
        self.repository = repository
        self.bom = PublishedPlanningService(FactQueryService(repository))
        self.inventory = PublishedInventoryAnalysisService(repository)

    def query(self, product: str, revision: str, effective_date: str, quantity: int, start_date: str,
              *, as_of: str | None = None, suppliers: dict[str, str] | None = None,
              availability_basis: AvailabilityBasis = "source_only") -> FactQueryResult:
        # NUMERIC(38,12) 数量×价格保留足够有效位；不按未知币种猜测货币舍入规则。
        with localcontext() as context:
            context.prec = 96
            return self._query(product, revision, effective_date, quantity, start_date,
                               as_of=as_of, suppliers=suppliers, availability_basis=availability_basis)

    def _query(self, product: str, revision: str, effective_date: str, quantity: int, start_date: str,
               *, as_of: str | None, suppliers: dict[str, str] | None,
               availability_basis: AvailabilityBasis) -> FactQueryResult:
        query = dict(product=product, revision=revision, effective_date=effective_date, quantity=quantity,
                     start_date=start_date, as_of=as_of, suppliers=suppliers, availability_basis=availability_basis)
        try:
            at = _at(as_of) if as_of is not None else datetime.now(UTC)
            start = _day(start_date)
            if not isinstance(start_date, str) or start_date != start.isoformat() or start < at.date():
                raise ValueError("开工日无效")
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                raise ValueError("生产数量无效")
            if availability_basis not in ("source_only", "on_hand_minus_reserved"):
                raise ValueError("可用量口径无效")
            if suppliers is not None and (not isinstance(suppliers, dict) or any(
                not isinstance(code, str) or not code.strip() or not isinstance(supplier, str) or not supplier.strip()
                for code, supplier in suppliers.items()
            )):
                raise ValueError("供应商选择无效")
        except (ValueError, TypeError, OverflowError):
            return self._result("invalid_query", query, None, {}, None, ["请明确生产数量、开工日期、带时区时点和精确供应商选择"])
        query["as_of"] = at.isoformat()
        plan = self.bom.query(product, revision, effective_date, requested_quantity=quantity, as_of=at.isoformat())
        if not plan["completeness"].get("bom") or not isinstance(plan.get("data"), dict):
            return self._result(plan["status"], query, None, {"bom": False}, plan["provenance"], plan["warnings"])
        materials = plan["data"]["materials"]
        codes = {material["material_code"] for material in materials}
        if suppliers and not set(suppliers).issubset(codes):
            return self._result("invalid_query", query, None, {}, None, ["供应商选择必须使用本次 BOM 叶子物料的精确标准编码"])
        recommendations = [self._material(material, plan["provenance"], at, start,
                                         suppliers.get(material["material_code"]) if suppliers else None,
                                         availability_basis) for material in materials]
        snapshot_keys = {(line["provenance"]["inventory"].get("snapshot_at"),
                          line["provenance"]["inventory"].get("import_batch_id"))
                         for line in recommendations if line["provenance"]["inventory"]}
        coherent = len(snapshot_keys) == 1 and all(line["provenance"]["inventory"] for line in recommendations)
        quantities_complete = all(line["suggested_purchase_qty"] is not None for line in recommendations)
        costs_complete = all(line["purchase_cost"] is not None for line in recommendations)
        parameters_complete = all(line["completeness"]["parameters"] for line in recommendations)
        currencies = {line["currency"] for line in recommendations if line["purchase_cost"] is not None}
        ranked_costs: dict[str, list[dict[str, Any]]] = {}
        for currency in sorted(currencies):
            group = [line for line in recommendations if line["currency"] == currency and line["purchase_cost"] is not None]
            group.sort(key=lambda line: (-Decimal(line["purchase_cost"]), line["material"]))
            ranked_costs[currency] = group
            for rank, line in enumerate(group, 1):
                line["cost_rank_in_currency"] = rank
            impact_group = sorted(group, key=lambda line: (-Decimal(line["constraint_cost_impact"]), line["material"]))
            for rank, line in enumerate(impact_group, 1):
                line["constraint_cost_impact_rank_in_currency"] = rank
        comparable_cost = costs_complete and len(currencies) == 1

        def priority_key(line: dict[str, Any]) -> tuple:
            incomplete = not line["completeness"]["quantity"] or not line["completeness"]["parameters"] or line["risk_level"] is None
            rank = {"high": 3, "general": 2, "buffer_only": 1, "none": 0}.get(line["risk_level"], -1)
            return (0 if incomplete else 1, -rank,
                    -(line["lead_time_days"] if line["lead_time_days"] is not None else -1),
                    -Decimal(line["shortage_ratio"]) if line["shortage_ratio"] is not None else Decimal("1"),
                    -Decimal(line["purchase_cost"]) if comparable_cost else Decimal("0"), line["material"])

        recommendations.sort(key=priority_key)
        for priority, line in enumerate(recommendations, 1):
            line["priority"] = priority
        totals_by_currency = {currency: _text(sum((Decimal(line["purchase_cost"]) for line in group), Decimal("0")))
                              for currency, group in ranked_costs.items()}
        total = next(iter(totals_by_currency.values())) if costs_complete and len(currencies) == 1 and coherent else None
        impacts_by_currency = {currency: _text(sum((Decimal(line["constraint_cost_impact"]) for line in group), Decimal("0")))
                               for currency, group in ranked_costs.items()}
        total_impact = next(iter(impacts_by_currency.values())) if costs_complete and len(currencies) == 1 and coherent else None
        warnings = ["采购建议不代表订单已执行或物料已到货，不修改生产可行性结论"]
        if not coherent:
            warnings.append("产品叶子物料库存快照不完整或不同批次时点，未生成完整采购计划合计")
        if not costs_complete:
            warnings.append("部分价格事实缺失，单币种总成本保持未知；已知分币种小计只是局部金额")
        if len(currencies) > 1:
            warnings.append("存在多种币种，没有汇率事实，不合并总成本也不跨币种比较成本优先级")
        data = {"product": plan["data"]["product"], "revision": revision, "quantity": quantity,
                "recommendations": recommendations, "total_purchase_cost": total,
                "total_cost_currency": next(iter(currencies)) if costs_complete and len(currencies) == 1 and coherent else None,
                "total_constraint_cost_impact": total_impact,
                "known_cost_subtotals_by_currency": totals_by_currency,
                "known_constraint_cost_impact_by_currency": impacts_by_currency,
                "priority_rule": "数据缺口优先核验；风险等级降序、交期降序、缺口比例降序、同币种完整成本降序、料号升序",
                "cost_ranking_applied": comparable_cost,
                "missing_fields": {line["material"]: line["missing_fields"] for line in recommendations if line["missing_fields"]}}
        completeness = {"bom": True, "snapshot_coherent": coherent, "quantity": quantities_complete,
                        "parameters": parameters_complete, "cost": costs_complete, "single_currency": costs_complete and len(currencies) == 1}
        return self._result("ok" if coherent and quantities_complete and costs_complete and parameters_complete else "data_unavailable",
                            query, data, completeness, {"bom": plan["provenance"]["bom"],
                            "materials": {line["material"]: line["provenance"] for line in recommendations}}, warnings)

    def _parameters(self, item_id: str, unit: str, at: datetime, supplier: str | None) -> tuple[dict[str, Any] | None, list[str]]:
        try:
            candidates = [row for row in self.repository.get_purchase_parameters(item_id) if _active(row, at.date())]
        except (ValueError, TypeError):
            return None, ["purchase_parameters.effective_period"]
        if supplier is not None:
            candidates = [row for row in candidates if row["supplier_code"] == supplier]
        if len(candidates) != 1:
            return None, ["purchase_parameters" if not candidates else "purchase_parameters_conflict"]
        parameters = candidates[0]
        missing: list[str] = []
        if not isinstance(parameters["supplier_code"], str) or not parameters["supplier_code"].strip():
            missing.append("supplier_code")
        if parameters["purchase_unit"] != unit:
            missing.append("purchase_unit")
        for field in ("minimum_order_quantity", "order_multiple", "packaging_multiple"):
            try:
                number = _decimal(parameters[field])
                if field == "minimum_order_quantity":
                    if number is None:
                        missing.append(field)
                else:
                    applicable = parameters[f"{field}_applicable"]
                    if number is not None and (number <= 0 or applicable is False or applicable == 0):
                        raise ValueError("采购倍数矛盾")
                    if number is None and applicable not in (False, 0):
                        missing.append(field)
            except ValueError:
                missing.append(field)
        lead = parameters["lead_time_days"]
        if isinstance(lead, bool) or not isinstance(lead, int) or lead < 0:
            missing.append("lead_time_days")
        return parameters, missing

    def _price(self, parameters: dict[str, Any] | None, item_id: str, unit: str,
               at: datetime) -> tuple[dict[str, Any] | None, list[str]]:
        if not parameters or not parameters["price_record_id"]:
            return None, ["price_record_id"]
        price = self.repository.get_price_record(str(parameters["price_record_id"]))
        if not price:
            return None, ["published_price"]
        missing: list[str] = []
        try:
            if _decimal(price["tax_included_unit_price"]) is None:
                missing.append("price.unit_price")
        except ValueError:
            missing.append("price.unit_price")
        if str(price["item_id"]) != item_id:
            missing.append("price.item")
        if price["unit"] != unit:
            missing.append("price.unit")
        if not isinstance(price["currency"], str) or not price["currency"].strip():
            missing.append("price.currency")
        if price["supplier_code"] is not None and price["supplier_code"] != parameters["supplier_code"]:
            missing.append("price.supplier_scope")
        try:
            if not _active(price, at.date()):
                missing.append("price.effective_period")
        except (ValueError, TypeError):
            missing.append("price.effective_period")
        return price, missing

    def _material(self, material: dict[str, Any], bom_sources: dict[str, Any], at: datetime, start: date,
                  supplier: str | None, availability_basis: AvailabilityBasis) -> dict[str, Any]:
        code, unit = str(material["material_code"]), str(material["unit"])
        item = self.repository.resolve_item(code)
        item_id = str(item["item_id"])
        inventory = self.inventory.analyze(code, as_of=at.isoformat(), availability_basis=availability_basis)
        supply = self.inventory.planned_supply(code, start.isoformat(), as_of=at.isoformat(), availability_basis=availability_basis)
        inv_data, sup_data = inventory.get("data") or {}, supply.get("data") or {}
        parameters, parameter_missing = self._parameters(item_id, unit, at, supplier)
        price, price_missing = self._price(parameters, item_id, unit, at)
        missing: list[str] = []
        required = _decimal(material["required_qty"])
        safety = _decimal(inv_data.get("safety_stock", {}).get("safety_stock_qty"))
        available = _decimal(sup_data.get("inventory_usable_at_start_qty"))
        incoming = _decimal(sup_data.get("eligible_inbound_qty"))
        planned = _decimal(sup_data.get("planned_supply_qty"))
        inv_source = inventory.get("provenance")
        supply_inv_source = (supply.get("provenance") or {}).get("inventory")
        same_inventory = isinstance(inv_source, dict) and isinstance(supply_inv_source, dict) and all(
            inv_source.get(key) == supply_inv_source.get(key) for key in ("snapshot_at", "import_batch_id", "content_sha256")
        )
        for field, number in (("required_qty", required), ("safety_stock_qty", safety), ("available_qty", available),
                              ("eligible_inbound_qty", incoming), ("planned_supply_qty", planned)):
            if number is None:
                missing.append(field)
        if not same_inventory:
            planned = None
            missing.append("inventory_observation_consistency")
        shortage = max(required - planned, Decimal("0")) if required is not None and planned is not None else None
        net = max(required + safety - planned, Decimal("0")) if required is not None and safety is not None and planned is not None else None
        constraint_fields = {"purchase_parameters", "purchase_parameters_conflict", "purchase_parameters.effective_period",
                             "purchase_unit", "minimum_order_quantity", "order_multiple", "packaging_multiple"}
        constraints_ok = parameters is not None and not constraint_fields.intersection(parameter_missing)
        common: Decimal | None = None
        suggested: Decimal | None = None
        if constraints_ok and net is not None:
            multiples = [_decimal(parameters[field]) for field in ("order_multiple", "packaging_multiple") if parameters[field] is not None]
            common = _common_multiple(multiples)
            suggested = _round_quantity(net, _decimal(parameters["minimum_order_quantity"]), common)
        extra = suggested - net if suggested is not None and net is not None else None
        valid_price = price is not None and not price_missing
        unit_price = _decimal(price["tax_included_unit_price"]) if valid_price else None
        cost = suggested * unit_price if suggested is not None and unit_price is not None else None
        baseline_cost = net * unit_price if net is not None and unit_price is not None else None
        cost_impact = extra * unit_price if extra is not None and unit_price is not None else None
        lead = parameters["lead_time_days"] if parameters and "lead_time_days" not in parameter_missing else None
        arrival: date | None = None
        if lead is not None:
            try:
                arrival = at.date() + timedelta(days=lead)
            except OverflowError:
                parameter_missing.append("lead_time_days")
                lead = None
        shortage_ratio = shortage / required if shortage is not None and required is not None and required > 0 else None
        risk = None
        risk_reasons: list[str] = []
        if shortage is not None and shortage == 0:
            risk = "buffer_only" if net is not None and net > 0 else "none" if net == 0 else None
        elif shortage is not None and lead is not None:
            risk = "high" if lead > 7 or arrival >= start else "general"
            if lead > 7:
                risk_reasons.append("权威交期超过 7 天")
            if arrival >= start:
                risk_reasons.append("按交期计算的到货日未早于开工日")
        all_missing = sorted(set([*missing, *parameter_missing, *price_missing]))
        reasons = ["补足生产需求并保留明确安全库存，扣除开工日前已确认计划供给"]
        if suggested is not None:
            reasons.append("采购量取满足 MOQ 与全部明确倍数约束的最小值；净需求为零时不触发 MOQ")
        else:
            reasons.append("关键数量或约束缺少权威事实，采购量保持未知")
        completeness = {"quantity": suggested is not None, "parameters": not parameter_missing,
                        "price": valid_price, "cost": cost is not None, "risk": risk is not None}
        public_parameters = {
            field: _text(_decimal(parameters[field])) if parameters and field not in parameter_missing else None
            for field in ("minimum_order_quantity", "order_multiple", "packaging_multiple")
        }
        for field in ("order_multiple", "packaging_multiple"):
            public_parameters[f"{field}_status"] = "unknown" if not parameters or field in parameter_missing else "applicable" if public_parameters[field] is not None else "not_applicable"
        public_parameters["purchase_unit"] = parameters["purchase_unit"] if parameters and "purchase_unit" not in parameter_missing else None
        return {"material": code, "unit": unit, "required_qty": _text(required),
                "current_available_qty": inv_data.get("available_qty"), "available_qty": _text(available),
                "eligible_inbound_qty": _text(incoming), "safety_stock_qty": _text(safety),
                "shortage": _text(shortage), "shortage_qty": _text(shortage), "shortage_ratio": _text(shortage_ratio),
                "net_purchase_requirement_qty": _text(net), "suggested_purchase_qty": _text(suggested),
                "constraint_extra_qty": _text(extra), "effective_order_multiple": _text(common),
                "minimum_order_quantity": public_parameters["minimum_order_quantity"], "purchase_parameters": public_parameters,
                "supplier": parameters["supplier_code"] if parameters and "supplier_code" not in parameter_missing else None, "lead_time_days": lead,
                "earliest_arrival_date": arrival.isoformat() if arrival else None,
                "arrival_assumption": "仅模拟分析日下单后的交期，不代表已下单或供应商承诺到货",
                "unit_price": _text(unit_price), "currency": price["currency"] if valid_price else None,
                "purchase_cost": _text(cost), "unconstrained_cost": _text(baseline_cost), "constraint_cost_impact": _text(cost_impact),
                "risk_level": risk, "long_lead_time": lead > 7 if lead is not None else None,
                "risk_reasons": risk_reasons, "cost_rank_in_currency": None,
                "constraint_cost_impact_rank_in_currency": None, "reason": reasons,
                "recommendation_status": "ok" if all(completeness.values()) else "data_unavailable",
                "missing_fields": all_missing, "completeness": completeness,
                "provenance": {"bom": bom_sources["bom"], "bom_paths": material["bom_paths"],
                               "unit_conversions": bom_sources["unit_conversions"], "inventory": inv_source,
                               "inbound": (supply.get("provenance") or {}).get("inbound"),
                               "safety_stock": inv_source.get("risk_policy") if inv_source else None,
                               "parameters": _source(parameters), "price": _source(price)}}

    @staticmethod
    def _result(status: str, query: dict[str, Any], data: dict[str, Any] | None,
                completeness: dict[str, bool], provenance: dict[str, Any] | None,
                warnings: list[str]) -> FactQueryResult:
        return {"status": status, "query": query, "data": data,
                "completeness": completeness, "provenance": provenance, "warnings": warnings}


__all__ = ["PublishedPurchasePlanningService"]
