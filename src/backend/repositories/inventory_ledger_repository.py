"""Persistent inventory balances; immutable released files are seed inputs only.

Unknown and ambiguous observations remain queryable, but are never converted to
zero or executable stock. Decimal arithmetic is performed by Python under SQLite
write transactions rather than by SQLite floating point SUM/subtraction.
"""
from __future__ import annotations

import copy
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def quantity(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() and result >= 0 else None


def text_number(value):
    return format(value, "f") if value is not None else None


class InventoryLedgerRepository:
    def __init__(self, path):
        self.path = Path(path).resolve()
        if any(a.name == "public" and a.parent.name == "data" for a in self.path.parents):
            raise ValueError("Runtime inventory cannot be written into released public inputs")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        schema = Path(__file__).resolve().parents[1] / "migrations/inventory_ledger/001_initial.sql"
        with closing(self.connect()) as connection:
            connection.executescript(schema.read_text(encoding="utf-8"))

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def seed(self, namespace, observations, *, quantity_basis="published_available"):
        """Seed once, refusing a silent reset on a different release or basis.

        confirmed_actual_stock is reserved for the already source-confirmed book
        inventory. The confirmation is retained, not generalized into reserved=0.
        """
        if not namespace or quantity_basis not in ("published_available", "confirmed_actual_stock"):
            raise ValueError("Invalid inventory namespace or quantity basis")
        if quantity_basis == "confirmed_actual_stock" and not observations.get("quantity_confirmation"):
            raise ValueError("Actual-stock seeding requires the source quantity confirmation")
        source = copy.deepcopy(observations)
        serialized = json_text(source)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        with self.transaction() as connection:
            previous = connection.execute("SELECT * FROM ledger_namespaces WHERE namespace=?", (namespace,)).fetchone()
            if previous:
                if previous["seed_hash"] != digest or previous["quantity_basis"] != quantity_basis:
                    raise ValueError("Inventory seed changed; reconcile explicitly without resetting balances")
                return
            connection.execute("INSERT INTO ledger_namespaces VALUES (?,?,?,?,0,?)",
                (namespace, digest, serialized, quantity_basis, datetime.now(UTC).isoformat()))
            for code, rows in source["records"].items():
                for ordinal, row in enumerate(rows):
                    on_hand = quantity(row.get("on_hand_qty", row.get("recorded_stock_qty")))
                    available = quantity(row.get("available_qty"))
                    if quantity_basis == "confirmed_actual_stock":
                        available = quantity(row.get("recorded_stock_qty"))
                    reserved = quantity(row.get("reserved_qty"))
                    if on_hand is None or available is not None and available > on_hand or (
                        reserved is not None and (reserved > on_hand or available is not None and available > on_hand - reserved)
                    ):
                        available = None
                    if row.get("duplicate_observation") or row.get("is_duplicate"):
                        available = None
                    lot_id = hashlib.sha256(f"{namespace}\0{code}\0{ordinal}\0{json_text(row)}".encode()).hexdigest()
                    connection.execute("INSERT INTO ledger_lots VALUES (?,?,?,?,?,?,?)", (
                        namespace, lot_id, code, ordinal, text_number(on_hand), text_number(available), json_text(row)))

    def _namespace(self, connection, namespace):
        result = connection.execute("SELECT * FROM ledger_namespaces WHERE namespace=?", (namespace,)).fetchone()
        if not result:
            raise ValueError("Inventory namespace has not been initialized")
        if hashlib.sha256(result["source_json"].encode()).hexdigest() != result["seed_hash"]:
            raise ValueError("Inventory source identity is damaged; balances were not reset")
        return result

    def version(self, namespace):
        with closing(self.connect()) as connection:
            return self._namespace(connection, namespace)["version"]

    def _lots(self, connection, namespace, code=None):
        query = "SELECT * FROM ledger_lots WHERE namespace=?"
        args = [namespace]
        if code is not None:
            query += " AND material_code=?"
            args.append(code)
        return connection.execute(query + " ORDER BY material_code, ordinal", args).fetchall()

    def load_observations(self, namespace, *, connection=None):
        if connection is None:
            with closing(self.connect()) as handle:
                handle.execute("BEGIN")
                return self.load_observations(namespace, connection=handle)
        metadata = self._namespace(connection, namespace)
        result = json.loads(metadata["source_json"])
        for lot in self._lots(connection, namespace):
            row = result["records"][lot["material_code"]][lot["ordinal"]]
            row["recorded_stock_qty"] = lot["on_hand_qty"]
            row["on_hand_qty"] = lot["on_hand_qty"]
            row["available_qty"] = lot["available_qty"]
            row["ledger_lot_id"] = lot["lot_id"]
            row["provenance"] = {**row.get("provenance", {}), "runtime_ledger": {
                "namespace": namespace, "version": metadata["version"], "lot_id": lot["lot_id"],
                "quantity_basis": metadata["quantity_basis"], "seed_hash": metadata["seed_hash"]}}
        result["ledger_version"] = metadata["version"]
        result["ledger_namespace"] = namespace
        return result

    def stock(self, namespace, code, *, connection=None):
        if connection is None:
            with closing(self.connect()) as handle:
                handle.execute("BEGIN")
                return self.stock(namespace, code, connection=handle)
        source = self.load_observations(namespace, connection=connection)
        rows = source["records"].get(code, [])
        units = {row.get("inventory_unit", row.get("unit")) for row in rows}
        unit = next(iter(units)) if len(units) == 1 else None
        identities = [tuple(r.get(key) for key in ("material_code", "batch_no", "location", "site", "warehouse")) for r in rows]
        duplicate_free = (not any(r.get("duplicate_observation") or r.get("is_duplicate") for r in rows)
                          and len(identities) == len(set(identities)))
        totals = {}
        for field in ("on_hand_qty", "available_qty"):
            values = [quantity(r.get(field)) for r in rows]
            total = sum(values, Decimal(0)) if rows and duplicate_free and unit and all(v is not None for v in values) else None
            totals[field] = text_number(total)
        return dict(material_code=code, name=next((r.get("name") for r in rows if r.get("name")), None),
            **totals, recorded_stock_qty=totals["on_hand_qty"], unit=unit, inventory_unit=unit,
            batches=rows, details=rows, record_found=bool(rows), duplicate_free=duplicate_free,
            version=source["ledger_version"], provenance=[r.get("provenance", {}) for r in rows])

    def stocks(self, namespace):
        with closing(self.connect()) as connection:
            connection.execute("BEGIN")
            codes = connection.execute("SELECT DISTINCT material_code FROM ledger_lots WHERE namespace=? ORDER BY material_code", (namespace,))
            return [self.stock(namespace, row[0], connection=connection) for row in codes]

    def movements(self, namespace):
        with closing(self.connect()) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM ledger_movements WHERE namespace=? ORDER BY id", (namespace,))]


class LedgerObservations:
    def __init__(self, repository, namespace):
        self.repository, self.namespace = repository, namespace

    def load(self):
        return self.repository.load_observations(self.namespace)
