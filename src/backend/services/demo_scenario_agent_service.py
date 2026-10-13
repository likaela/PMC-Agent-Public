"""独立比赛场景的会话/显示适配；计算完全复用现有 published Service。"""

from __future__ import annotations

from pathlib import Path

from repositories.agent_conversation_repository import AgentConversationRepository, SessionNotFound
from repositories.demo_scenario_repository import SCENARIO_ID, DemoScenarioRepository, ScenarioManifest
from services.persistent_agent_service import PersistentAgentService
from tools.demo_scenario_tools import build_demo_scenario_tools


class DemoScenarioConversationRepository(AgentConversationRepository):
    def __init__(self, repository: DemoScenarioRepository, manifest: ScenarioManifest):
        super().__init__(repository.store)
        self.manifest = manifest

    def create_session(self, *, initial_context: dict | None = None) -> dict:
        if initial_context is not None:
            raise ValueError("演示上下文只能来自场景元数据")
        manifest = self.manifest
        task = dict(material_code=manifest.product, revision=manifest.revision,
                    effective_date=manifest.effective_date.isoformat(), as_of=manifest.snapshot_at.isoformat(),
                    start_date=manifest.start_date.isoformat(), quantity=None)
        return super().create_session(initial_context={
            "scenario_id": SCENARIO_ID, "synthetic": True, "active_domain": "production",
            "fact": {**task, "query_type": "bom"}, "production": {**task, "query_type": "production_capacity"}})

    def get_session(self, session_id: str) -> dict:
        session = super().get_session(session_id)
        if session["context"].get("scenario_id") != SCENARIO_ID:
            raise SessionNotFound("不是当前场景的会话")
        return {**session, "scenario_id": SCENARIO_ID}


def _presentation(response: dict) -> dict:
    response = {**response, "scenario_id": SCENARIO_ID}
    if response["intent"] == "production_capacity" and response["status"] == "ok":
        data = response["structured_data"].get("data") or (response["structured_data"].get("reused_result") or {}).get("data") or {}
        sufficient = data.get("inventory_sufficient_for_requested")
        quantity = data.get("requested_quantity")
        if quantity is not None and isinstance(sufficient, bool):
            prefix = (f"按独立演示场景的库存齐套口径，可以生产 {quantity} 台。"
                      if sufficient else f"按独立演示场景的库存齐套口径，{quantity} 台方案缺料，无法立即满足生产需求。")
            response["answer"] = prefix + " " + response["answer"] + " 数据全部为合成演示事实，不代表真实企业库存。"
    return response


def build_demo_scenario_agent(*, runtime_root: Path | None = None) -> PersistentAgentService:
    repository = DemoScenarioRepository(runtime_root=runtime_root)
    manifest = repository.initialize()
    tools = build_demo_scenario_tools(repository)
    return PersistentAgentService(DemoScenarioConversationRepository(repository, manifest),
                                  tool_resolver=tools.__getitem__, response_adapter=_presentation)
