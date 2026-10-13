"""Explicit filtered review sessions, isolated from enterprise and original Demo."""
import json
import os
from pathlib import Path

from agents.slim_review_queries import SlimReviewQueries
from repositories.agent_conversation_repository import AgentConversationRepository, SessionNotFound
from repositories.fact_store import PROJECT_ROOT, SQLiteFactStore, create_fact_store
from repositories.fact_repository import FactRepository
from repositories.fact_query_repository import FactQueryRepository
from services.fact_query_service import FactQueryService
from services.persistent_agent_service import PersistentAgentService
from services.slim_review_service import SlimReviewService, load_dataset, SLIM_SCENARIOS
from services.slim_review_presentation import SlimReviewPresentation


class SlimConversationRepository(AgentConversationRepository):
    def __init__(self, store, dataset):
        super().__init__(store)
        self.dataset = dataset

    def create_session(self, *, initial_context=None):
        if initial_context is not None:
            raise ValueError("Filtered context must come from selected dataset")
        d = self.dataset
        return super().create_session(initial_context=dict(scenario_id=d["scenario_id"],
            active_domain="production", production=dict(material_code=d["product"], revision=d["revision"], quantity=None),
            dataset_sha256=d["dataset_sha256"]))

    def get_session(self, session_id):
        s = super().get_session(session_id)
        if s["context"].get("scenario_id") != self.dataset["scenario_id"] or s["context"].get("dataset_sha256") != self.dataset["dataset_sha256"]:
            raise SessionNotFound("Session belongs to a different dataset version")
        return {**s, "scenario_id": self.dataset["scenario_id"]}


class SlimAgentService(PersistentAgentService):
    def public_session(self, session):
        return SlimReviewPresentation(self.book_service.dataset).session(session)

    def enable_orchestrator(self):
        super().enable_orchestrator()
        self.book_queries = SlimReviewQueries(self.book_service)


def build_slim_scenario_agent(scenario_id, *, dataset_path=None, facts=None, runtime_root=None):
    if os.getenv("PMC_TRIAL_MODE") == "1" or scenario_id not in SLIM_SCENARIOS:
        raise PermissionError("Private filtered scenario is not allowed")
    if dataset_path is None:
        profile = json.loads((PROJECT_ROOT / "data/facts/confirmations/review_slim.json").read_text(encoding="utf-8"))
        dataset_path = PROJECT_ROOT / profile[scenario_id]
    dataset = load_dataset(dataset_path)
    if dataset["scenario_id"] != scenario_id:
        raise ValueError("Scenario/dataset mismatch")
    facts = facts or FactQueryService(FactQueryRepository(FactRepository(create_fact_store())))
    root = Path(runtime_root) if runtime_root else PROJECT_ROOT / ".runtime" / scenario_id / dataset["dataset_version"]
    root.mkdir(parents=True, exist_ok=True)
    store = SQLiteFactStore(root / "sessions.db")
    store.migrate()  # This isolated conversation store only, never enterprise DB.
    presentation = SlimReviewPresentation(dataset)
    return SlimAgentService(SlimConversationRepository(store, dataset), book_service=SlimReviewService(facts, dataset),
        response_adapter=lambda r: {**presentation.response(r), "scenario_id": scenario_id})
