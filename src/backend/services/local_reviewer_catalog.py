"""Local reviewer discovery; published enterprise facts and isolated Demo remain separate."""
import json

from repositories.fact_store import PROJECT_ROOT
from repositories.fact_repository import FactRepository
from repositories.fact_query_repository import FactQueryRepository
from services.fact_query_service import FactQueryService
from services.demo_scenario_agent_service import build_demo_scenario_agent
from services.slim_review_service import load_dataset, REAL_SLIM
from tools.published_product_tools import build_published_product_search


def local_review_catalog(store):
    from services.local_business_binding import local_business_binding
    binding = local_business_binding()
    if binding:
        return list(binding.catalog.values())
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
    demo = build_demo_scenario_agent()
    manifest = demo.repository.manifest
    demo_facts = FactQueryService(FactQueryRepository(FactRepository(demo.repository.store)))
    data = demo_facts.query_bom(manifest.product, manifest.revision)["data"]
    rows.append(dict(product_code=manifest.product, product_name=data["parent_item"].get("name"), revision=manifest.revision,
        synthetic=True, unit=data["parent_item"].get("unit"), default_mode="full",
        modes=[dict(mode="full", label="产品分析", scenario_id=manifest.scenario_id, line_count=data["line_count"], dataset_version=None)]))
    return rows
