"""Explicit private review simulation. Real BOM; hypothetical supplementary stock.

Never publishes inventory, edits Excel or uses the remote LLM. Conversations live
in a separate runtime DB. The enterprise and fully synthetic Demo stay unchanged.
"""
import copy
import json
import os
from decimal import Decimal
from pathlib import Path
from threading import RLock

from repositories.agent_conversation_repository import AgentConversationRepository, SessionNotFound
from repositories.book_inventory_repository import BookInventoryRepository
from repositories.fact_store import PROJECT_ROOT, SQLiteFactStore, create_fact_store
from repositories.fact_repository import FactRepository
from repositories.fact_query_repository import FactQueryRepository
from services.fact_query_service import FactQueryService
from services.book_inventory_service import BookInventoryService
from services.persistent_agent_service import PersistentAgentService

SCENARIO_ID = "review_real_bom_simulation"
_lock = RLock()


class ReviewObservations:
    def __init__(self, observations, facts, product, revision, target_quantity=50):
        if isinstance(target_quantity, bool) or not isinstance(target_quantity, int) or target_quantity <= 0:
            raise ValueError("Simulation target must be a positive integer")
        self.observations, self.facts = observations, facts
        self.product, self.revision, self.target_quantity = product, revision, target_quantity

    def load(self):
        if os.getenv("PMC_TRIAL_MODE") == "1":
            raise PermissionError("Private review simulations are disabled in public trial")
        source = copy.deepcopy(self.observations.load())
        bom = self.facts.query_bom(self.product, self.revision)
        if bom["status"] != "ok" or not bom["completeness"].get("unit"):
            raise ValueError("Review requires a published BOM with confirmed units")
        usage = {}
        for line in bom["data"]["lines"]:
            code = line["child_item"]["material_code"]
            if code in usage:
                usage[code][0] += Decimal(line["qty_per"])
            else:
                usage[code] = [Decimal(line["qty_per"]), line["unit"]]
        for code, (per, unit) in usage.items():
            original = BookInventoryService._stock(code, source)
            target = per * self.target_quantity
            known = original["recorded_stock_qty"] is not None and original["inventory_unit"] == unit
            if known:
                supplement = max(target - Decimal(original["recorded_stock_qty"]), Decimal("0"))
            else:
                # This is an explicitly hypothetical balance, not a claim that
                # unknown or duplicate real observations have zero stock.
                supplement = target
                source["records"][code] = []
            if supplement > 0:
                source["records"].setdefault(code, []).append(dict(material_code=code, name=original["name"],
                    recorded_stock_qty=format(supplement,"f"), inventory_unit=unit,
                    location="评审推演合成补充", batch_no="SYNTHETIC-REVIEW", site=None,
                    duplicate_observation=False,
                    provenance=dict(source_file="review-synthetic-supplement", source_kind="synthetic_review",
                        scenario_id=SCENARIO_ID, synthetic=True, snapshot_at=None, import_batch_id=None,
                        original_observations=copy.deepcopy(original["details"]), original_total=original["recorded_stock_qty"],
                        replaces_unresolved_balance=not known,
                        assumption=f"补充至满足{self.target_quantity}片需求，仅供推演")))
        source["quantity_confirmation"] = dict(unit=bom["data"]["parent_item"]["unit"],
            product_scope=[dict(product=self.product,revision=self.revision)],
            source_kind="explicit_review_simulation", scenario_id=SCENARIO_ID, synthetic=True)
        source["review_scenario"] = dict(scenario_id=SCENARIO_ID, synthetic=True,
            product=self.product, revision=self.revision, target_quantity=self.target_quantity,
            label="评审推演：真实BOM + 合成补充库存（不是企业库存）")
        return source


class ReviewConversationRepository(AgentConversationRepository):
    def __init__(self, store, product, revision):
        super().__init__(store)
        self.product, self.revision = product, revision

    def create_session(self, *, initial_context=None):
        if initial_context is not None:
            raise ValueError("Review context comes only from explicit scenario configuration")
        return super().create_session(initial_context=dict(scenario_id=SCENARIO_ID, synthetic=True,
            active_domain="production", fact={}, production=dict(query_type="production_capacity",
                material_code=self.product,revision=self.revision,quantity=None)))

    def get_session(self, session_id):
        session=super().get_session(session_id)
        if session["context"].get("scenario_id") != SCENARIO_ID:
            raise SessionNotFound("Not a review scenario session")
        return {**session,"scenario_id":SCENARIO_ID}


class ReviewBookInventoryService(BookInventoryService):
    def analyze(self, product, revision=None, quantity=None, requested_unit=None):
        identity = self.facts.repository.resolve_item(product)
        code = identity.get("material_code") if identity and not identity.get("ambiguous") else product
        if code != self.observations.product or revision not in (None, self.observations.revision):
            return self._result("data_unavailable", dict(product=product,revision=revision,requested_quantity=quantity),
                None,{},None,["当前评审推演只适用于配置的产品与版本，请切回企业空间查询其他产品。"])
        return super().analyze(product,revision,quantity,requested_unit)


def build_review_scenario_agent(*, facts=None, observations=None, product=None,
                               revision=None, target_quantity=50, runtime_root=None):
    if os.getenv("PMC_TRIAL_MODE") == "1":
        raise PermissionError("Private review scenarios cannot run in public trial")
    if product is None:
        profile=json.loads((PROJECT_ROOT / "data/facts/confirmations/review_scenario.json").read_text(encoding="utf-8"))
        product,revision,target_quantity=profile["product"],profile["revision"],profile["target_quantity"]
    facts=facts or FactQueryService(FactQueryRepository(FactRepository(create_fact_store())))
    if observations is None:
        from repositories.data_source import RAW_INVENTORY_FILE
        observations=BookInventoryRepository(Path(os.getenv("PMC_BOOK_INVENTORY_FILE") or RAW_INVENTORY_FILE))
    root=Path(runtime_root) if runtime_root else PROJECT_ROOT / ".runtime/review_real_bom_simulation"
    with _lock:
        root.mkdir(parents=True,exist_ok=True)
        session_store=SQLiteFactStore(root / "sessions.db")
        session_store.migrate()  # Only the isolated, empty review store.
    book=ReviewBookInventoryService(facts,ReviewObservations(observations,facts,product,revision,target_quantity))
    service=PersistentAgentService(ReviewConversationRepository(session_store,product,revision), book_service=book,
        response_adapter=lambda r: {**r,"scenario_id":SCENARIO_ID})
    return service
