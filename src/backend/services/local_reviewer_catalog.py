"""Local reviewer discovery; published enterprise facts and isolated Demo remain separate.

产品目录只暴露两个已发布企业产品。Demo 场景仅作为隔离的合成测试环境保留在代码与
`data/public/demo/` 中，不再作为第三个可选择产品出现在任何目录/别名/回退路径上。
"""
import json

from repositories.fact_store import PROJECT_ROOT
from repositories.fact_repository import FactRepository
from repositories.fact_query_repository import FactQueryRepository
from services.fact_query_service import FactQueryService
from services.slim_review_service import load_dataset, REAL_SLIM
from tools.published_product_tools import build_published_product_search

#: 业务允许进入运行时产品目录的唯一产品编码。
PRODUCT_CATALOG = ("KQ-Z112", "KQ-BHX3588M")


def published_catalog_rows(rows):
    """只保留两个企业产品；合成/演示行永不进入产品目录。"""
    return [row for row in rows
            if row.get("product_code") in PRODUCT_CATALOG and row.get("synthetic") is not True]


def local_review_catalog(store):
    from services.local_business_binding import local_business_binding
    from services.product_display_names import enrich_product_rows
    binding = local_business_binding()
    if binding:
        return enrich_product_rows(published_catalog_rows(list(binding.catalog.values())))
    result = build_published_product_search(store).invoke({"limit": 100})
    facts = FactQueryService(FactQueryRepository(FactRepository(store)))
    rows = []
    for product in (result.get("data") or {}).get("products", []):
        if len(product["revisions"]) != 1:
            continue  # Never choose an ambiguous revision for a selector.
        revision = product["revisions"][0]["revision"]
        bom = facts.query_bom(product["product_code"], revision)
        if bom["status"] != "ok":
            continue
        data = bom["data"]
        rows.append(dict(product_code=product["product_code"], product_name=product["product_name"], revision=revision,
            synthetic=False, unit=data["parent_item"].get("unit"), default_mode="full",
            modes=[dict(mode="full", label="完整追溯范围", scenario_id=None, line_count=data["line_count"], dataset_version=None)]))
    profile = PROJECT_ROOT / "data/facts/confirmations/review_slim.json"
    from repositories.current_business_repository import active_manifest, CurrentBusinessRepository
    from services.current_business_service import current_entry
    manifest = active_manifest()
    from repositories.fact_store import DEFAULT_SQLITE_PATH
    from pathlib import Path
    path = getattr(store, "database_path", None)
    current = CurrentBusinessRepository(manifest) if manifest and path and Path(path).resolve() == DEFAULT_SQLITE_PATH.resolve() else None
    if current:
        rows = [current_entry(current) if row["product_code"] == current.product else row for row in rows]
    elif not manifest and profile.is_file():
        dataset = load_dataset(PROJECT_ROOT / json.loads(profile.read_text(encoding="utf-8"))[REAL_SLIM])
        for product in rows:
            if product["product_code"] == dataset["product"] and not dataset["synthetic"]:
                product["default_mode"] = "slim"
                product["modes"].insert(0, dict(mode="slim", label="标准分析范围", scenario_id=REAL_SLIM,
                    line_count=dataset["retained_material_count"], dataset_version=dataset["dataset_version"]))
    # Demo 产品不再作为目录项暴露；其合成账本仅供隔离测试环境使用。
    return enrich_product_rows(published_catalog_rows(rows))
