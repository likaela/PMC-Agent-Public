"""v1.11 published facts 批次分析与计划供给；所有数量由 Decimal 计算。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fact_layer.query_models import FactQueryResult
from repositories.inventory_analysis_repository import InventoryAnalysisRepository


AvailabilityBasis = Literal["source_only", "on_hand_minus_reserved"]
OPEN_INBOUND_STATUSES = frozenset({"open", "confirmed", "in_transit", "partially_received"})
CLOSED_INBOUND_STATUSES = frozenset({"cancelled", "canceled", "closed", "received"})


def _number(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("数量不能为布尔值")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("数量格式无效") from error
    if not result.is_finite() or result < 0:
        raise ValueError("数量必须是有限非负数")
    return result


def _text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _instant(value: object) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("时间缺少明确时区")
    return parsed.astimezone(UTC)


def _day(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _source(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in (
        "source_file", "content_sha256", "import_batch_id", "sheet_name", "row_number",
    ) if key in row}


def _result(status: str, query: dict[str, Any], data: dict[str, Any] | None = None,
            completeness: dict[str, bool] | None = None, provenance: dict[str, Any] | None = None,
            warnings: list[str] | None = None) -> FactQueryResult:
    return {"status": status, "query": query, "data": data,
            "completeness": completeness or {}, "provenance": provenance,
            "warnings": warnings or []}


class PublishedInventoryAnalysisService:
    """只读批次事实。FIFO 是模拟分配，不写库存、预留或生产决策。"""

    def __init__(self, repository: InventoryAnalysisRepository) -> None:
        self.repository = repository

    def _context(self, material_code: str, as_of: str | None,
                 availability_basis: AvailabilityBasis) -> tuple[dict[str, Any], datetime, dict[str, Any]] | FactQueryResult:
        query = {"material_code": material_code, "as_of": as_of, "availability_basis": availability_basis}
        if not isinstance(material_code, str) or not material_code.strip() or availability_basis not in ("source_only", "on_hand_minus_reserved"):
            return _result("invalid_query", query, warnings=["请提供精确物料编码和明确的可用量计算口径"])
        try:
            at = _instant(as_of) if as_of is not None else datetime.now(UTC)
        except (ValueError, TypeError, OverflowError):
            return _result("invalid_query", query, warnings=["查询时点必须是带时区的 ISO 8601 时间"])
        query["as_of"] = at.isoformat()
        item = self.repository.resolve_item(material_code.strip())
        if item is None:
            return _result("item_not_found", query, warnings=["当前事实库没有精确匹配的物料"])
        if item.get("ambiguous"):
            return _result("data_unavailable", query, warnings=["物料别名存在歧义，需要确认标准物料"])
        if not isinstance(item.get("unit"), str) or not item["unit"].strip():
            return _result("data_unavailable", query, warnings=["物料缺少明确计量单位，未生成数量分析"])
        query["resolved_material_code"] = item["material_code"]
        return item, at, query

    def _inventory(self, item: dict[str, Any], at: datetime,
                   query: dict[str, Any]) -> dict[str, Any] | FactQueryResult:
        payload = self.repository.get_lots(str(item["item_id"]), at)
        if not payload or payload.get("ambiguous"):
            reason = "同一时点存在多个库存快照，需要确认来源" if payload else "当前时点没有已发布库存快照"
            return _result("data_unavailable", query, warnings=[reason])
        snapshot = payload["snapshot"]
        snapshot_at = _instant(snapshot["snapshot_at"])
        lots: list[dict[str, Any]] = []
        warnings: list[str] = []
        for row in payload["lots"]:
            quantities: dict[str, Decimal | None] = {}
            errors: list[str] = []
            for field in ("on_hand_qty", "reserved_qty", "available_qty"):
                try:
                    quantities[field] = _number(row[field])
                except ValueError:
                    quantities[field] = None
                    errors.append(field)
            on_hand, reserved, available = (quantities[key] for key in ("on_hand_qty", "reserved_qty", "available_qty"))
            if on_hand is None:
                errors.append("on_hand_qty")
            if on_hand is not None and (reserved is not None and reserved > on_hand or available is not None and available > on_hand):
                errors.append("quantity_inconsistent")
            if on_hand is not None and reserved is not None and available is not None and available > on_hand - reserved:
                errors.append("quantity_inconsistent")
            available_source = "published_available_qty" if available is not None else None
            if available is None and query["availability_basis"] == "on_hand_minus_reserved" and on_hand is not None and reserved is not None and not errors:
                available = on_hand - reserved
                available_source = "explicit_on_hand_minus_reserved"
            if errors:
                available = None
                available_source = None
                warnings.append(f"库存来源第 {row['row_number']} 行的数量无效或不一致，未计算可用量")
            times: dict[str, datetime | None] = {}
            event_dates_valid = True
            for field in ("received_at", "last_movement_at"):
                try:
                    timestamp = _instant(row[field]) if row[field] is not None else None
                    if timestamp is not None and timestamp > snapshot_at:
                        raise ValueError("事件时间晚于库存快照")
                    times[field] = timestamp
                except (ValueError, TypeError, OverflowError):
                    times[field] = None
                    event_dates_valid = False
                    warnings.append(f"库存来源第 {row['row_number']} 行的事件时间无效，未生成对应判断")
            if times["received_at"] and times["last_movement_at"] and times["last_movement_at"] < times["received_at"]:
                times["last_movement_at"] = None
                event_dates_valid = False
                warnings.append("最后移动时间早于入库时间，呆滞口径需要核验")
            try:
                expiry = _day(row["expiry_date"]) if row["expiry_date"] is not None else None
            except (ValueError, TypeError):
                expiry = None
                warnings.append("批次有效期无效，未生成临期判断")
            lots.append({
                "inventory_lot": str(row["id"]), "batch_no": row["batch_no"],
                "site": row["site"], "warehouse": row["warehouse"], "location": row["location"],
                "snapshot_at": snapshot_at.isoformat(), "unit": item.get("unit"),
                "on_hand_qty": _text(on_hand), "reserved_qty": _text(reserved), "available_qty": _text(available),
                "available_basis": available_source, "quality_status": row["quality_status"],
                "received_at": times["received_at"].isoformat() if times["received_at"] else None,
                "last_movement_at": times["last_movement_at"].isoformat() if times["last_movement_at"] else None,
                "expiry_date": expiry.isoformat() if expiry else None,
                "quantity_valid": not errors, "is_duplicate": bool(row["is_duplicate"]),
                "event_dates_valid": event_dates_valid,
                "provenance": {**_source(row), "snapshot_at": snapshot_at.isoformat()},
            })
        duplicate_free = not any(lot["is_duplicate"] for lot in lots)
        quantity_valid = bool(lots) and all(lot["quantity_valid"] for lot in lots)
        totals: dict[str, str | None] = {}
        for field in ("on_hand_qty", "reserved_qty", "available_qty"):
            totals[field] = _text(sum((Decimal(lot[field]) for lot in lots), Decimal("0"))) if (
                quantity_valid and duplicate_free and all(lot[field] is not None for lot in lots)
            ) else None
        if not duplicate_free:
            warnings.append("快照包含标记重复行，保留明细但拒绝合计与 FIFO 分配")
        provenance = {**_source(snapshot), "snapshot_at": snapshot_at.isoformat(),
                      "observed_at": _instant(snapshot["observed_at"]).isoformat(),
                      "lots": [lot["provenance"] for lot in lots]}
        return {"lots": lots, "totals": totals, "provenance": provenance, "warnings": warnings,
                "completeness": {"on_hand_qty": totals["on_hand_qty"] is not None,
                                 "reserved_qty": totals["reserved_qty"] is not None,
                                 "available_qty": totals["available_qty"] is not None,
                                 "duplicate_free": duplicate_free, "quantity_valid": quantity_valid}}

    def _policy(self, item: dict[str, Any], at: datetime) -> tuple[dict[str, Any] | None, list[str]]:
        active: list[dict[str, Any]] = []
        for policy in self.repository.get_risk_policies(str(item["item_id"])):
            try:
                start = _day(policy["effective_from"])
                end = _day(policy["effective_to"]) if policy["effective_to"] is not None else None
                if end is not None and end < start:
                    raise ValueError("生效期无效")
                if start <= at.date() and (end is None or at.date() <= end):
                    active.append(policy)
            except (ValueError, TypeError):
                return None, ["已发布库存风险策略的生效期无效"]
        if len(active) != 1:
            return None, ["没有唯一有效的已发布库存风险策略，相关阈值保持未知"]
        policy = active[0]
        if not item.get("unit") or policy["unit"] != item["unit"]:
            return None, ["库存风险策略单位与标准物料单位不一致，未推断换算"]
        try:
            _number(policy["safety_stock_qty"])
            for field, minimum in (("expiry_warning_days", 0), ("stagnant_days", 1)):
                if policy[field] is not None and (isinstance(policy[field], bool) or not isinstance(policy[field], int) or policy[field] < minimum):
                    raise ValueError("阈值无效")
        except ValueError:
            return None, ["库存风险策略包含非法阈值，未生成判断"]
        return policy, []

    def analyze(self, material_code: str, *, as_of: str | None = None,
                availability_basis: AvailabilityBasis = "source_only",
                expiry_warning_days: int | None = None, stagnant_days: int | None = None) -> FactQueryResult:
        context = self._context(material_code, as_of, availability_basis)
        if isinstance(context, dict):
            return context
        item, at, query = context
        query.update(expiry_warning_days=expiry_warning_days, stagnant_days=stagnant_days)
        for value, minimum in ((expiry_warning_days, 0), (stagnant_days, 1)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < minimum):
                return _result("invalid_query", query, warnings=["预警天数必须是明确的有效整数"])
        inventory = self._inventory(item, at, query)
        if "status" in inventory:
            return inventory
        policy, policy_warnings = self._policy(item, at)
        expiry_days = expiry_warning_days if expiry_warning_days is not None else policy.get("expiry_warning_days") if policy else None
        idle_days = stagnant_days if stagnant_days is not None else policy.get("stagnant_days") if policy else None
        expiry_assessments: list[dict[str, Any]] = []
        idle_assessments: list[dict[str, Any]] = []
        for lot in inventory["lots"]:
            reference = {"inventory_lot": lot["inventory_lot"], "batch_no": lot["batch_no"],
                         "location": lot["location"], "on_hand_qty": lot["on_hand_qty"],
                         "provenance": lot["provenance"]}
            days_left = (_day(lot["expiry_date"]) - at.date()).days if lot["expiry_date"] else None
            expiry_status = "data_unavailable"
            suggestion = None
            if days_left is not None:
                if days_left < 0:
                    expiry_status, suggestion = "expired", "核验有效期与质量状态，暂停将该批次用于生产"
                elif expiry_days is not None:
                    expiry_status = "near_expiry" if days_left <= expiry_days else "normal"
                    suggestion = "优先核验可用性并安排消耗，避免超过有效期" if expiry_status == "near_expiry" else None
            expiry_assessments.append({**reference, "status": expiry_status, "days_remaining": days_left,
                                       "expiry_date": lot["expiry_date"], "suggested_action": suggestion})
            event_field = "last_movement_at" if lot["last_movement_at"] else "received_at"
            event = lot[event_field] if lot["event_dates_valid"] else None
            age = (at - _instant(event)).days if event is not None else None
            idle_status = "data_unavailable" if age is None or idle_days is None else "stagnant" if age >= idle_days else "normal"
            idle_assessments.append({**reference, "status": idle_status, "age_days": age,
                                     "date_basis": event_field if event else None,
                                     "suggested_action": "核验库存移动记录与消耗计划" if idle_status == "stagnant" else None})
        available = _number(inventory["totals"]["available_qty"])
        safety = _number(policy["safety_stock_qty"]) if policy else None
        safety_status = "data_unavailable" if available is None or safety is None else "below_safety_stock" if available < safety else "normal"
        safety_gap = max(safety - available, Decimal("0")) if available is not None and safety is not None else None
        completeness = {**inventory["completeness"],
                        "expiry_analysis": all(row["status"] != "data_unavailable" for row in expiry_assessments),
                        "stagnant_analysis": all(row["status"] != "data_unavailable" for row in idle_assessments),
                        "safety_stock_analysis": safety_status != "data_unavailable"}
        warnings = [*inventory["warnings"], *policy_warnings]
        for field, message in (("available_qty", "可用量缺少完整权威事实，保持未知"),
                               ("reserved_qty", "预留量缺少完整权威事实，保持未知"),
                               ("expiry_analysis", "部分批次缺少有效期或明确临期阈值，不生成对应判断"),
                               ("stagnant_analysis", "部分批次缺少事件日期或明确呆滞阈值，不生成对应判断")):
            if not completeness[field]:
                warnings.append(message)
        data = {"canonical_item": item, "snapshot_at": inventory["provenance"]["snapshot_at"],
                "inventory_lots": inventory["lots"], **inventory["totals"],
                "expiry_assessments": expiry_assessments, "stagnant_assessments": idle_assessments,
                "safety_stock": {"status": safety_status, "safety_stock_qty": _text(safety), "gap_qty": _text(safety_gap)},
                "thresholds": {"expiry_warning_days": expiry_days, "stagnant_days": idle_days}}
        provenance = {**inventory["provenance"], "risk_policy": _source(policy) if policy else None,
                      "threshold_overrides": {key: value for key, value in (
                          ("expiry_warning_days", expiry_warning_days), ("stagnant_days", stagnant_days)) if value is not None}}
        return _result("ok" if completeness["on_hand_qty"] else "data_unavailable", query, data, completeness, provenance, warnings)

    def fifo(self, material_code: str, required_qty: str, *, as_of: str | None = None,
             availability_basis: AvailabilityBasis = "source_only") -> FactQueryResult:
        context = self._context(material_code, as_of, availability_basis)
        if isinstance(context, dict):
            return context
        item, at, query = context
        query["required_qty"] = required_qty
        try:
            required = _number(required_qty)
            if required is None or required <= 0:
                raise ValueError("需求量必须大于零")
        except ValueError:
            return _result("invalid_query", query, warnings=["FIFO 需求量必须是大于零的确定性数量"])
        inventory = self._inventory(item, at, query)
        if "status" in inventory:
            return inventory
        lots = inventory["lots"]
        if not inventory["completeness"]["available_qty"] or not all(lot["received_at"] and lot["batch_no"] for lot in lots):
            return _result("data_unavailable", query, {"allocations": None, "shortage_qty": None},
                           inventory["completeness"], inventory["provenance"],
                           [*inventory["warnings"], "FIFO 需要完整批次标识、明确入库时间和权威可用量"])
        eligible = [lot for lot in lots if not lot["expiry_date"] or _day(lot["expiry_date"]) >= at.date()]
        excluded = [lot for lot in lots if lot not in eligible]
        eligible.sort(key=lambda lot: (
            _instant(lot["received_at"]), str(lot["warehouse"] or ""), str(lot["location"] or ""),
            str(lot["batch_no"]), lot["provenance"]["sheet_name"], lot["provenance"]["row_number"],
        ))
        remaining = required
        allocations: list[dict[str, Any]] = []
        for lot in eligible:
            available = Decimal(lot["available_qty"])
            used = min(available, remaining)
            if used > 0:
                allocations.append({"inventory_lot": lot["inventory_lot"], "batch_no": lot["batch_no"],
                                    "warehouse": lot["warehouse"], "location": lot["location"],
                                    "received_at": lot["received_at"], "available_before": _text(available),
                                    "allocated_qty": _text(used), "available_after": _text(available - used),
                                    "remaining_requirement": _text(remaining - used), "provenance": lot["provenance"]})
                remaining -= used
        warnings = list(inventory["warnings"])
        if any(lot["expiry_date"] is None for lot in eligible):
            warnings.append("部分批次有效期未知；FIFO 只给出入库顺序，不代表已通过有效期或质量放行检查")
        return _result("ok", query, {"unit": item.get("unit"), "required_qty": _text(required),
                       "allocated_qty": _text(required - remaining), "shortage_qty": _text(remaining),
                       "allocations": allocations, "excluded_expired_lots": excluded, "simulation_only": True,
                       "sorting_rule": "received_at ASC, warehouse, location, batch_no, source sheet/row"},
                       {**inventory["completeness"], "received_at": True, "batch_no": True}, inventory["provenance"], warnings)

    def planned_supply(self, material_code: str, start_date: str, *, as_of: str | None = None,
                       availability_basis: AvailabilityBasis = "source_only") -> FactQueryResult:
        context = self._context(material_code, as_of, availability_basis)
        if isinstance(context, dict):
            return context
        item, at, query = context
        query["start_date"] = start_date
        try:
            start = _day(start_date)
            if not isinstance(start_date, str) or start_date != start.isoformat() or start < at.date():
                raise ValueError("开工日无效")
        except (ValueError, TypeError):
            return _result("invalid_query", query, warnings=["请提供不早于分析日期的明确开工日期"])
        supplies = self.repository.get_supplies(str(item["item_id"]), at)
        if not supplies or supplies.get("ambiguous"):
            return _result("data_unavailable", query,
                           {"eligible_inbound_qty": None, "planned_supply_qty": None},
                           {"inbound": False}, warnings=["当前没有唯一的已发布权威在途观察，未将未知在途当作零"])
        observation = supplies["observation"]
        observation_at = _instant(observation["observed_at"])
        lines: list[dict[str, Any]] = []
        complete = True
        known_eligible = Decimal("0")
        order_keys: set[tuple[str, str, int]] = set()
        for row in supplies["lines"]:
            reason = None
            try:
                ordered, received, incoming = (_number(row[field]) for field in ("ordered_qty", "received_qty", "in_transit_qty"))
                if ordered is None or received is not None and received > ordered:
                    raise ValueError("在途数量不一致")
                if incoming is None and received is not None:
                    incoming = ordered - received
                if incoming is not None and (incoming > ordered or received is not None and incoming != ordered - received):
                    raise ValueError("在途数量不一致")
            except ValueError:
                incoming = None
                reason = "quantity_invalid"
            arrival_field = "committed_date" if row["committed_date"] is not None else "expected_arrival_date"
            try:
                arrival = _day(row[arrival_field]) if row[arrival_field] is not None else None
            except (ValueError, TypeError):
                arrival = None
            status = row["supply_status"]
            disposition = "pending_confirmation"
            # 标准状态必须由权威适配器明确提供，不猜测企业自定义状态含义。
            if status in CLOSED_INBOUND_STATUSES or incoming == 0:
                disposition, reason = "excluded", "closed_or_fully_received"
            elif incoming is None:
                reason = reason or "remaining_quantity_unknown"
            elif status not in OPEN_INBOUND_STATUSES:
                reason = "order_status_unknown"
            elif arrival is None:
                reason = "arrival_date_unknown"
            elif arrival < at.date():
                reason = "overdue_arrival_needs_confirmation"
            elif arrival >= start:
                disposition, reason = "excluded", "not_confirmed_before_start"
            else:
                disposition = "eligible"
            # 一个来源行只能计入一次；重复行身份异常时拒绝完整计划。
            key = (str(row["import_batch_id"]), str(row["sheet_name"]), int(row["row_number"]))
            if key in order_keys:
                disposition, reason = "pending_confirmation", "duplicate_source_row"
            order_keys.add(key)
            if disposition == "eligible":
                known_eligible += incoming
            if disposition == "pending_confirmation":
                complete = False
            lines.append({"order_no": row["order_no"], "supplier_code": row["supplier_code"],
                          "in_transit_qty": _text(incoming), "arrival_date": arrival.isoformat() if arrival else None,
                          "arrival_basis": arrival_field if arrival else None, "status": status,
                          "planning_status": disposition, "reason": reason, "provenance": _source(row)})
        inventory = self._inventory(item, at, query)
        inventory_ok = "status" not in inventory and inventory["completeness"]["available_qty"]
        coherent = inventory_ok and _instant(inventory["provenance"]["snapshot_at"]) == observation_at
        eligible_total = known_eligible if complete else None
        stock = _number(inventory["totals"]["available_qty"]) if inventory_ok else None
        valid_at_start = inventory_ok and all(
            Decimal(lot["available_qty"]) == 0 or lot["expiry_date"] is not None
            for lot in inventory["lots"]
        )
        usable_stock = sum((Decimal(lot["available_qty"]) for lot in inventory["lots"]
                            if lot["expiry_date"] is not None and _day(lot["expiry_date"]) >= start), Decimal("0")) if valid_at_start else None
        planned = usable_stock + eligible_total if coherent and usable_stock is not None and eligible_total is not None else None
        warnings = ["计划在途不是实际到货，计划供给不构成可开工结论"]
        if not complete:
            warnings.append("部分在途行缺少完整数量、明确日期或标准状态，完整在途计划保持未知")
        if not coherent:
            warnings.append("库存与在途观察时点不一致或可用库存未知，拒绝相加以避免重复计入已收货物料")
        if not valid_at_start:
            warnings.append("部分库存批次缺少有效期，无法确认开工日可用性，保留当前可用量但不生成完整供给量")
        data = {"canonical_item": item, "known_eligible_inbound_qty": _text(known_eligible),
                "eligible_inbound_qty": _text(eligible_total), "current_available_qty": _text(stock),
                "inventory_usable_at_start_qty": _text(usable_stock),
                "planned_supply_qty": _text(planned), "lines": lines,
                "arrival_cutoff": "arrival_date < start_date"}
        provenance = {"inventory": inventory.get("provenance"),
                      "inbound": {**_source(observation), "observed_at": observation_at.isoformat(),
                                  "lines": [line["provenance"] for line in lines]}}
        return _result("ok" if planned is not None else "data_unavailable", query, data,
                       {"inbound": complete, "available_qty": bool(inventory_ok), "expiry_at_start": bool(valid_at_start),
                        "observation_coherent": bool(coherent)}, provenance, warnings)


__all__ = ["PublishedInventoryAnalysisService", "AvailabilityBasis"]
