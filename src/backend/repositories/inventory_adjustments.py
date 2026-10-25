"""运行时库存扣减流水（先进先出）。

已发布库存文件带 SHA-256 完整性校验，属于只读发布物；投产扣减以“运行时扣减流水”
方式生效：读取库存时按流水叠加，任何查询（含原有分析）都看到扣减后的账面数量，
同时导出“更新后的库存文件”副本到 data/runtime/inventory_updates/ 供留档核对。

流水按 (物料编码, 批次, 库位) 定位原始记录行，累计扣减，绝不改写已发布文件。
"""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from repositories.fact_store import PROJECT_ROOT


class LegacyInventoryError(RuntimeError):
    """Legacy stock history requires explicit reconciliation; never reset it."""


def _quantity(value) -> Decimal:
    if value is None or isinstance(value, bool):
        raise LegacyInventoryError("旧库存流水数量不完整，需先核对记录。")
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError) as error:
        raise LegacyInventoryError("旧库存流水数量无效，需先核对记录。") from error
    if not result.is_finite() or result < 0:
        raise LegacyInventoryError("旧库存流水数量无效，需先核对记录。")
    return result


def runtime_root() -> Path:
    return Path(os.getenv("PMC_RUNTIME_ROOT") or (PROJECT_ROOT / "data/runtime"))


def journal_path() -> Path:
    return runtime_root() / "inventory_adjustments.json"


def updates_dir() -> Path:
    return runtime_root() / "inventory_updates"


def load_deductions() -> list[dict]:
    path = journal_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LegacyInventoryError("库存历史流水暂不可读取，未使用初始库存替代。") from error
    if not isinstance(data, dict) or data.get("format") != "pmc_inventory_adjustments_v1":
        raise LegacyInventoryError("库存历史流水格式无效，需先核对记录。")
    entries = data.get("deductions")
    if not isinstance(entries, list):
        raise LegacyInventoryError("库存历史流水明细无效，需先核对记录。")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("consumptions"), list):
            raise LegacyInventoryError("库存历史流水明细无效，需先核对记录。")
        for row in entry["consumptions"]:
            if not isinstance(row, dict) or not row.get("material_code"):
                raise LegacyInventoryError("库存历史流水缺少物料标识。")
            _quantity(row.get("consumed_qty"))
    return copy.deepcopy(entries)


def _consumed_by_key() -> dict[tuple, Decimal]:
    consumed: dict[tuple, Decimal] = {}
    for entry in load_deductions():
        for row in entry.get("consumptions") or []:
            key = (row["material_code"], row.get("batch_no"), row.get("location"))
            consumed[key] = consumed.get(key, Decimal("0")) + _quantity(row["consumed_qty"])
    return consumed


def apply_to_records(records: dict) -> dict:
    """把扣减流水叠加到账面记录；只减少数量，不删除、不合并、不补零。"""
    consumed = _consumed_by_key()
    if not consumed:
        return records
    result = {}
    for code, rows in records.items():
        identities = {}
        for row in rows:
            key = (code, row.get("batch_no"), row.get("location"))
            identities.setdefault(key, []).append(row)
        if any(key[0] == code and amount and key not in identities for key, amount in consumed.items()):
            raise LegacyInventoryError("旧库存流水与当前来源行不匹配，需先对账。")
        for key, matching in identities.items():
            if consumed.get(key, Decimal("0")) and len(matching) != 1:
                raise LegacyInventoryError("旧流水无法唯一对应库存行，未合并或重复扣减。")
        updated = []
        for row in rows:
            key = (code, row.get("batch_no"), row.get("location"))
            pending = consumed.get(key)
            item = copy.deepcopy(row)
            if pending:
                available = _quantity(row.get("recorded_stock_qty"))
                if pending > available:
                    raise LegacyInventoryError("旧库存流水超过对应库存数量，需先对账。")
                item["recorded_stock_qty"] = format(available - pending, "f")
                for field in ("on_hand_qty", "available_qty"):
                    if item.get(field) is not None:
                        value = _quantity(item[field])
                        if pending > value:
                            raise LegacyInventoryError("旧库存流水与库存数量口径冲突，需先对账。")
                        item[field] = format(value - pending, "f")
                item["inventory_deduction_applied"] = True
            updated.append(item)
        result[code] = updated
    return result


def append_deduction(entry: dict) -> dict:
    """Retained import name; direct JSON writes are permanently disabled."""
    raise LegacyInventoryError("直接库存扣减已禁用，请先生成领料预览。")


def patch_last_deduction(**fields) -> None:
    raise LegacyInventoryError("旧库存流水仅供只读审计，不能修改历史记录。")


def export_updated_inventory(source_name: str, records: dict, *, label: str) -> str:
    """导出扣减后的库存文件副本（更新后的库存文件），返回相对工程路径。"""
    directory = updates_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": "pmc_inventory_after_deduction_v1",
        "source_file": source_name,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": "投产扣减后的账面库存副本；已发布原文件保持只读，本文件由扣减流水生成。",
        "records": records,
    }
    target = directory / (label + ".json")
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target.relative_to(PROJECT_ROOT)) if target.is_relative_to(PROJECT_ROOT) else str(target)


__all__ = ["append_deduction", "apply_to_records", "export_updated_inventory",
           "journal_path", "load_deductions", "patch_last_deduction", "runtime_root", "updates_dir"]
