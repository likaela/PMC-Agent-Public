"""Immutable, checksum-bound product BOM, inventory and alias configuration.

The production entry binds the manifest from its verified public data package.
The separate Demo-only entry cannot load business product configurations.
"""
import copy
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path

from repositories.fact_store import PROJECT_ROOT


def active_manifest():
    if os.getenv("PMC_TRIAL_MODE") == "1":
        return None
    from repositories.public_data_repository import public_root, release_settings
    if public_root():
        return release_settings()["business_manifest_path"]
    configured = os.getenv("PMC_CURRENT_BUSINESS_MANIFEST")
    if configured:
        return Path(configured)
    profile = PROJECT_ROOT / "data/facts/confirmations/current_business.json"
    if not profile.exists():
        return None
    value = json.loads(profile.read_text(encoding="utf-8"))
    return PROJECT_ROOT / value["manifest_path"]


class CurrentBusinessRepository:
    def __init__(self, manifest_path):
        if os.getenv("PMC_TRIAL_MODE") == "1":
            raise PermissionError("Business configurations are not trial data")
        self.path = Path(manifest_path).resolve(strict=True)
        raw = self.path.read_bytes()
        self.manifest_hash = hashlib.sha256(raw).hexdigest()
        self.manifest = json.loads(raw)
        if self.manifest.get("format") != "current_business_bom_v1" or self.manifest.get("synthetic") is not False:
            raise ValueError("Invalid current business manifest")
        self.version = self.manifest["dataset_version"]
        self.product = self.manifest["product"]
        self.revision = self.manifest["revision"]
        self.bom = self._read("bom")
        self.observations = self._read("inventory")
        self.aliases = self._read("aliases")
        lines = self.bom["data"]["lines"]
        codes = {r["child_item"]["material_code"] for r in lines}
        if not codes or len(codes) != len(lines) or set(self.observations["records"]) != codes or set(self.aliases) != codes:
            raise ValueError("Working BOM, inventory and aliases do not match")
        if self.observations["record_count"] != sum(len(rows) for rows in self.observations["records"].values()) or self.observations["unique_material_count"] != len(codes):
            raise ValueError("Working inventory counts do not match its actual rows")
        if self.bom["data"]["revision"] != self.revision or self.bom["data"]["line_count"] != len(lines):
            raise ValueError("Working BOM version/count mismatch")
        confirmation = self.manifest["confirmations"]
        if any(confirmation[k]["content_sha256"] != self.manifest["files"][k]["sha256"] for k in ("bom", "inventory")):
            raise ValueError("Confirmation does not bind current source hash")
        self.observations["content_sha256"] = self.manifest["files"]["inventory"]["sha256"]
        self.observations["quantity_confirmation"].update(content_sha256=self.observations["content_sha256"],
            source_uri=str(self.path.parent / self.manifest["files"]["inventory"]["path"]))
        self.bom["provenance"]["content_sha256"] = self.manifest["files"]["bom"]["sha256"]
        for line in lines:
            line["provenance"]["content_sha256"] = self.manifest["files"]["bom"]["sha256"]
        for r in lines:
            code = r["child_item"]["material_code"]
            per = Decimal(r["qty_per"])
            rows = self.observations["records"][code]
            if not per.is_finite() or per <= 0 or not r["unit"] or not rows:
                raise ValueError("Invalid working BOM relation")
            for row in rows:
                stock = Decimal(row["recorded_stock_qty"])
                if not stock.is_finite() or stock < 0 or row["duplicate_observation"] or row["inventory_unit"] != r["unit"]:
                    raise ValueError("Unverified working inventory row")
        self.items = {self.product: self.bom["data"]["parent_item"], **{r["child_item"]["material_code"]: r["child_item"] for r in lines}}
        self.allowed = set(self.items)

    def _read(self, key):
        entry = self.manifest["files"][key]
        path = (self.path.parent / entry["path"]).resolve(strict=True)
        if path.parent != self.path.parent:
            raise ValueError("Working source must be a sibling of its manifest")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("Working source integrity mismatch")
        return json.loads(raw)

    def resolve_item(self, code):
        reverse = {v: k for k, v in self.aliases.items()}
        canonical = reverse.get(code, code)
        item = self.items.get(canonical)
        return {**copy.deepcopy(item), "matched_by": "alias" if canonical != code else "material_code"} if item else None

    def verify_sources(self, keys=("bom", "inventory", "aliases")):
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != self.manifest_hash:
            raise ValueError("Current manifest changed; start a new configuration binding")
        for key in keys:
            self._read(key)

    def list_published_bom_versions(self, code):
        return [dict(version=self.revision, effective_from=self.bom["data"]["effective_from"], effective_to=None)] if code == self.product else []


class CurrentBusinessFacts:
    def __init__(self, repository):
        self.repository = repository

    def query_bom(self, parent_material_code, revision):
        r = self.repository
        r.verify_sources(("bom", "aliases"))
        if parent_material_code != r.product or revision != r.revision:
            return dict(status="data_unavailable", query=dict(parent_material_code=parent_material_code, revision=revision),
                data=None, completeness={}, provenance=None, warnings=["该版本已归档或不属于当前BOM配置，请新建当前产品分析。"])
        return copy.deepcopy(r.bom)


class CurrentBusinessObservations:
    def __init__(self, repository):
        self.repository = repository

    def load(self):
        self.repository.verify_sources(("inventory",))
        records = copy.deepcopy(self.repository.observations)
        # 投产扣减流水叠加：已发布文件保持只读，查询按扣减后账面数量生效。
        from services.safe_inventory_runtime import overlay_observations
        return overlay_observations(records)
