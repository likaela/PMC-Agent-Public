"""Authorized read-only review binding. No new BOM/stock/planning algorithms."""
from __future__ import annotations

import copy
import hashlib
import re
import sqlite3
from pathlib import Path

from langchain_core.tools import StructuredTool
from agents.context_resolver import ContextResolver
from agents.evidence import EvidenceCollector
from agents.orchestrator import AgentOrchestrator
from agents.planner import AgentPlanner
from agents.response_generator import ResponseGenerator
from agents.tool_registry import ToolRegistry
from agents.business_formatter import format_business_answer
from agents.enterprise_book_queries import EnterpriseBookQueries
from repositories.agent_context_repository import AgentContextRepository
from repositories.agent_conversation_repository import AgentConversationRepository
from repositories.fact_store import SQLiteFactStore
from repositories.fact_repository import FactRepository
from repositories.fact_query_repository import FactQueryRepository
from repositories.book_inventory_repository import BookInventoryRepository
from repositories.inventory_unit_confirmation_repository import (
    InventoryUnitConfirmationRepository, InventoryQuantityConfirmationRepository,
)
from services.book_inventory_service import BookInventoryService
from services.fact_query_service import FactQueryService
from services.persistent_agent_service import PersistentAgentService
from services.production_feasibility_query_service import ProductionFeasibilityQueryService
from services.demo_scenario_agent_service import build_demo_scenario_agent

ENTERPRISE_PRODUCTS = ("KQ-Z112", "KQ-BHX3588M")
DEMO_PRODUCT = "DEMO-PMC-01"
DEMO_SCENARIO = "demo_scenario_50_vs_100"


class ReadOnlyReviewStore(SQLiteFactStore):
    def __init__(self, path):
        self.database_path = str(Path(path).resolve(strict=True))

    def connect(self):
        connection = sqlite3.connect(Path(self.database_path).as_uri() + "?mode=ro", uri=True, timeout=30)
        connection.execute("PRAGMA query_only=ON")
        return connection

    def migrate(self):
        raise PermissionError("Review must never migrate enterprise facts")


class ScopedFacts(FactQueryRepository):
    def __init__(self, store, product):
        super().__init__(FactRepository(store))
        self.product = product
        versions = super().list_published_bom_versions(product)
        if len(versions) != 1:
            raise ValueError("Review requires an unambiguous published product revision")
        self.revision = versions[0]["version"]
        bom = super().get_published_bom(product, self.revision)
        self.allowed = {product, *(row["material_code"] for row in bom["lines"])}

    def resolve_item(self, code):
        result = super().resolve_item(code)
        return result if result and not result.get("ambiguous") and result["material_code"] in self.allowed else None

    def list_published_bom_versions(self, code):
        return super().list_published_bom_versions(code) if code in self.allowed else []

    def get_published_bom(self, code, revision):
        return super().get_published_bom(code, revision) if code in self.allowed else None


class ScopedObservations:
    """Filter disclosure scope, not quantities or duplicate handling."""
    def __init__(self, repository, allowed):
        self.repository, self.allowed = repository, allowed

    def load(self):
        source = copy.deepcopy(self.repository.load())
        source["records"] = {k: v for k, v in source["records"].items() if k in self.allowed}
        source["row_errors"] = [e for e in source["row_errors"] if e.get("material_code") in self.allowed]
        source["excluded_sheets"] = []
        source["record_count"] = sum(map(len, source["records"].values()))
        source["unique_material_count"] = len(source["records"])
        return source


def _bound_tools(facts, capacity):
    def inventory(material_code: str, as_of: str | None = None):
        return facts.query_inventory(material_code, as_of)
    def batches(material_code: str, as_of: str | None = None):
        return facts.query_inventory_batches(material_code, as_of)
    def bom(parent_material_code: str, revision: str):
        return facts.query_bom(parent_material_code, revision)
    def production(product: str, bom_version: str, requested_quantity: int | None = None,
                   as_of: str | None = None, effective_date: str | None = None):
        return capacity.query(product, bom_version, requested_quantity=requested_quantity, as_of=as_of, effective_date=effective_date)
    return {kind: StructuredTool.from_function(fn, name="review_" + kind, description="Bound read-only published query")
        for kind, fn in dict(inventory=inventory, inventory_batches=batches, bom=bom, production_capacity=production).items()}


