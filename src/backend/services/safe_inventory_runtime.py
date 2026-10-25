"""Isolated inventory-operation boundary, separate from immutable release inputs.

Public Agent conversations can create read-only previews. Enterprise execution is
always disabled. Optional Demo execution requires a server-supplied principal;
neither a session ID nor a shared HTTP token supplies that identity.
"""
from __future__ import annotations

import contextvars
import copy
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import logging
import os
import uuid
from pathlib import Path

from repositories.inventory_adjustments import LegacyInventoryError, load_deductions
from repositories.inventory_ledger_repository import InventoryLedgerRepository, json_text
from repositories.public_data_repository import release_settings
from services.inventory_execution_service import InventoryExecutionService

logger = logging.getLogger(__name__)

#: 请求级可信身份。只有服务端中间件可以写入（模拟测试环境），客户端无法设置。
_REQUEST_PRINCIPAL: contextvars.ContextVar = contextvars.ContextVar("pmc_inventory_principal", default=None)


def set_request_principal(principal):
    return _REQUEST_PRINCIPAL.set(principal)


def reset_request_principal(token) -> None:
    _REQUEST_PRINCIPAL.reset(token)


@dataclass(frozen=True)
class InventoryPrincipal:
    """Injected by trusted server middleware, never deserialized from a request."""
    owner_id: str
    session_ids: frozenset[str]
    allowed_namespaces: frozenset[str] = frozenset({"demo"})
    can_execute_demo: bool = False

    def owns(self, session_id):
        return bool(self.owner_id and session_id in self.session_ids)


def session_id_of(session):
    value = session.get("session_id") or session.get("id")
    if not isinstance(value, str) or not value:
        raise ValueError("A persistent session is required")
    return value


def demand_fingerprint(session):
    context = session.get("context") or {}
    if "current_demand" in context:
        task = context["current_demand"]
        if not isinstance(task, dict):
            task = {"status": "invalid"}
        value = {key: task.get(key) for key in ("generation", "status", "product", "quantity", "unit")}
    else:
        # Legacy history is not promoted to a new request or across products.
        binding = context.get("product_context") or {}
        code = binding.get("product_code")
        value = {"product": code, "revision": binding.get("revision")}
        for key in ("production", "book_analysis", "slim_analysis"):
            candidate = context.get(key) or {}
            if code and (candidate.get("product") or candidate.get("material_code")) == code:
                value.update(quantity=candidate.get("quantity"), status=candidate.get("status"))
                break
    return hashlib.sha256(json_text(value).encode()).hexdigest()


def _released_source(namespace):
    paths = release_settings()
    if namespace == "enterprise":
        return json.loads(paths["inventory_path"].read_text(encoding="utf-8"))["observations"]
    if namespace != "demo":
        raise ValueError("Unsupported inventory space")
    items = {row["material_code"]: row for row in json.loads(paths["demo_items"].read_text(encoding="utf-8"))}
    source = {"source_file": "demo/inventory_facts.json", "records": {}, "quantity_confirmation": None}
    for index, raw in enumerate(json.loads(paths["demo_inventory_facts"].read_text(encoding="utf-8"))):
        code = raw["material_code"]
        row = {**raw, "inventory_unit": items[code]["unit"], "name": items[code]["name"],
               "recorded_stock_qty": raw["on_hand_qty"], "duplicate_observation": False,
               # The existing DemoScenarioRepository._publish explicitly binds
               # these synthetic identifiers; retain that same published identity.
               "site": "DEMO-SITE", "warehouse": "DEMO-WAREHOUSE",
               "provenance": {"source_file": "demo/inventory_facts.json", "row_number": index + 1}}
        source["records"].setdefault(code, []).append(row)
    return source


def _legacy_guard():
    if load_deductions():
        raise LegacyInventoryError("存在旧库存扣减流水；请先离线对账，未自动迁移或重复扣减。")


# ---------------------------------------------------------------- 模拟执行（仅本地合成账本）
# 明确与真实企业库存隔离：模拟模式必须显式开启、账本与库存来源都必须位于项目数据目录之外，
# 且来源文件必须自证为合成数据。正式 public/review/trial 入口不会设置这些开关。
SIMULATION_NAMESPACE = "simulation"
SIMULATION_FORMAT = "pmc_simulation_observations_v1"


