"""Current product configuration using the existing quantity/capacity engine."""
import copy
from pathlib import Path

from agents.slim_review_queries import SlimReviewQueries
from repositories.agent_conversation_repository import AgentConversationRepository, SessionNotFound
from repositories.current_business_repository import CurrentBusinessRepository, CurrentBusinessFacts, CurrentBusinessObservations
from repositories.fact_store import PROJECT_ROOT, SQLiteFactStore
from services.book_inventory_service import BookInventoryService
from services.persistent_agent_service import PersistentAgentService
from services.slim_review_service import SlimReviewService
from services.slim_review_presentation import SlimReviewPresentation


class CurrentBusinessService(SlimReviewService):
    def __init__(self, repository):
        self.current_repository = repository
        # Adapter fields for the existing capacity engine; quantities are unchanged.
        self.dataset = dict(product=repository.product, revision=repository.revision,
            dataset_version=repository.version, dataset_sha256=repository.manifest_hash,
            bom=repository.bom, observations=repository.observations, material_aliases=repository.aliases,
            retained_material_count=len(repository.aliases), original_material_count=len(repository.aliases),
            excluded=[], synthetic=False, scenario_id=None, scenario_name="当前BOM配置", current_business=True)
        BookInventoryService.__init__(self, CurrentBusinessFacts(repository), CurrentBusinessObservations(repository))

    def analyze(self, *args, **kwargs):
        result = super().analyze(*args, **kwargs)
        if result.get("data"):
            # Retain successfully calculated BOM demand if a later inventory read fails.
            # These are presentation/binding fields, never substitute stock or capacity.
            data = result["data"]
            data.setdefault("limiting_materials", [])
            data.update(slim_review=True, synthetic=False, dataset_version=self.dataset["dataset_version"])
            if data.get("stock_read_failed"):
                result["status"] = "data_unavailable"
        return result


class CurrentBusinessPresentation(SlimReviewPresentation):
    def result(self, result):
        value = super().result(result)
        data = value.get("data")
        if data:
            for key in ("original_material_count", "excluded_material_count", "scenario_id", "scenario_product_code", "scenario_name"):
                data.pop(key, None)
            data.update(current_business=True, configuration_version=self.dataset["dataset_version"])
            bom = self.dataset["bom"]["data"]
            data["line_count"] = len(bom["lines"])
            data["parent_item"] = {k: bom["parent_item"].get(k) for k in ("material_code", "name", "unit")}
            data["effective_from"] = bom["effective_from"]
            data["lines"] = [dict(child_item=dict(material_code=self.aliases[line["child_item"]["material_code"]],
                name="物料" + self.aliases[line["child_item"]["material_code"]][4:], unit=line["unit"]),
                qty_per=line["qty_per"], unit=line["unit"], price=line.get("price"),
                completeness=copy.deepcopy(line["completeness"]),
                provenance=dict(source_file="当前业务BOM", dataset_version=self.dataset["dataset_version"])) for line in bom["lines"]]
            data["source_summary"] = dict(source_file="当前业务库存", unit_confirmation=dict(unit=data["product_unit"]))
        value["provenance"].pop("filtered_dataset", None)
        value["provenance"]["current_configuration"] = dict(dataset_version=self.dataset["dataset_version"],
            revision=self.dataset["revision"], material_count=len(self.aliases))
        value["provenance"]["bom"].update(source_file="当前业务BOM", dataset_version=self.dataset["dataset_version"])
        value["provenance"]["inventory"].update(source_file="当前业务库存", dataset_version=self.dataset["dataset_version"])
        value["warnings"] = (["物料数量核对不等于正式工单审批。"]
                             if data and data.get("requested_quantity") is not None else [])
        return value

    def session(self, session):
        value = super().session(session)
        value["context"]["product_context"] = copy.deepcopy(session["context"].get("product_context"))
        for turn in value.get("turns", []):
            for ref in turn.get("fact_references", []):
                if ref["tool_name"] == "bom_query" and ref["result"].get("data"):
                    bom = ref["result"]["data"]
                    bom["parent_item"] = {k: bom["parent_item"].get(k) for k in ("material_code", "name", "unit")}
                    for line in bom["lines"]:
                        code = line["child_item"]["material_code"]
                        alias = self.aliases.get(code, code)
                        line["child_item"] = dict(material_code=alias, name="物料" + alias[4:], unit=line["unit"])
                        line.pop("bom_line_id", None)
                        line["provenance"] = dict(source_file="当前业务BOM", dataset_version=self.dataset["dataset_version"])
                    ref["result"]["provenance"] = dict(source_file="当前业务BOM", dataset_version=self.dataset["dataset_version"])
                    ref["provenance"] = copy.deepcopy(ref["result"]["provenance"])
        return value