class ReviewBinding:
    def __init__(self, *, facts_path, inventory_path, units_path, quantity_path, runtime_root, slim_dataset_path=None,
                 business_manifest_path=None):
        self.runtime = Path(runtime_root).resolve()
        if Path(facts_path).resolve() == self.runtime / "sessions.sqlite3":
            raise ValueError("Facts and conversation stores must differ")
        self.fact_store = ReadOnlyReviewStore(facts_path)
        session_path = self.runtime / "sessions.sqlite3"
        if session_path.exists():
            with sqlite3.connect(session_path.as_uri() + "?mode=ro", uri=True) as conn:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "review_store_identity" not in tables or conn.execute(
                    "SELECT purpose FROM review_store_identity").fetchall() != [("authorized_review_conversations",)]:
                    raise ValueError("Existing runtime store is not an authorized review conversation store")
        self.sessions = SQLiteFactStore(self.runtime / "sessions.sqlite3")
        self.sessions.migrate()  # Only the new, separate conversation store.
        with self.sessions.transaction(write_lock=True) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS review_store_identity (purpose TEXT NOT NULL)")
            if not conn.execute("SELECT 1 FROM review_store_identity").fetchone():
                conn.execute("INSERT INTO review_store_identity VALUES (?)", ("authorized_review_conversations",))
        self.repository = AgentConversationRepository(self.sessions)
        if Path(inventory_path).suffix == ".json":
            from repositories.public_data_repository import PublicInventoryRepository
            observations = PublicInventoryRepository(inventory_path)
        else:
            observations = BookInventoryRepository(Path(inventory_path),
                unit_confirmations=InventoryUnitConfirmationRepository(units_path),
                quantity_confirmations=InventoryQuantityConfirmationRepository(quantity_path))
        self.services, self.facts, self.catalog = {}, {}, {}
        for product in ENTERPRISE_PRODUCTS:
            if business_manifest_path is not None:
                from services.current_business_service import build_current_business_agent, current_entry
                from repositories.current_business_repository import CurrentBusinessRepository
                current_source = CurrentBusinessRepository(business_manifest_path)
                if current_source.product == product:
                    current = build_current_business_agent(business_manifest_path, runtime_root=self.runtime / "current-business")
                    self.services[product] = current
                    self.facts[product] = current.book_service.facts
                    self.catalog[product] = current_entry(current_source)
                    continue
            repository = ScopedFacts(self.fact_store, product)
            facts = FactQueryService(repository)
            self.facts[product] = facts
            book = BookInventoryService(facts, ScopedObservations(observations, repository.allowed))
            service = PersistentAgentService(self.repository, book_service=book)
            capacity = ProductionFeasibilityQueryService(facts)

            def unavailable(**query):
                return dict(status="data_unavailable", query=query, data=None, completeness={}, provenance=None,
                    warnings=["当前授权评审数据没有完整的采购、在途或风险事实；未生成推断结果。"])

            tools = _bound_tools(facts, capacity)
            from pydantic import BaseModel, ConfigDict
            class UnavailableArguments(BaseModel):
                model_config = ConfigDict(extra="allow")
            blocked = StructuredTool.from_function(unavailable, args_schema=UnavailableArguments,
                name="review_unavailable", description="No authorized facts for this capability")
            tools.update(inbound=blocked, purchase_planning=blocked)
            service.tool_resolver = tools.__getitem__  # Includes legacy inbound delegation, never global tools.
            disabled = dict(llm_status=lambda: {"status": "warning", "provider": "review_no_egress"})
            # Existing planner/orchestrator/evidence/formatter, explicitly bound.
            service.orchestrator = AgentOrchestrator(ToolRegistry(resolver=tools.__getitem__, scope="enterprise",
                analysis_resolver=lambda name: blocked, product_resolver=lambda p=product: self.product_tool([p])),
                planner=AgentPlanner(enterprise_mode=True, **disabled),
                response_generator=ResponseGenerator(formatter=format_business_answer, **disabled),
                context_resolver=ContextResolver(AgentContextRepository(repository)))
            service.book_queries = EnterpriseBookQueries(book)
            self.services[product] = service
            data = facts.query_bom(product, repository.revision)["data"]
            self.catalog[product] = dict(product_code=product, product_name=data["parent_item"].get("name"),
                revision=repository.revision, unit=data["parent_item"].get("unit"), line_count=data["line_count"],
                synthetic=False, capabilities=["BOM结构", "主库存查询", "生产需求数量", "已知库存数量核对", "已核算物料产量上限", "来源及连续追问"],
                limitations=["数量核算仅采用已核验库存记录", "数量核对不代表正式工单放行"])
        demo = build_demo_scenario_agent(runtime_root=self.runtime / "demo")
        demo.enable_orchestrator()
        # Defense independent of global model configuration/import order.
        demo.orchestrator.planner.llm_status = lambda: {"status": "warning"}
        demo.orchestrator.response_generator.llm_status = lambda: {"status": "warning"}
        self.services[DEMO_PRODUCT] = demo
        from agents.reviewer_catalog_queries import ReviewerOverviewQueries
        demo.book_queries = ReviewerOverviewQueries(demo, DEMO_PRODUCT)
        manifest = demo.repository.manifest
        self.demo_facts = FactQueryService(FactQueryRepository(FactRepository(demo.repository.store)))
        data = self.demo_facts.query_bom(manifest.product, manifest.revision)["data"]
        self.catalog[DEMO_PRODUCT] = dict(product_code=manifest.product, product_name=data["parent_item"].get("name"),
            revision=manifest.revision, unit=data["parent_item"].get("unit"), line_count=data["line_count"], synthetic=True,
            capabilities=["BOM与库存", "齐套与最大理论量", "采购建议", "历史恢复"], limitations=["演示产品数据，仅用于体验分析流程，不用于业务放行"])
        for product, entry in self.catalog.items():
            entry["scenario_id"] = DEMO_SCENARIO if product == DEMO_PRODUCT else None
            if entry.get("default_mode") == "current":
                continue
            entry["default_mode"] = "full"
            entry["modes"] = [dict(mode="full", label="产品分析" if entry["synthetic"] or business_manifest_path is not None else "完整追溯范围",
                scenario_id=entry["scenario_id"], line_count=entry["line_count"], dataset_version=None)]
        self.slim = None
        if business_manifest_path is None and slim_dataset_path is not None:
            from services.slim_scenario_agent_service import build_slim_scenario_agent
            from agents.slim_review_queries import SlimReviewQueries
            from services.slim_review_service import REAL_SLIM
            self.slim = build_slim_scenario_agent(REAL_SLIM, dataset_path=slim_dataset_path,
                facts=self.facts[ENTERPRISE_PRODUCTS[0]], runtime_root=self.runtime / "slim")
            dataset = self.slim.book_service.dataset
            if dataset["product"] != ENTERPRISE_PRODUCTS[0] or dataset["synthetic"]:
                raise ValueError("Default review must bind the approved real filtered dataset")
            # All queries terminate in the existing filtered Tool, never global facts or a model.
            self.slim.orchestrator = self.services[dataset["product"]].orchestrator
            self.slim.book_queries = SlimReviewQueries(self.slim.book_service)
            self.catalog[dataset["product"]].update(default_mode="slim")
            self.catalog[dataset["product"]]["modes"].insert(0, dict(mode="slim", label="标准分析范围",
                scenario_id=REAL_SLIM, line_count=dataset["retained_material_count"], dataset_version=dataset["dataset_version"]))

    def mode(self, product, mode=None):
        mode = mode or self.catalog[product]["default_mode"]
        return next((m for m in self.catalog[product]["modes"] if m["mode"] == mode), None)

    def service(self, product, mode):
        for service in [*self.services.values(), self.slim]:
            if service is not None:
                service.intent_presentation = True
        if mode == "slim" and self.slim and product == self.slim.book_service.dataset["product"]:
            return self.slim
        if mode in ("full", "current") and self.mode(product, mode):
            return self.services[product]
        raise ValueError("Unknown review scope")

    def product_tool(self, products):
        def search(search_term: str | None = None, limit: int = 50):
            rows = [dict(product_code=p, product_name=self.catalog[p]["product_name"], published_bom_status="published",
                revisions=[dict(revision=self.catalog[p]["revision"])]) for p in products
                if not search_term or search_term in p or search_term in (self.catalog[p]["product_name"] or "")]
            return dict(status="ok", query={"search_term": search_term}, data={"products": rows[:limit], "truncated": False},
                completeness={"published_bom": True}, provenance={"source_kind": "authorized_published_catalog"}, warnings=[])
        return StructuredTool.from_function(search, name="published_product_search", description="Authorized published product catalog")

    def materials(self, product, mode=None):
        selected = self.mode(product, mode)
        if selected is None:
            raise ValueError("Unknown review scope")
        if selected["mode"] in ("slim", "current"):
            from services.slim_review_presentation import SlimReviewPresentation
            service = self.service(product, selected["mode"])
            result = service.book_service.analyze(product)
            presentation = getattr(service, "presentation", SlimReviewPresentation(service.book_service.dataset))
            data = presentation.result(result)["data"]
            return [dict(material_code=r["material_code"], name=r["name"], qty_per=r["qty_per"],
                unit=r["unit"], stock_qty=r["recorded_stock_qty"]) for r in data["materials"]]
        facts = self.demo_facts if product == DEMO_PRODUCT else self.facts[product]
        result = facts.query_bom(product, self.catalog[product]["revision"])
        rows = []
        queue = [(product, result)]
        seen = set()
        while queue:
            parent, current = queue.pop(0)
            if parent in seen or len(seen) >= 64:
                raise ValueError("Invalid catalog structure")
            seen.add(parent)
            for row in current["data"]["lines"]:
                child = row["child_item"]["material_code"]
                rows.append({"material_code": child, "name": row["child_item"].get("name"),
                    "qty_per": row["qty_per"], "unit": row.get("unit"), "parent_material_code": parent})
                # Display published relationships; quantity expansion remains in the existing Service.
                if product == DEMO_PRODUCT:
                    versions = facts.repository.list_published_bom_versions(child)
                    if len(versions) == 1:
                        queue.append((child, facts.query_bom(child, versions[0]["version"])))
        if product != DEMO_PRODUCT:
            observed = self.services[product].book_service.analyze(product)
            stocks = {r["material_code"]: r for r in (observed.get("data") or {}).get("materials", [])}
            for row in rows:
                row["stock_qty"] = stocks.get(row["material_code"], {}).get("recorded_stock_qty")
        return rows


