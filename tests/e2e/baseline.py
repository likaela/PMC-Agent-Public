"""独立数据基准：只读解析发布 BOM 与库存观察，供自然语言 E2E 断言使用。

本脚本不导入后端业务服务，也不调用 HTTP 接口，避免"用被测系统证明自己"。
数据来源仅限：

* ``data/public/bom/published.db``（只读 URI 打开）
* ``data/public/inventory/observations.json``

口径（与项目已公布规则一致，逐条在 README 中说明）：

1. BOM 需求 = 单耗 × 生产数量；
2. 库存按 ``(batch_no, location)`` 分组取最大值后求和，避免同批重复导出重复累加；
3. 标了 ``duplicate_observation`` 的料号视为"待核验"，不参与确定性缺口；
4. 单位必须同维度且可换算，本数据全部为「片」。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
BOM_DB = PROJECT / "data/public/bom/published.db"
OBSERVATIONS = PROJECT / "data/public/inventory/observations.json"
QUANTITIES = (1, 2, 10, 50, 54, 55, 100)


def load_bom() -> dict[str, dict]:
    connection = sqlite3.connect(f"file:{BOM_DB.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    headers = {row["parent_material_code"]: row for row in connection.execute("SELECT * FROM bom_headers")}
    products = {}
    for code, header in headers.items():
        rows = connection.execute(
            "SELECT i.material_code AS material_code, i.name AS name, b.quantity AS quantity, b.unit AS unit "
            "FROM bom_lines b JOIN items i ON i.id = b.item_id WHERE b.bom_header_id = ?", (header["id"],)).fetchall()
        products[code] = {
            "revision": header["version"],
            "lines": [{"material_code": r["material_code"], "name": r["name"],
                       "quantity": str(r["quantity"]), "unit": r["unit"]} for r in rows],
        }
    connection.close()
    return products


def load_inventory() -> dict:
    payload = json.loads(OBSERVATIONS.read_text(encoding="utf-8"))["observations"]
    records = payload["records"]
    stock: dict[str, Decimal] = {}
    for code, rows in records.items():
        if not rows or any(row.get("recorded_stock_qty") is None for row in rows):
            continue
        groups: dict[tuple, Decimal] = {}
        for row in rows:
            key = (row.get("batch_no"), row.get("location"))
            value = Decimal(str(row["recorded_stock_qty"]))
            groups[key] = max(groups.get(key, value), value)
        stock[code] = sum(groups.values(), Decimal("0"))
    return {"payload": payload, "records": records, "stock": stock}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(Path(__file__).with_name("baseline.json")))
    args = parser.parse_args()

    bom, inventory = load_bom(), load_inventory()
    records, stock = inventory["records"], inventory["stock"]
    payload = inventory["payload"]
    batches = {row.get("batch_no") for rows in records.values() for row in rows if row.get("batch_no")}
    locations = {row.get("location") for rows in records.values() for row in rows if row.get("location")}

    products: dict[str, dict] = {}
    for code, data in bom.items():
        lines = data["lines"]
        distinct = {line["material_code"] for line in lines}
        duplicates = sorted(c for c in distinct
                            if any(row.get("duplicate_observation") for row in records.get(c, [])))
        missing = sorted(c for c in distinct if c not in records)
        comparable = [line for line in lines
                      if line["material_code"] in stock and line["material_code"] not in duplicates]
        limits = []
        for line in comparable:
            per = Decimal(line["quantity"])
            if per > 0:
                limits.append((int(stock[line["material_code"]] // per), line["material_code"],
                               stock[line["material_code"]], per))
        limits.sort(key=lambda item: (item[0], item[1]))
        checks = {}
        for quantity in QUANTITIES:
            shortages = []
            for limit, material, on_hand, per in limits:
                required = per * quantity
                if on_hand < required:
                    shortages.append({"material_code": material, "required": str(required),
                                      "stock": str(on_hand), "gap": str(required - on_hand)})
            checks[str(quantity)] = {"shortage_count": len(shortages), "sufficient": not shortages,
                                     "shortages": sorted(shortages, key=lambda item: item["material_code"])[:6]}
        products[code] = {
            "revision": data["revision"],
            "bom_lines": len(lines),
            "distinct_materials": len(distinct),
            "comparable_materials": len(comparable),
            "duplicate_unverified": duplicates,
            "missing_record": missing,
            "max_producible": limits[0][0] if limits else None,
            "limiting_material": limits[0][1] if limits else None,
            "limiting_stock": str(limits[0][2]) if limits else None,
            "limiting_per": str(limits[0][3]) if limits else None,
            "quantity_checks": checks,
        }

    focus = "105A510014201"
    material = {"material_code": focus, "stock": str(stock.get(focus)) if focus in stock else None,
                "records": len(records.get(focus, [])),
                "batch": records[focus][0].get("batch_no") if records.get(focus) else None,
                "location": records[focus][0].get("location") if records.get(focus) else None,
                "per": {code: next((line["quantity"] for line in data["lines"]
                                    if line["material_code"] == focus), None) for code, data in bom.items()}}

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "bom": "data/public/bom/published.db (sqlite readonly)",
            "inventory": "data/public/inventory/observations.json",
            "inventory_sha256": payload.get("content_sha256"),
            "inventory_source_file": payload.get("source_file"),
        },
        "rules": [
            "required = qty_per * quantity",
            "stock = sum(max(qty) per (batch_no, location))",
            "duplicate_observation materials are unverified and excluded from deterministic gaps",
            "missing records are never treated as zero stock",
        ],
        "catalog": sorted(bom),
        "products": products,
        "inventory": {
            "materials": len(records),
            "records": sum(len(rows) for rows in records.values()),
            "batches": len(batches),
            "locations": len(locations),
            "duplicate_materials": sum(1 for rows in records.values()
                                       if any(row.get("duplicate_observation") for row in rows)),
            "unit_confirmation": (payload.get("unit_confirmation") or {}).get("unit"),
        },
        "focus_material": material,
    }
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({code: {k: v for k, v in data.items() if k != "quantity_checks"}
                      for code, data in products.items()}, ensure_ascii=False, indent=1))
    print("inventory", output["inventory"])
    print("written", args.out)


if __name__ == "__main__":
    main()