class CurrentConversationRepository(AgentConversationRepository):
    def __init__(self, store, source):
        super().__init__(store)
        self.source = source

    def create_session(self, *, initial_context=None):
        r = self.source
        binding = dict(product_code=r.product, revision=r.revision, dataset_version=r.version,
            analysis_mode="current", scenario_id=None)
        task = dict(material_code=r.product, revision=r.revision, quantity=None)
        return super().create_session(initial_context=dict(active_domain="production", production=task,
            fact={**task, "query_type":"bom"}, product_context=binding, current_configuration_hash=r.manifest_hash))

    def get_session(self, sid):
        result = super().get_session(sid)
        if result["context"].get("current_configuration_hash") != self.source.manifest_hash:
            raise SessionNotFound("Archived configuration; start a current analysis")
        return result


class CurrentBusinessAgent(PersistentAgentService):
    def public_session(self, session):
        result = self.presentation.session(session)
        result["product_context"] = copy.deepcopy(session["context"]["product_context"])
        return result

    def enable_orchestrator(self):
        from langchain_core.tools import StructuredTool
        from agents.orchestrator import AgentOrchestrator
        from agents.planner import AgentPlanner
        from agents.response_generator import ResponseGenerator
        from agents.tool_registry import ToolRegistry
        from agents.business_formatter import format_business_answer
        facts = self.book_service.facts
        def bom(parent_material_code: str, revision: str):
            return facts.query_bom(parent_material_code, revision)
        tool = StructuredTool.from_function(bom, name="current_business_bom", description="Read current immutable working BOM")
        def resolve(kind):
            if kind == "bom":
                return tool
            raise ValueError("Capability not available in current configuration")
        disabled = dict(llm_status=lambda: {"status":"warning", "provider":"private_local_only"})
        self.orchestrator = AgentOrchestrator(ToolRegistry(resolver=resolve, scope="enterprise"),
            planner=AgentPlanner(enterprise_mode=True, **disabled), response_generator=ResponseGenerator(formatter=format_business_answer, **disabled))
        self.book_queries = SlimReviewQueries(self.book_service, presentation=self.presentation)
        self.intent_presentation = True


def build_current_business_agent(manifest_path, *, runtime_root=None):
    source = CurrentBusinessRepository(manifest_path)
    root = Path(runtime_root) if runtime_root else PROJECT_ROOT / ".runtime/current-business" / source.version
    root.mkdir(parents=True, exist_ok=True)
    store = SQLiteFactStore(root / "sessions.sqlite3")
    store.migrate()  # Separate version-bound conversations, never original enterprise facts.
    book = CurrentBusinessService(source)
    presentation = CurrentBusinessPresentation(book.dataset)
    service = CurrentBusinessAgent(CurrentConversationRepository(store, source), book_service=book,
        response_adapter=presentation.response)
    service.presentation = presentation
    service.enable_orchestrator()
    return service


def current_entry(source):
    return dict(product_code=source.product, product_name=source.bom["data"]["parent_item"].get("name"),
        revision=source.revision, unit=source.bom["data"]["parent_item"]["unit"], line_count=len(source.aliases),
        synthetic=False, scenario_id=None, default_mode="current",
        modes=[dict(mode="current", label="当前BOM", scenario_id=None, line_count=len(source.aliases), dataset_version=source.version)],
        capabilities=["BOM查询", "库存数量", "生产需求核对", "最大满足数量", "来源及连续追问"],
        limitations=["数量核对适用于当前BOM配置，不等于正式生产放行"])
