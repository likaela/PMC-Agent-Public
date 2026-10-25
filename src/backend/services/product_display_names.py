"""产品展示名称与联想别名（只影响展示文案与用户输入识别）。

名称/别名来自 data/public/products/product_names.json；
不修改任何已发布 BOM/库存事实，也不参与业务计算。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from repositories.fact_store import PROJECT_ROOT


def _names_path() -> Path:
    root = os.getenv("PMC_PUBLIC_DATA_ROOT")
    base = Path(root).expanduser().resolve() if root else PROJECT_ROOT / "data/public"
    return base / "products/product_names.json"


@lru_cache(maxsize=1)
def product_display_table() -> dict:
    path = _names_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    products = data.get("products")
    return products if isinstance(products, dict) else {}


def display_names(code: str | None) -> dict:
    if not code:
        return {}
    entry = product_display_table().get(code) or {}
    return {
        "product_name": entry.get("product_name"),
        "aliases": [a for a in (entry.get("aliases") or []) if isinstance(a, str) and a.strip()],
        "description": entry.get("description"),
    }


def enrich_product_rows(rows):
    """为产品目录行补全 product_name 与联想别名（原字段优先，不覆盖已有值）。"""
    table = product_display_table()
    for row in rows:
        if not isinstance(row, dict):
            continue
        entry = table.get(row.get("product_code")) or {}
        if not row.get("product_name") and entry.get("product_name"):
            row["product_name"] = entry["product_name"]
        extra = [a for a in (entry.get("aliases") or []) if isinstance(a, str) and a.strip()]
        if extra:
            merged = list(dict.fromkeys([*(row.get("aliases") or []), *extra]))
            row["aliases"] = merged
    return rows


__all__ = ["display_names", "enrich_product_rows", "product_display_table"]
