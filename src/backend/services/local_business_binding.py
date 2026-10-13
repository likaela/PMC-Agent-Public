"""Local UI uses the same current sources as review, with separate conversations."""
import json
import os
from pathlib import Path
from functools import lru_cache

from repositories.current_business_repository import active_manifest
from repositories.fact_store import PROJECT_ROOT


@lru_cache(maxsize=4)
def _binding(manifest, digest):
    from services.reviewer_agent_service import ReviewBinding
    from repositories.public_data_repository import public_root, release_settings
    if public_root():
        paths = release_settings()
        return ReviewBinding(**{k:paths[k] for k in ("facts_path", "inventory_path", "units_path", "quantity_path")},
            business_manifest_path=manifest,
            runtime_root=Path(os.getenv("PMC_RUNTIME_ROOT", str(PROJECT_ROOT / "data/runtime"))) / "business")
    settings = json.loads((PROJECT_ROOT / "deploy/review/data.local.json").read_text(encoding="utf-8"))
    return ReviewBinding(**{k: PROJECT_ROOT / settings[k] for k in ("facts_path", "inventory_path", "units_path", "quantity_path")},
        business_manifest_path=manifest, runtime_root=PROJECT_ROOT / ".runtime/local-business")


def local_business_binding():
    if os.getenv("PMC_TRIAL_MODE") == "1":
        return None
    manifest = active_manifest()
    if manifest is None:
        return None
    if os.getenv("PMC_LOCAL_REVIEW") != "1":
        from repositories.fact_store import create_fact_store, DEFAULT_SQLITE_PATH
        from pathlib import Path
        database = getattr(create_fact_store(), "database_path", None)
        if not database or Path(database).resolve() != DEFAULT_SQLITE_PATH.resolve():
            return None
    import hashlib
    return _binding(str(manifest), hashlib.sha256(manifest.read_bytes()).hexdigest())


def local_business_service(product=None, session_id=None):
    binding = local_business_binding()
    if not binding:
        return None
    if product in binding.catalog:
        return binding.service(product, binding.catalog[product]["default_mode"])
    if session_id:
        from repositories.agent_conversation_repository import SessionNotFound
        for product in binding.catalog:
            service = binding.service(product, binding.catalog[product]["default_mode"])
            try:
                service.repository.get_session(session_id)
                return service
            except SessionNotFound:
                continue
        raise SessionNotFound("Archived local session")
    from services.persistent_agent_service import PersistentAgentService
    # Catalog-only blank sessions; no enterprise DB write or model export.
    service = PersistentAgentService(binding.repository)
    from agents.orchestrator import AgentOrchestrator
    from agents.planner import AgentPlanner
    from agents.response_generator import ResponseGenerator
    from agents.tool_registry import ToolRegistry
    disabled = dict(llm_status=lambda: {"status":"warning", "provider":"private_local_only"})
    def unavailable(kind):
        raise ValueError("Select a product before a business tool")
    service.orchestrator = AgentOrchestrator(ToolRegistry(resolver=unavailable, scope="enterprise"),
        planner=AgentPlanner(enterprise_mode=True, **disabled), response_generator=ResponseGenerator(**disabled))
    return service