_PRIVATE_KEYS = {"content_sha256", "source_sha256", "sha256", "source_uri", "storage_key", "source_path", "original_filename",
    "confirmed_by", "user_statement", "confirmation_id", "quantity_confirmation", "unit_confirmation",
    "bom_header_id", "source_file_id", "item_id", "after_state", "before_state", "evidence_uri", "evidence_sha256", "id"}


def public_review_payload(value, _source_names=None):
    """Same evidence facts; restricted source disclosures, including history."""
    if _source_names is None:
        _source_names = set()
        def collect(node):
            if isinstance(node, dict):
                for key, child in node.items():
                    if key in {"original_filename", "source_file", "source_filename", "file_name"} and isinstance(child, str):
                        _source_names.add(child)
                    collect(child)
            elif isinstance(node, list):
                for child in node:
                    collect(child)
        collect(value)
    if isinstance(value, list):
        return [public_review_payload(v, _source_names) for v in value]
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key in {"unit_confirmation", "quantity_confirmation"}:
                result[key] = {k: child[k] for k in ("unit", "quantity_role", "source_kind", "applies_to_existing_source_only")
                    if k in child} if isinstance(child, dict) else None
                continue
            if key in _PRIVATE_KEYS:
                continue
            if key == "reason" and "confirmed_by" in value:
                continue
            if key in {"source_file", "source_filename", "file_name"}:
                result[key] = "已授权业务来源" if child else None
            elif key == "import_batch_id" and child:
                result[key] = "批次-" + hashlib.sha256(str(child).encode()).hexdigest()[:12]
            else:
                result[key] = public_review_payload(child, _source_names)
        return result
    if isinstance(value, str):
        for source in sorted(_source_names, key=len, reverse=True):
            if source:
                value = value.replace(source, "已授权业务来源")
        value = re.sub(r"[A-Za-z]:[\\/][^\s\"，。；]+", "[内部路径已隐藏]", value)
        value = re.sub(r"\b[a-fA-F0-9]{64}\b", "[内部校验已隐藏]", value)
        value = re.sub(r"[^\s，；：。“”\"]+\.(?:xlsx|xls|sqlite3|db)\b", "已授权业务来源", value, flags=re.I)
    return value
