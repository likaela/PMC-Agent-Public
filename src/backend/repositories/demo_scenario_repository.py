"""只把受版本控制的合成场景加载到独立文件库，绝不使用企业数据库 URL。"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, field_validator

from repositories.fact_repository import FactRepository
from repositories.fact_store import PROJECT_ROOT, SQLiteFactStore


SCENARIO_ID = "demo_scenario_50_vs_100"
SCENARIO_ROOT = PROJECT_ROOT / "data" / "public" / "demo" / SCENARIO_ID
_lock = RLock()


class _ScenarioSQLiteStore(SQLiteFactStore):
    def begin_transaction(self, connection, *, write_lock: bool) -> None:
        # 小型独立 Demo 库串行事务，包含 bootstrap migration；多进程初始化也不重复执行迁移。
        # 只读查询仍不写业务数据；该策略不影响企业 SQLite/PostgreSQL Store。
        super().begin_transaction(connection, write_lock=True)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScenarioManifest(_Model):
    scenario_id: Literal["demo_scenario_50_vs_100"]
    synthetic: Literal[True]
    title: str
    product: str
    revision: str
    effective_from: date
    effective_date: date
    snapshot_at: datetime
    start_date: date

    @field_validator("snapshot_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("演示时点需要时区")
        return value


class ScenarioItem(_Model):
    material_code: str
    name: str
    unit: str


class ScenarioBomLine(_Model):
    parent_material_code: str
    revision: str
    child_material_code: str
    qty_per: str
    unit: str


class ScenarioLot(_Model):
    material_code: str
    batch_no: str
    location: str
    on_hand_qty: str
    available_qty: str
    reserved_qty: str
    received_at: datetime
    expiry_date: date

    @field_validator("received_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("批次日期需要时区")
        return value


class ScenarioPurchaseParameters(_Model):
    material_code: str
    supplier_code: str
    unit: str
    safety_stock_qty: str
    minimum_order_quantity: str
    order_multiple: str | None
    packaging_multiple: str | None
    order_multiple_applicable: bool
    packaging_multiple_applicable: bool
    lead_time_days: int
    tax_included_unit_price: str
    currency: str


class ScenarioInbound(_Model):
    material_code: str
    supplier_code: str
    order_no: str
    ordered_qty: str
    received_qty: str
    in_transit_qty: str
    expected_arrival_date: date
    status: str


def _number(value: str, *, positive: bool = False) -> Decimal:
    number = Decimal(value)
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError("演示数量/价格需要有限非负十进制数")
    return number


def _id(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"demo://{SCENARIO_ID}/{key}"))


class DemoScenarioRepository:
    """私有企业 FactStore 不作为参数；已有非场景数据库一律拒绝加载。"""

    def __init__(self, *, runtime_root: Path | None = None, source_root: Path | None = None):
        from repositories.public_data_repository import public_root, release_settings
        public = public_root()
        if public:
            released_source = release_settings(public)["demo_manifest"].parent
            if source_root is not None and source_root.resolve() != released_source:
                raise ValueError("Public deployment Demo must use the released data source")
            source_root = released_source
        source_root = source_root or SCENARIO_ROOT
        default_runtime = (Path(os.environ["PMC_RUNTIME_ROOT"]) / "demo" if os.getenv("PMC_PUBLIC_DATA_ROOT") and os.getenv("PMC_RUNTIME_ROOT")
                           else PROJECT_ROOT / "data" / "runtime" / "demo")
        root = (runtime_root or default_runtime).resolve()
        if any(root.is_relative_to((PROJECT_ROOT / "data" / private).resolve()) for private in ("raw", "facts")):
            raise ValueError("演示运行库禁止位于真实数据目录")
        self.source_root = source_root.resolve()
        self.store = _ScenarioSQLiteStore(root / "demo_facts.sqlite3")
        self.facts = FactRepository(self.store)

    def initialize(self) -> ScenarioManifest:
        """幂等发布固定场景；数据包改变时拒绝静默覆盖旧事实及历史问答。"""
        manifest, records, sources, fingerprint = self._load_package()
        with _lock:
            # 先验证库归属，再 migration，防止对非场景库作任何结构改动。
            with self.store.transaction(write_lock=True) as connection:
                tables = {row[0] for row in self.store.execute(connection,
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()}
                if tables and "demo_scenario_identity" not in tables:
                    raise ValueError("目标不是本场景的独立数据库，拒绝加载")
                if "demo_scenario_identity" in tables:
                    row = self.store.execute(connection,
                        "SELECT scenario_id, package_sha256 FROM demo_scenario_identity WHERE singleton=1").fetchone()
                    if row is None or row != (SCENARIO_ID, fingerprint):
                        raise ValueError("场景身份或数据包校验值不一致，拒绝覆盖")
                    if "import_batches" in tables and self.store.execute(connection,
                        "SELECT COUNT(*) FROM import_batches WHERE mapping_name NOT LIKE ?",
                        (f"{SCENARIO_ID}/%",)).fetchone()[0]:
                        raise ValueError("场景库混入非场景批次，拒绝加载")
                else:
                    self.store.execute(connection,
                        "CREATE TABLE demo_scenario_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                        "scenario_id TEXT NOT NULL, package_sha256 TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0)")
                    self.store.execute(connection,
                        "INSERT INTO demo_scenario_identity (singleton, scenario_id, package_sha256) VALUES (1, ?, ?)",
                        (SCENARIO_ID, fingerprint))
            self.facts.migrate()
            with self.store.transaction(write_lock=True) as connection:
                ready = self.store.execute(connection, "SELECT published FROM demo_scenario_identity WHERE singleton=1").fetchone()[0]
                if not ready:
                    self._publish(connection, manifest, records, sources)
                    self.store.execute(connection, "UPDATE demo_scenario_identity SET published=1 WHERE singleton=1")
        return manifest

    def _load_package(self):
        manifest_bytes = (self.source_root / "manifest.json").read_bytes()
        manifest = ScenarioManifest.model_validate_json(manifest_bytes)
        types = {"items.json": ScenarioItem, "bom_facts.json": ScenarioBomLine,
                 "inventory_facts.json": ScenarioLot, "purchase_parameters.json": ScenarioPurchaseParameters,
                 "inbound_facts.json": ScenarioInbound}
        records, sources = {}, {}
        hashes = {"manifest.json": hashlib.sha256(manifest_bytes).hexdigest()}
        for filename, model in types.items():
            content = (self.source_root / filename).read_bytes()
            raw = json.loads(content)
            if not isinstance(raw, list) or not raw:
                raise ValueError("场景事实需要非空明细数组")
            records[filename] = [model.model_validate(row) for row in raw]
            sha = hashlib.sha256(content).hexdigest()
            sources[filename] = (sha, len(content))
            hashes[filename] = sha
        items = {row.material_code: row for row in records["items.json"]}
        if len(items) != len(records["items.json"]) or manifest.product not in items:
            raise ValueError("演示物料重复或产品不存在")
        if any(not row.material_code.startswith("DEMO-") or not row.name.strip() or not row.unit.strip() for row in items.values()):
            raise ValueError("只能加载明确 DEMO- 合成物料及单位")
        if not manifest.effective_from <= manifest.effective_date or not manifest.snapshot_at.date() < manifest.start_date:
            raise ValueError("演示日期范围不合法")
        seen = set()
        for row in records["bom_facts.json"]:
            key = row.parent_material_code, row.revision, row.child_material_code
            if key in seen or row.parent_material_code not in items or row.child_material_code not in items:
                raise ValueError("BOM行重复或物料未定义")
            seen.add(key)
            _number(row.qty_per, positive=True)
            if row.unit != items[row.child_material_code].unit:
                raise ValueError("BOM单位未明确匹配，禁止猜测换算")
        for row in records["inventory_facts.json"]:
            if row.material_code not in items:
                raise ValueError("库存料号未定义")
            for field in ("on_hand_qty", "available_qty", "reserved_qty"):
                _number(getattr(row, field))
        for row in records["purchase_parameters.json"]:
            if row.material_code not in items or row.unit != items[row.material_code].unit or row.lead_time_days < 0:
                raise ValueError("采购物料、单位或交期不合法")
            if not row.supplier_code.startswith("DEMO-") or not row.currency.strip():
                raise ValueError("演示供应商/币种需要显式定义")
            for field in ("safety_stock_qty", "minimum_order_quantity", "tax_included_unit_price"):
                _number(getattr(row, field))
            for field in ("order_multiple", "packaging_multiple"):
                value, applicable = getattr(row, field), getattr(row, field + "_applicable")
                if applicable != (value is not None):
                    raise ValueError("包装约束适用状态不明确")
                if value is not None:
                    _number(value, positive=True)
        for row in records["inbound_facts.json"]:
            if row.material_code not in items or not row.order_no.startswith("DEMO-") or not row.supplier_code.startswith("DEMO-"):
                raise ValueError("在途记录不是明确的合成来源")
            ordered, received, transit = (_number(getattr(row, field)) for field in ("ordered_qty", "received_qty", "in_transit_qty"))
            if ordered - received != transit:
                raise ValueError("在途来源数量审计校验失败")
        fingerprint = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
        return manifest, records, sources, fingerprint

    def _insert(self, connection, table: str, **values):
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        self.store.execute(connection, f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(values.values()))

    def _publish(self, connection, manifest, records, sources):
        # 表/字段均由本模块常量提供，外部数据只作为绑定参数。
        at = manifest.snapshot_at.isoformat()
        batches, files = {}, {}
        for filename, (sha, size) in sources.items():
            batch, source = _id(f"batch/{filename}"), _id(f"source/{filename}")
            batches[filename], files[filename] = batch, source
            self._insert(connection, "import_batches", id=batch, mapping_name=f"{SCENARIO_ID}/{filename}",
                         status="published", created_at=at, published_at=at, staged_row_count=len(records[filename]))
            self._insert(connection, "source_files", id=source, import_batch_id=batch, original_filename=filename,
                         content_sha256=sha, byte_size=size, source_kind="local",
                         source_uri=f"demo://{SCENARIO_ID}/{filename}", immutable_copy=False)

        def provenance(filename, sheet, row):
            return dict(import_batch_id=batches[filename], source_file_id=files[filename], source_sheet=sheet, source_row_number=row)

        for row, item in enumerate(records["items.json"], 1):
            self._insert(connection, "items", id=_id(f"item/{item.material_code}"), material_code=item.material_code,
                         name=item.name, specification="公开合成演示数据", unit=item.unit, **provenance("items.json", "items", row))
        headers = set()
        for row, line in enumerate(records["bom_facts.json"], 1):
            header = _id(f"bom/{line.parent_material_code}/{line.revision}")
            if header not in headers:
                self._insert(connection, "bom_headers", id=header, parent_material_code=line.parent_material_code,
                             version=line.revision, effective_from=manifest.effective_from.isoformat(),
                             import_batch_id=batches["bom_facts.json"], source_file_id=files["bom_facts.json"], source_sheet="bom_lines")
                headers.add(header)
            self._insert(connection, "bom_lines", id=_id(f"bom_line/{row}"), bom_header_id=header,
                         item_id=_id(f"item/{line.child_material_code}"), quantity=line.qty_per, unit=line.unit,
                         **provenance("bom_facts.json", "bom_lines", row))
        # 冻结 BOM 导入批次的物料名称/单位，不依赖未来主数据变化。
        children = {line.child_material_code for line in records["bom_facts.json"]}
        for row, item in enumerate(records["items.json"], 1):
            if item.material_code in children:
                self._insert(connection, "item_revisions", id=_id(f"item_revision/{item.material_code}"),
                             item_id=_id(f"item/{item.material_code}"), name=item.name, specification="公开合成演示数据", unit=item.unit,
                             import_batch_id=batches["bom_facts.json"], source_file_id=files["items.json"],
                             source_sheet="items", source_row_number=row)
        snapshot = _id("snapshot")
        self._insert(connection, "inventory_snapshots", id=snapshot, snapshot_at=at, observed_at=at,
                     import_batch_id=batches["inventory_facts.json"], source_file_id=files["inventory_facts.json"],
                     source_content_sha256=sources["inventory_facts.json"][0], source_sheet="inventory_lots", published_at=at)
        for row, lot in enumerate(records["inventory_facts.json"], 1):
            self._insert(connection, "inventory_facts", id=_id(f"lot/{row}"), inventory_snapshot_id=snapshot,
                         item_id=_id(f"item/{lot.material_code}"), raw_material_code=lot.material_code,
                         site="DEMO-SITE", warehouse="DEMO-WAREHOUSE", location=lot.location, batch_no=lot.batch_no,
                         on_hand_qty=lot.on_hand_qty, available_qty=lot.available_qty, reserved_qty=lot.reserved_qty,
                         quality_status="released", received_at=lot.received_at.isoformat(), expiry_date=lot.expiry_date.isoformat(),
                         **provenance("inventory_facts.json", "inventory_lots", row))
        for row, params in enumerate(records["purchase_parameters.json"], 1):
            item, price = _id(f"item/{params.material_code}"), _id(f"price/{row}")
            self._insert(connection, "inventory_risk_policies", id=_id(f"safety/{row}"), item_id=item,
                         safety_stock_qty=params.safety_stock_qty, unit=params.unit, effective_from=manifest.effective_from.isoformat(),
                         **provenance("purchase_parameters.json", "safety_stock", row))
            self._insert(connection, "price_records", id=price, item_id=item, tax_included_unit_price=params.tax_included_unit_price,
                         currency=params.currency, unit=params.unit, supplier_code=params.supplier_code,
                         effective_from=manifest.effective_from.isoformat(), **provenance("purchase_parameters.json", "prices", row))
            self._insert(connection, "purchase_parameter_facts", id=_id(f"parameters/{row}"), item_id=item,
                         supplier_code=params.supplier_code, purchase_unit=params.unit, minimum_order_quantity=params.minimum_order_quantity,
                         order_multiple=params.order_multiple, packaging_multiple=params.packaging_multiple,
                         order_multiple_applicable=params.order_multiple_applicable, packaging_multiple_applicable=params.packaging_multiple_applicable,
                         lead_time_days=params.lead_time_days, price_record_id=price, effective_from=manifest.effective_from.isoformat(),
                         **provenance("purchase_parameters.json", "purchase_parameters", row))
        observation = _id("inbound_observation")
        self._insert(connection, "inbound_supply_observations", id=observation, observed_at=at,
                     import_batch_id=batches["inbound_facts.json"], source_file_id=files["inbound_facts.json"],
                     source_content_sha256=sources["inbound_facts.json"][0], source_name=f"{SCENARIO_ID}/closed_orders", published_at=at)
        for row, order in enumerate(records["inbound_facts.json"], 1):
            self._insert(connection, "inbound_supply_facts", id=_id(f"inbound/{row}"), inbound_observation_id=observation,
                         item_id=_id(f"item/{order.material_code}"), raw_material_code=order.material_code,
                         supplier_code=order.supplier_code, order_no=order.order_no, ordered_qty=order.ordered_qty,
                         received_qty=order.received_qty, in_transit_qty=order.in_transit_qty,
                         expected_arrival_date=order.expected_arrival_date.isoformat(), supply_status=order.status,
                         **provenance("inbound_facts.json", "closed_orders", row))