def simulation_enabled() -> bool:
    return os.getenv("PMC_SIMULATION_MODE") == "1"


def _project_data_root() -> Path:
    from repositories.fact_store import PROJECT_ROOT
    return (Path(PROJECT_ROOT) / "data").resolve()


def _refuse_released_path(path: Path, what: str) -> None:
    if path.resolve().is_relative_to(_project_data_root()):
        raise ValueError(f"{what} cannot live inside the released/production data directory")


def simulation_source_path() -> Path:
    raw = os.getenv("PMC_SIMULATION_SOURCE")
    if not raw:
        raise ValueError("Simulation mode requires PMC_SIMULATION_SOURCE")
    path = Path(raw).expanduser()
    if not path.is_file():
        raise ValueError("Simulation inventory source is missing")
    _refuse_released_path(path, "Simulation inventory source")
    return path.resolve()


def _simulation_source(namespace):
    if namespace != SIMULATION_NAMESPACE:
        raise ValueError("Unsupported inventory space")
    path = simulation_source_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("Simulation inventory source is unreadable") from error
    if data.get("format") != SIMULATION_FORMAT or data.get("synthetic") is not True:
        raise ValueError("Simulation inventory source must be explicitly synthetic")
    records = data.get("records")
    if not isinstance(records, dict) or not records:
        raise ValueError("Simulation inventory source has no records")
    source = {
        "source_file": "synthetic_simulation", "sheet_name": "synthetic_simulation",
        "records": records, "synthetic": True,
        "unit_confirmation": data.get("unit_confirmation"),
        "quantity_confirmation": None,
        "record_count": sum(len(rows) for rows in records.values()),
        "unique_material_count": len(records),
        "duplicate_observation_count": sum(
            1 for rows in records.values() for row in rows if row.get("duplicate_observation")),
        "row_errors": [], "excluded_sheets": [],
        "provenance": {"source_kind": "synthetic_simulation", "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
    }
    return source


class SafeInventoryRuntime:
    def __init__(self, root, *, source_provider=None, allow_demo_execution=None, namespace=None):
        _legacy_guard()
        self.root = Path(root).resolve()
        self.namespace = namespace
        if namespace == SIMULATION_NAMESPACE:
            _refuse_released_path(self.root, "Simulation ledger")
            if not simulation_enabled():
                raise ValueError("Simulation namespace requires PMC_SIMULATION_MODE=1")
        self.allowed_namespaces = (SIMULATION_NAMESPACE,) if namespace == SIMULATION_NAMESPACE else ("demo",)
        self.ledger = InventoryLedgerRepository(self.root / "inventory" / "ledger.sqlite3")
        self.source_provider = source_provider or (
            _simulation_source if namespace == SIMULATION_NAMESPACE else _released_source)
        self.allow_demo_execution = (os.getenv("PMC_DEMO_INVENTORY_WRITE_ENABLED") == "1"
                                     if allow_demo_execution is None else allow_demo_execution is True)

    def ensure(self, namespace):
        _legacy_guard()
        if namespace not in self.allowed_namespaces:
            raise ValueError("Inventory space is not enabled in this runtime")
        source = self.source_provider(namespace)
        basis = "confirmed_actual_stock" if namespace == "enterprise" and source.get("quantity_confirmation") else "published_available"
        self.ledger.seed(namespace, source, quantity_basis=basis)
        return source

    def validate_source(self, namespace):
        _legacy_guard()
        return hashlib.sha256(json_text(self.source_provider(namespace)).encode()).hexdigest()

    def execution(self):
        return InventoryExecutionService(self.ledger, allow_demo_execution=self.allow_demo_execution,
                                         allowed_namespaces=self.allowed_namespaces,
                                         source_validator=self.validate_source)


def runtime():
    from repositories.inventory_adjustments import runtime_root
    if simulation_enabled():
        return SafeInventoryRuntime(runtime_root(), namespace=SIMULATION_NAMESPACE)
    return SafeInventoryRuntime(runtime_root())


def overlay_observations(source):
    """Only immutable inputs are cached; a persisted balance is read every time."""
    from repositories.inventory_adjustments import apply_to_records, runtime_root
    path = runtime_root() / "inventory" / "ledger.sqlite3"
    if not path.exists():
        value = copy.deepcopy(source)
        value["records"] = apply_to_records(value["records"])
        return value
    active = runtime()
    with closing(active.ledger.connect()) as connection:
        row = connection.execute("SELECT 1 FROM ledger_namespaces WHERE namespace='enterprise'").fetchone()
    if row is None:
        value = copy.deepcopy(source)
        value["records"] = apply_to_records(value["records"])
        return value
    active.ensure("enterprise")  # Reject changed source identities; never reseed balances.
    current = active.ledger.load_observations("enterprise")
    value = copy.deepcopy(source)
    for code, rows in value["records"].items():
        candidates = list(current["records"].get(code, []))
        def identity(row):
            return tuple(row.get(key) for key in ("material_code", "batch_no", "location", "site"))
        if len(rows) != len(candidates) or sorted(map(identity, rows), key=str) != sorted(map(identity, candidates), key=str):
            raise ValueError("Inventory projection cannot be uniquely matched to runtime rows")
        for row in rows:
            position = next(index for index, item in enumerate(candidates) if identity(row) == identity(item))
            live = candidates.pop(position)
            for field in ("recorded_stock_qty", "on_hand_qty", "available_qty", "ledger_lot_id"):
                row[field] = live[field]
            row.setdefault("provenance", {})["runtime_ledger"] = live["provenance"]["runtime_ledger"]
    value.update(ledger_version=current["ledger_version"], ledger_namespace="enterprise")
    return value


def preview_issue(service, session, product, quantity, *, principal=None, inventory_runtime=None,
                  requested_unit=None, namespace=None):
    """Agent adapter: the result is always a preview, never an execution.

    命名空间优先级：显式参数 > 运行实例自带的命名空间 > 产品默认（Demo/企业）。
    企业空间恒为只读；只有 Demo 或模拟空间在服务端可信授权下才可能执行。
    单位口径沿用统一单位模块：requested_unit 来自当前有效生产需求，缺失时由
    BookInventoryService 按成品单位/默认计数单位解析并保留来源标记。
    """
    query = {"product": product, "quantity": quantity}
    if requested_unit is not None:
        query["requested_unit"] = requested_unit
    try:
        if type(quantity) is not int or quantity <= 0:
            raise ValueError("生产数量需要为大于0的整数。")
        sid = session_id_of(session)
        current = (session.get("context") or {}).get("current_demand")
        if current is not None and (not isinstance(current, dict) or current.get("status") != "valid"
                or current.get("product") != product or current.get("quantity") != quantity):
            raise ValueError("The preview does not match the current valid production request")
        active = inventory_runtime
        space = namespace or (active.namespace if active is not None else None)
        if space is None:
            if simulation_enabled():
                active = active or runtime()
                space = active.namespace
            elif product == "DEMO-PMC-01":
                active = active or runtime()
                space = "demo"
            else:
                space = "enterprise"
        book = getattr(service, "book_service", None)
        if book is not None:
            calculated = book.analyze(product, quantity=quantity, requested_unit=requested_unit)
        else:
            manifest = service.repository.manifest
            if manifest.product != product:
                raise ValueError("Product does not belong to this session")
            calculated = service.tool_resolver("production_capacity").invoke({
                "product": product, "bom_version": manifest.revision, "requested_quantity": quantity,
                "effective_date": manifest.effective_date.isoformat(), "as_of": manifest.snapshot_at.isoformat()})
        data = calculated.get("data") or {}
        materials = data.get("materials") or []
        if not materials:
            return {"status": "data_unavailable", "query": query, "data": None, "completeness": {},
                    "provenance": calculated.get("provenance"), "warnings": ["没有完整物料需求，未生成领料预览。"]}
        if space == "enterprise":
            # Product-specific and master Excel projections can have different
            # verified row sets. A read-only preview must not seed/replace either
            # with the other, and cannot enable real enterprise execution.
            now = datetime.now(UTC)
            preview = {**copy.deepcopy(data), "namespace": space,
                "preview_id": "read-only-" + str(uuid.uuid4()), "session_id": sid,
                "product": product, "requested_quantity": quantity,
                "created_at": now.isoformat(), "expires_at": (now + timedelta(minutes=15)).isoformat(),
                "demand_fingerprint": demand_fingerprint(session), "executed": False,
                "executable": False, "can_execute": False, "execution_available": False,
                "write_policy": "enterprise_read_only"}
            return {"status": calculated.get("status", "data_unavailable"), "query": query,
                    "data": preview, "completeness": {**calculated.get("completeness", {}), "executed": False},
                    "provenance": calculated.get("provenance"),
                    "warnings": ["当前入口仅提供领料预览；企业库存执行未启用。"]}
        active = active or runtime()
        if active.namespace is not None and space != active.namespace:
            # 模拟运行实例只能操作自己的命名空间，绝不落回 Demo 或企业空间。
            raise ValueError("Inventory space does not match the active runtime")
        active.ensure(space)
        # 未显式传入时，取服务端请求级身份（仅模拟测试环境的中间件会写入）。
        principal = principal if principal is not None else _REQUEST_PRINCIPAL.get()
        trusted = isinstance(principal, InventoryPrincipal) and principal.owns(sid)
        owner = principal.owner_id if trusted else "preview-only:" + sid
        result = active.execution().preview(space, owner, sid, product, quantity,
            [{"material_code": row["material_code"], "required_qty": row.get("required_qty"), "unit": row.get("unit")} for row in materials],
            calculated.get("provenance"), demand_fingerprint=demand_fingerprint(session),
            execution_authorized=bool(trusted and principal.can_execute_demo and space in principal.allowed_namespaces))
        if result.get("data"):
            result["data"].update(product_unit=data.get("product_unit"), revision=data.get("revision"),
                                 write_policy="synthetic_simulation_only" if space == SIMULATION_NAMESPACE
                                 else "authenticated_demo_only",
                                 synthetic=space == SIMULATION_NAMESPACE)
        result.update(query=query, completeness={"executed": False, "preview": result.get("data") is not None})
        return result
    except (ValueError, TypeError) as error:
        logger.info("Inventory preview parameters/source rejected: %s", type(error).__name__)
        return {"status": "invalid_query", "query": query, "data": None, "completeness": {},
                "provenance": None, "warnings": ["请核对生产数量、产品与库存来源后重新预览。"]}
    except Exception:
        logger.exception("Inventory preview failed; no inventory execution attempted")
        return {"status": "query_failed", "query": query, "data": None, "completeness": {},
                "provenance": None, "warnings": ["领料预览暂不可用；本次没有扣减库存。"]}


from repositories.published_purchase_repository import PublishedPurchaseRepository


class OperationalDemoRepository(PublishedPurchaseRepository):
    """Existing Demo Services read live balances through their repository seam."""
    def __init__(self, facts):
        super().__init__(facts)
        self.inventory_runtime = runtime()
        self.inventory_runtime.ensure("demo")
        self.ledger = self.inventory_runtime.ledger

    @staticmethod
    def _key(row):
        return tuple(row.get(field) for field in ("batch_no", "location", "site", "warehouse"))

    @classmethod
    def _balances(cls, batches):
        rows = {}
        for row in batches:
            key = cls._key(row)
            if key in rows:
                raise ValueError("Ambiguous Demo lot identity; no balance projection was selected")
            rows[key] = row
        return rows

    def get_inventory_snapshot(self, item_id, as_of):
        original = super().get_inventory_snapshot(item_id, as_of)
        if original is None:
            return None
        result = copy.deepcopy(original)
        code = result["facts"][0]["raw_material_code"]
        live = self.ledger.stock("demo", code)
        rows = self._balances(live["batches"])
        for row in result["facts"]:
            balance = rows[self._key(row)]
            for field in ("on_hand_qty", "available_qty"):
                row[field] = Decimal(balance[field]) if balance[field] is not None else None
        result["snapshot"]["runtime_ledger"] = {"namespace": "demo", "version": live["version"]}
        return result

    def get_lots(self, item_id, as_of):
        result = super().get_lots(item_id, as_of)
        if not result or result.get("ambiguous"):
            return result
        with self.store.transaction() as connection:
            code = self.store.execute(connection, "SELECT material_code FROM items WHERE id=?", (item_id,)).fetchone()[0]
        live = self.ledger.stock("demo", code)
        rows = self._balances(live["batches"])
        for lot in result["lots"]:
            balance = rows[self._key(lot)]
            lot.update(on_hand_qty=balance["on_hand_qty"], available_qty=balance["available_qty"])
        result["snapshot"]["runtime_ledger"] = {"namespace": "demo", "version": live["version"]}
        return result
