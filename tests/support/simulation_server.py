"""隔离的本地模拟执行环境（仅测试与本地验收使用，不属于生产入口）。

组成：
- 事实层：`data/public/bom/published.db` 的**只读副本**（复制到项目外临时目录），
  只取 KQ-Z112 / KQ-BHX3588M 的真实已发布 BOM 结构；
- 库存层：按 BOM 单耗生成的**合成库存**，写入项目外 JSON，账本为项目外 SQLite；
- 身份层：仅回环 + 显式模拟模式时，由服务端中间件注入可信 `InventoryPrincipal`；
  客户端 Header、查询参数与会话 ID 都不能建立身份；
- 写层：只允许 `simulation` 命名空间；企业命名空间恒只读，public 入口不加载本模块。

本模块不修改 `data/` 下任何文件，也不写回真实库存、BOM 或 Excel。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src" / "backend"))

PRODUCTS = ("KQ-Z112", "KQ-BHX3588M")
SIMULATION_OWNER = "local-simulation-operator"
#: 每个物料按"可支持 X 片"的合成库存规模生成；小批量可执行，大批量会不足。
SCALE_PIECES = 50


def _q(value) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.000001")).normalize(), "f")


def published_db_copy(root: Path) -> Path:
    """把发布 BOM 复制到项目外临时目录，避免测试期任何写入落到发布件上。"""
    source = PROJECT_ROOT / "data/public/bom/published.db"
    if not source.is_file():
        raise RuntimeError("Published BOM database is missing")
    target = Path(root) / "facts-published-copy.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != hashlib.sha256(source.read_bytes()).hexdigest():
        shutil.copyfile(source, target)
    return target


def synthetic_observations(facts_by_product: dict) -> dict:
    """按真实 BOM 单耗生成合成库存：每料号两条批次，数量足以支持 SCALE_PIECES 片。"""
    records: dict[str, list[dict]] = {}
    seen: dict[str, Decimal] = {}
    for product, facts in facts_by_product.items():
        revision = facts.revision
        bom = facts.get_published_bom(product, revision)
        if not bom or not bom.get("lines"):
            raise RuntimeError(f"Published BOM is unavailable for {product}")
        for line in bom["lines"]:
            code = line["material_code"]
            unit = line.get("unit") or "片"
            per = Decimal(str(line["quantity"]))
            required = per * SCALE_PIECES
            if code in seen and seen[code] >= required:
                continue
            seen[code] = required
            first = (required * 3 / 5).quantize(Decimal("0.000001"))
            second = required - first
            rows = []
            for index, (batch, location, qty, received) in enumerate((
                    (f"SYN-{index_letter(0)}", "SIM-A", first, "2024-01-15T00:00:00+00:00"),
                    (f"SYN-{index_letter(1)}", "SIM-B", second, "2024-06-15T00:00:00+00:00"))):
                rows.append({
                    "material_code": code,
                    "name": line.get("name") or code,
                    "batch_no": batch,
                    "location": location,
                    "site": "SIM-SITE",
                    "warehouse": "SIM-WAREHOUSE",
                    "inventory_unit": unit,
                    "recorded_stock_qty": _q(qty),
                    "on_hand_qty": _q(qty),
                    "available_qty": _q(qty),
                    "reserved_qty": None,
                    "received_at": received,
                    "last_movement_at": received,
                    "expiry_date": None,
                    "quality_status": "normal",
                    "duplicate_observation": False,
                    "provenance": {"source_file": "synthetic_simulation.json",
                                   "source_kind": "synthetic_simulation",
                                   "row_number": index + 1,
                                   "synthetic": True},
                })
            records[code] = rows
    return {
        "format": "pmc_simulation_observations_v1",
        "synthetic": True,
        "note": "合成库存，仅用于本地模拟执行；不是企业实时库存。",
        "scale_pieces": SCALE_PIECES,
        "unit_confirmation": {"unit": "片", "source_kind": "synthetic_simulation"},
        "records": records,
    }


def index_letter(index: int) -> str:
    return ("A", "B")[index]


class SyntheticObservations:
    """把合成库存 JSON 按 BookInventoryService 需要的观察口径加载。

    只读、每次重新读取；不参与企业真实库存的任何读写。
    """

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("synthetic") is not True:
            raise RuntimeError("Simulation observations must be synthetic")
        records = copy.deepcopy(payload["records"])
        return {
            "source_file": "synthetic_simulation", "sheet_name": "synthetic_simulation",
            "content_sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
            "records": records,
            "unit_confirmation": payload.get("unit_confirmation"),
            "quantity_confirmation": None,
            "record_count": sum(len(rows) for rows in records.values()),
            "unique_material_count": len(records),
            "duplicate_observation_count": sum(
                1 for rows in records.values() for row in rows if row.get("duplicate_observation")),
            "row_errors": [], "excluded_sheets": [],
            "review_scenario": {"synthetic": True, "scope": "simulation"},
        }


def write_synthetic_source(path: Path, payload: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return path


def bootstrap_environment(root: Path) -> dict:
    """设置模拟模式所需的全部环境变量；所有可写路径都在项目之外。"""
    root = Path(root).resolve()
    if root.is_relative_to((PROJECT_ROOT / "data").resolve()):
        raise RuntimeError("Simulation runtime must live outside the released data directory")
    published = PROJECT_ROOT / "data/public/bom/published.db"
    os.environ.update({
        "PMC_SIMULATION_MODE": "1",
        "PMC_RUNTIME_ROOT": str(root),
        "PMC_FACT_DATABASE_URL": "sqlite:///" + published_db_copy(root).as_posix(),
        "PMC_PUBLIC_DATA_ROOT": str(PROJECT_ROOT / "data/public"),
        "PMC_DEMO_INVENTORY_WRITE_ENABLED": "1",  # 仅对模拟命名空间生效
        "PMC_LOCAL_REVIEW": "1",
        "PMC_REVIEW_MODE": "1",
        "PMC_TRIAL_MODE": "0",
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
        "LANGCHAIN_TRACING": "false",
        "PMC_SIMULATION_SOURCE": str(root / "synthetic_inventory.json"),
    })
    return dict(os.environ)


def build_simulation_services(root: Path):
    """构建两个企业产品的服务：真实 BOM 结构 + 合成库存。"""
    from repositories.agent_conversation_repository import AgentConversationRepository
    from repositories.fact_store import SQLiteFactStore
    from services.book_inventory_service import BookInventoryService
    from services.fact_query_service import FactQueryService
    from services.persistent_agent_service import PersistentAgentService
    from services.reviewer_agent_service import ReadOnlyReviewStore, ScopedFacts

    root = Path(root).resolve()
    copy_path = Path(os.environ["PMC_FACT_DATABASE_URL"].split("sqlite:///", 1)[1])
    store = ReadOnlyReviewStore(copy_path)
    repositories = {product: ScopedFacts(store, product) for product in PRODUCTS}
    facts = {product: FactQueryService(repositories[product]) for product in PRODUCTS}
    source = synthetic_observations(repositories)
    source_path = write_synthetic_source(Path(os.environ["PMC_SIMULATION_SOURCE"]), source)
    observations = SyntheticObservations(source_path)

    store_for_sessions = SQLiteFactStore(root / "simulation-sessions.sqlite3")
    store_for_sessions.migrate()
    repository = AgentConversationRepository(store_for_sessions)

    services, catalog = {}, {}
    from services.product_display_names import display_names
    for product in PRODUCTS:
        book = BookInventoryService(facts[product], observations)
        service = PersistentAgentService(repository, book_service=book)
        service.enable_orchestrator()
        service.intent_presentation = True
        services[product] = service
        bom = repositories[product].get_published_bom(product, repositories[product].revision)
        catalog[product] = dict(product_code=product,
            product_name=display_names(product).get("product_name") or product,
            revision=repositories[product].revision, unit=bom["header"].get("parent_unit"),
            synthetic=False, default_mode="full",
            modes=[dict(mode="full", label="模拟执行范围", scenario_id=None,
                        line_count=len(bom["lines"]), dataset_version=None)])
    return services, catalog, repository, source_path


def create_simulation_app(root, *, reset_token=None):
    """真实 FastAPI 应用：挂载既有 Agent/Inventory 路由 + 服务端可信身份。"""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    root = Path(root).resolve()
    bootstrap_environment(root)
    services, catalog, repository, source_path = build_simulation_services(root)

    from api.routes import agent as agent_routes
    from api.routes.inventory import router as inventory_router
    from services import local_business_binding as binding_module
    from services import local_reviewer_catalog
    from services.safe_inventory_runtime import (InventoryPrincipal, SafeInventoryRuntime,
                                                 SIMULATION_NAMESPACE, reset_request_principal,
                                                 set_request_principal)

    # 模拟环境不使用企业只读绑定（那是真实库存路径），产品目录由合成目录提供。
    originals = {
        "binding": binding_module.local_business_binding,
        "catalog": local_reviewer_catalog.local_review_catalog,
        "current": agent_routes._current_service,
        "persistent": agent_routes._persistent_service,
        "fact_store": agent_routes.create_fact_store,
    }
    binding_module.local_business_binding = lambda: None

    def catalog_rows():
        return copy.deepcopy(list(catalog.values()))

    local_reviewer_catalog.local_review_catalog = lambda store=None: catalog_rows()
    agent_routes.local_reviewer_catalog = local_reviewer_catalog

    def current(product=None, session_id=None):
        if session_id:
            context = repository.get_session(session_id)["context"]
            product = (context.get("product_context") or {}).get("product_code")
        return services.get(product or PRODUCTS[0], services[PRODUCTS[0]])

    agent_routes._current_service = current
    agent_routes._persistent_service = lambda scenario=None: services[PRODUCTS[0]]
    agent_routes.create_fact_store = lambda: None

    runtime = SafeInventoryRuntime(root, namespace=SIMULATION_NAMESPACE, allow_demo_execution=True)
    runtime.ensure(SIMULATION_NAMESPACE)

    app = FastAPI(title="PMC-Agent Simulation", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.inventory_runtime = runtime
    app.state.simulation_services = services
    app.state.simulation_catalog = catalog
    app.state.simulation_repository = repository
    app.state.simulation_root = root
    app.state.reset_token = reset_token

    def session_of(sid):
        try:
            return repository.get_session(sid)
        except Exception:
            return None

    @app.middleware("http")
    async def simulation_identity(request: Request, call_next):
        # 身份只来自服务端：显式模拟模式 + 回环客户端 + 已存在的会话。
        if os.getenv("PMC_SIMULATION_MODE") != "1":
            return JSONResponse(status_code=403, content={"code": "SIMULATION_DISABLED", "user_message": "模拟执行未启用。"})
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            return JSONResponse(status_code=403, content={"code": "SIMULATION_LOOPBACK_ONLY", "user_message": "模拟执行仅允许本机回环访问。"})
        sid = request.query_params.get("session_id")
        if not sid and request.method in {"POST", "PUT", "PATCH"}:
            try:
                body = await request.body()
                payload = json.loads(body) if body else {}
                if isinstance(payload, dict):
                    sid = payload.get("session_id")
            except ValueError:
                sid = None
        token = None
        if sid and session_of(sid) is not None:
            principal = InventoryPrincipal(SIMULATION_OWNER, frozenset({sid}),
                                           allowed_namespaces=frozenset({SIMULATION_NAMESPACE}),
                                           can_execute_demo=True)
            request.state.inventory_principal = principal
            token = set_request_principal(principal)
        try:
            response = await call_next(request)
        finally:
            if token is not None:
                reset_request_principal(token)
        response.headers["X-PMC-Simulation"] = "synthetic-ledger"
        return response

    def loader(session_id, scenario):
        session = repository.get_session(session_id)
        product = ((session.get("context") or {}).get("product_context") or {}).get("product_code")
        return services.get(product or PRODUCTS[0]), session

    app.state.inventory_session_loader = loader
    app.include_router(agent_routes.router)
    app.include_router(inventory_router)

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "simulation", "synthetic": True, "products": list(PRODUCTS)}

    def restore():
        """撤销进程级装配，保证测试进程内其他入口不受模拟环境影响。"""
        binding_module.local_business_binding = originals["binding"]
        local_reviewer_catalog.local_review_catalog = originals["catalog"]
        agent_routes._current_service = originals["current"]
        agent_routes._persistent_service = originals["persistent"]
        agent_routes.create_fact_store = originals["fact_store"]

    app.state.simulation_restore = restore

    @app.get("/api/v1/simulation/state")
    def simulation_state():
        """E2E 只读探针：返回合成账本的执行/流水摘要，绝不暴露企业库存。"""
        active = app.state.inventory_runtime
        ledger = active.ledger
        with ledger.transaction() as connection:
            executions = [dict(row) for row in connection.execute(
                "SELECT task_id, preview_id, session_id, owner_id FROM ledger_executions ORDER BY executed_at")]
            previews = [dict(row) for row in connection.execute(
                "SELECT preview_id, session_id, version FROM ledger_previews ORDER BY created_at")]
        return {"namespace": SIMULATION_NAMESPACE, "synthetic": True,
                "version": ledger.version(SIMULATION_NAMESPACE),
                "executions": executions, "previews": previews,
                "movements": [{"material_code": m["material_code"], "before_qty": m["before_qty"],
                               "deducted_qty": m["deducted_qty"], "after_qty": m["after_qty"]}
                              for m in ledger.movements(SIMULATION_NAMESPACE)]}

    @app.post("/api/v1/simulation/reset")
    def reset(token: str | None = None):
        """只重置独立合成账本；不删除会话、真实数据或项目文件。"""
        if reset_token and token != reset_token:
            return JSONResponse(status_code=403, content={"code": "RESET_DENIED", "user_message": "重置需要显式令牌。"})
        fresh = reset_ledger(root)
        app.state.inventory_runtime = fresh
        return {"reset": True, "scope": "simulation-ledger", "products": list(PRODUCTS),
                "ledger": str(root / "inventory" / "ledger.sqlite3"), "source": str(source_path)}

    return app


def reset_ledger(root, runtime=None):
    """供测试调用的重置：删除并重建合成账本（只影响项目外模拟目录）。"""
    from services.safe_inventory_runtime import SIMULATION_NAMESPACE, SafeInventoryRuntime
    root = Path(root).resolve()
    if root.is_relative_to((PROJECT_ROOT / "data").resolve()):
        raise RuntimeError("Refuse to reset a ledger inside the released data directory")
    ledger = root / "inventory" / "ledger.sqlite3"
    for suffix in ("", "-wal", "-shm"):
        target = Path(str(ledger) + suffix)
        if target.exists():
            target.unlink()
    fresh = runtime or SafeInventoryRuntime(root, namespace=SIMULATION_NAMESPACE, allow_demo_execution=True)
    fresh.ensure(SIMULATION_NAMESPACE)
    return fresh


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Local synthetic simulation server (loopback only)")
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--root", default=str(Path(os.getenv("TEMP", "/tmp")) / "pmc-simulation"))
    args = parser.parse_args()
    application = create_simulation_app(args.root)
    uvicorn.run(application, host="127.0.0.1", port=args.port, log_level="warning")
