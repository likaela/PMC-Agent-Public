"""Two-step inventory execution using the established FIFO allocation service.

Preview never mutates balances. The same published FIFO rules run again inside a
BEGIN IMMEDIATE transaction at execution. No alternate BOM/MRP engine is added.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
import uuid

from repositories.inventory_ledger_repository import json_text, quantity, text_number
from services import unit_system
from services.published_inventory_analysis_service import PublishedInventoryAnalysisService


def _result(status, data=None, warnings=None, provenance=None):
    return dict(status=status, data=data, warnings=warnings or [], provenance=provenance or {})


class _RuntimeFifoRepository:
    """Translate ledger observations to the existing FIFO repository contract.

    Snapshot time here is the actual ledger read time, never a fabricated Excel
    business date. Original received_at dates are never synthesized.
    """
    def __init__(self, ledger, namespace, connection, as_of):
        self.ledger, self.namespace, self.connection, self.as_of = ledger, namespace, connection, as_of

    def resolve_item(self, code):
        value = self.ledger.stock(self.namespace, code, connection=self.connection)
        return dict(item_id=code, material_code=code, unit=value["unit"]) if value["record_found"] else None

    def get_lots(self, item_id, at):
        value = self.ledger.stock(self.namespace, item_id, connection=self.connection)
        lots = []
        for ordinal, row in enumerate(value["batches"]):
            provenance = row.get("provenance", {})
            lots.append(dict(id=row["ledger_lot_id"], batch_no=row.get("batch_no"),
                site=row.get("site"), warehouse=row.get("warehouse"), location=row.get("location"),
                on_hand_qty=row.get("on_hand_qty"), available_qty=row.get("available_qty"),
                reserved_qty=row.get("reserved_qty"), quality_status=row.get("quality_status"),
                received_at=row.get("received_at"), last_movement_at=row.get("last_movement_at"),
                expiry_date=row.get("expiry_date"), is_duplicate=row.get("duplicate_observation", False),
                source_file=provenance.get("source_file"), content_sha256=provenance.get("content_sha256"),
                import_batch_id=provenance.get("import_batch_id"),
                sheet_name=provenance.get("sheet_name", "runtime_ledger"),
                row_number=provenance.get("row_number", ordinal + 1)))
        return dict(lots=lots, snapshot=dict(snapshot_at=self.as_of, observed_at=self.as_of,
            source_file="runtime_inventory_ledger", namespace=self.namespace, version=value["version"]))


class InventoryExecutionService:
    def __init__(self, ledger, *, preview_lifetime_seconds=900, allow_demo_execution=False,
                 allowed_namespaces=("demo",), source_validator=None):
        self.ledger = ledger
        self.preview_lifetime_seconds = preview_lifetime_seconds
        self.allow_demo_execution = allow_demo_execution is True
        self.allowed_namespaces = tuple(allowed_namespaces)
        self.source_validator = source_validator

    @staticmethod
    def _batch_errors(batches, now):
        """An absent optional date differs from a malformed explicit date."""
        errors = []
        for row in batches:
            if not row.get("batch_no"):
                errors.append("批次标识缺失")
            parsed = {}
            for field in ("received_at", "last_movement_at"):
                value = row.get(field)
                if value is None:
                    parsed[field] = None
                    if field == "received_at":
                        errors.append("缺少可靠入库日期")
                    continue
                try:
                    if not isinstance(value, (str, datetime)):
                        raise ValueError("Invalid date type")
                    instant = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if instant.tzinfo is None or instant > now:
                        raise ValueError("Invalid event time")
                    parsed[field] = instant.astimezone(UTC)
                except (TypeError, ValueError, OverflowError):
                    parsed[field] = None
                    errors.append("批次事件日期无效")
            received, moved = parsed.get("received_at"), parsed.get("last_movement_at")
            if received and moved and moved < received:
                errors.append("最后移动时间早于入库时间")
            expiry = row.get("expiry_date")
            if expiry is not None:
                try:
                    if not isinstance(expiry, (str, date)) or isinstance(expiry, datetime):
                        raise ValueError("Invalid expiry type")
                    end = date.fromisoformat(expiry) if isinstance(expiry, str) else expiry
                    if received and end < received.date():
                        raise ValueError("Expiry precedes receipt")
                except (TypeError, ValueError, OverflowError):
                    errors.append("批次有效期无效或早于入库日期")
        return list(dict.fromkeys(errors))

    @staticmethod
    def _demands(rows):
        totals = OrderedDict()
        if not isinstance(rows, list) or not rows:
            raise ValueError("必须先取得完整BOM需求，不能执行空物料清单。")
        for row in rows:
            code, unit = row.get("material_code"), row.get("unit")
            amount = quantity(row.get("required_qty"))
            if (not isinstance(code, str) or not code.strip() or not isinstance(unit, str)
                    or not unit.strip() or amount is None or amount <= 0):
                raise ValueError("物料编码、单位和正数需求量必须完整。")
            if code in totals and totals[code]["unit"] != unit:
                raise ValueError("同一物料需求单位冲突，未合并或执行。")
            if code not in totals:
                totals[code] = dict(material_code=code, unit=unit, required_qty=Decimal(0))
            totals[code]["required_qty"] += amount
        return [{**row, "required_qty": text_number(row["required_qty"])} for row in totals.values()]

    def _plan(self, connection, namespace, demands, as_of):
        fifo = PublishedInventoryAnalysisService(_RuntimeFifoRepository(self.ledger, namespace, connection, as_of))
        rows, warnings, executable = [], [], True
        for demand in demands:
            stock = self.ledger.stock(namespace, demand["material_code"], connection=connection)
            stock_unit, demand_unit = stock["unit"], demand["unit"]
            required = Decimal(demand["required_qty"])
            same_unit = stock_unit == demand_unit
            # 单位口径统一：完全一致直接用；同维度不同单位按 Decimal 精确换算；
            # 跨维度或无法识别一律视为“没有可核实且单位一致的库存”，不扣减。
            available = quantity(stock["available_qty"]) if stock_unit else None
            required_in_stock = required if same_unit else (
                unit_system.convert(required, demand_unit, stock_unit) if stock_unit else None)
            available_in_demand = available if same_unit else (
                unit_system.convert(available, stock_unit, demand_unit)
                if available is not None and stock_unit else None)
            row = {**demand, "available_qty": stock["available_qty"], "stock_unit": stock_unit,
                "available_qty_in_demand_unit": text_number(available_in_demand),
                "shortage_qty": None, "allocations": [], "executable": False,
                "allocation_unit": stock_unit, "provenance": stock["provenance"]}
            if available_in_demand is not None:
                row["shortage_qty"] = text_number(max(required - available_in_demand, Decimal(0)))
            date_errors = self._batch_errors(stock["batches"], datetime.fromisoformat(as_of))
            if available_in_demand is None or required_in_stock is None:
                warnings.append(f"{demand['material_code']}：没有可核实且单位一致的库存，未执行扣减。")
            elif date_errors:
                warnings.extend(f"{demand['material_code']}：{error}，不能执行领料。" for error in date_errors)
            elif any(r.get("quality_status") not in (None, "normal", "released") for r in stock["batches"]):
                warnings.append(f"{demand['material_code']}：库存状态不允许领料。")
            else:
                row["shortage_qty"] = text_number(max(required - available_in_demand, Decimal(0)))
                # 严格FIFO按库存计量单位分配，分配量即该批次实际扣减量。
                result = fifo.fifo(demand["material_code"], text_number(required_in_stock), as_of=as_of)
                row["fifo_status"] = result["status"]
                if result["status"] == "ok":
                    row["allocations"] = result["data"]["allocations"]
                    shortage_stock = Decimal(result["data"]["shortage_qty"])
                    row["shortage_qty"] = text_number(
                        shortage_stock if same_unit else
                        unit_system.convert(shortage_stock, stock_unit, demand_unit))
                    row["shortage_in_stock_unit"] = text_number(shortage_stock)
                    row["executable"] = shortage_stock == 0
                    row["excluded_expired_lots"] = result["data"].get("excluded_expired_lots", [])
                warnings.extend(result.get("warnings", []))
                if result["status"] != "ok":
                    warnings.append(f"{demand['material_code']}：缺少完整批次标识或明确批次日期，无法执行严格FIFO。")
            rows.append(row)
            executable = executable and row["executable"]
        return dict(materials=rows, executable=executable,
            shortages=[r for r in rows if r["shortage_qty"] is not None and Decimal(r["shortage_qty"]) > 0],
            warnings=list(dict.fromkeys(warnings)))

    def preview(self, namespace, owner_id, session_id, product, quantity, demands, provenance=None,
                *, demand_fingerprint=None, execution_authorized=False):
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            return _result("invalid_query", warnings=["生产数量需要为大于0的整数。"])
        if not owner_id or not session_id or not product:
            return _result("invalid_query", warnings=["预览必须绑定用户、会话及产品。"])
        try:
            requirements = self._demands(demands)
        except (ValueError, TypeError, AttributeError) as error:
            return _result("invalid_query", warnings=[str(error)])
        now = datetime.now(UTC)
        with self.ledger.transaction() as connection:
            metadata = self.ledger._namespace(connection, namespace)
            plan = self._plan(connection, namespace, requirements, now.isoformat())
            preview_id = str(uuid.uuid4())
            expires = (now + timedelta(seconds=self.preview_lifetime_seconds)).isoformat()
            payload = dict(namespace=namespace, preview_id=preview_id, product=product,
                requested_quantity=quantity, requirements=requirements, stock_version=metadata["version"],
                session_id=session_id, source_hash=metadata["seed_hash"], demand_fingerprint=demand_fingerprint,
                created_at=now.isoformat(), expires_at=expires, provenance=provenance or {}, **plan)
            payload["execution_available"] = bool(namespace in self.allowed_namespaces
                                                  and self.allow_demo_execution and execution_authorized)
            payload["can_execute"] = bool(payload["execution_available"] and payload["executable"])
            payload["executed"] = False
            connection.execute("INSERT INTO ledger_previews VALUES (?,?,?,?,?,?,?,?)", (
                preview_id, namespace, owner_id, session_id, metadata["version"], json_text(payload), now.isoformat(), expires
            ))
        return _result("ok", payload, plan["warnings"], provenance)

    def execute(self, preview_id, owner_id, session_id, *, confirmed=False, task_id=None,
                current_demand_fingerprint=None, demand_validator=None):
        if confirmed is not True:
            return _result("confirmation_required", warnings=["请先查看预览，再明确确认执行库存扣减。"])
        if not owner_id or not session_id:
            return _result("forbidden", warnings=["无权执行此库存预览。"])
        with self.ledger.transaction() as connection:
            preview = connection.execute("SELECT * FROM ledger_previews WHERE preview_id=?", (preview_id,)).fetchone()
            if not preview or preview["owner_id"] != owner_id or preview["session_id"] != session_id:
                return _result("forbidden", warnings=["无权访问此库存预览。"])
            previous = connection.execute("SELECT result_json FROM ledger_executions WHERE preview_id=?", (preview_id,)).fetchone()
            if previous:
                return json.loads(previous[0])
            if preview["namespace"] not in self.allowed_namespaces or not self.allow_demo_execution:
                return _result("forbidden", warnings=["当前环境不允许执行此库存操作。"])
            now = datetime.now(UTC)
            if now >= datetime.fromisoformat(preview["expires_at"]):
                return _result("preview_expired", warnings=["领料预览已过期，请重新生成预览。"])
            metadata = self.ledger._namespace(connection, preview["namespace"])
            if metadata["version"] != preview["version"]:
                return _result("stale_preview", warnings=["库存已变化，请根据最新库存重新生成预览。"])
            task_id = task_id or preview_id
            if connection.execute("SELECT 1 FROM ledger_executions WHERE task_id=?", (task_id,)).fetchone():
                return _result("task_conflict", warnings=["生产任务标识已绑定其他领料预览。"])
            payload = json.loads(preview["payload_json"])
            if payload.get("execution_available") is not True:
                return _result("forbidden", warnings=["该预览未获得执行授权。"])
            latest_demand = demand_validator() if demand_validator is not None else current_demand_fingerprint
            if payload.get("demand_fingerprint") != latest_demand:
                return _result("stale_preview", warnings=["生产需求已改变，请重新生成领料预览。"])
            if payload.get("source_hash") != metadata["seed_hash"]:
                return _result("stale_preview", warnings=["库存来源版本已改变，请先核对来源。"])
            if self.source_validator is not None and self.source_validator(preview["namespace"]) != metadata["seed_hash"]:
                return _result("stale_preview", warnings=["库存来源已变化，未执行扣减。"])
            plan = self._plan(connection, preview["namespace"], payload["requirements"], now.isoformat())
            if not plan["executable"]:
                return _result("data_unavailable", {**plan, "preview_id": preview_id, "executed": False},
                    [*plan["warnings"], "库存或FIFO条件不满足，本次未扣减任何物料。"])
            # Insert audit task first, within the same transaction as every lot.
            connection.execute("INSERT INTO ledger_executions VALUES (?,?,?,?,?,?,?)", (
                task_id, preview_id, preview["namespace"], owner_id, session_id, "{}", now.isoformat()))
            movements = []
            for row in plan["materials"]:
                for allocation in row["allocations"]:
                    lot = connection.execute("SELECT * FROM ledger_lots WHERE namespace=? AND lot_id=?", (
                        preview["namespace"], allocation["inventory_lot"])).fetchone()
                    used = Decimal(allocation["allocated_qty"])
                    available, on_hand = Decimal(lot["available_qty"]), Decimal(lot["on_hand_qty"])
                    if used <= 0 or available < used or on_hand < used:
                        raise RuntimeError("Inventory changed inside locked transaction")
                    connection.execute("UPDATE ledger_lots SET available_qty=?,on_hand_qty=? WHERE namespace=? AND lot_id=?", (
                        text_number(available-used), text_number(on_hand-used), preview["namespace"], lot["lot_id"]))
                    movement = {**allocation, "material_code": row["material_code"],
                        "on_hand_before": text_number(on_hand), "on_hand_after": text_number(on_hand-used)}
                    movements.append(movement)
                    connection.execute("INSERT INTO ledger_movements(task_id,namespace,material_code,lot_id,before_qty,deducted_qty,after_qty,detail_json,executed_at) VALUES (?,?,?,?,?,?,?,?,?)", (
                        task_id, preview["namespace"], row["material_code"], lot["lot_id"], text_number(available),
                        text_number(used), text_number(available-used), json_text(movement), now.isoformat()))
            connection.execute("UPDATE ledger_namespaces SET version=version+1 WHERE namespace=?", (preview["namespace"],))
            stocks = [self.ledger.stock(preview["namespace"], row["material_code"], connection=connection) for row in plan["materials"]]
            result = _result("ok", dict(executed=True, task_id=task_id, preview_id=preview_id,
                namespace=preview["namespace"], product=payload["product"], requested_quantity=payload["requested_quantity"],
                stock_version=metadata["version"]+1, executed_at=now.isoformat(), movements=movements, stocks=stocks),
                plan["warnings"], payload["provenance"])
            connection.execute("UPDATE ledger_executions SET result_json=? WHERE task_id=?", (json_text(result), task_id))
            return result

    def receipt(self, preview_id, owner_id, session_id):
        """Return only the owning session's durable execution receipt."""
        with self.ledger.transaction() as connection:
            preview = connection.execute("SELECT * FROM ledger_previews WHERE preview_id=?", (preview_id,)).fetchone()
            if not preview or preview["owner_id"] != owner_id or preview["session_id"] != session_id:
                return _result("forbidden", warnings=["无权访问此库存记录。"])
            row = connection.execute("SELECT result_json FROM ledger_executions WHERE preview_id=?", (preview_id,)).fetchone()
            return json.loads(row[0]) if row else _result("data_unavailable", warnings=["该预览尚无执行回执。"])
