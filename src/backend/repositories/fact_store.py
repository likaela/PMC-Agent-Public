"""受控事实层的持久化连接抽象。

生产环境通过 ``PMC_FACT_DATABASE_URL=postgresql://...`` 使用 PostgreSQL；
测试和本地开发可使用 ``sqlite:///...``。PostgreSQL 驱动按需导入，因此 SQLite
测试不需要运行中的 PostgreSQL 服务。
"""

from __future__ import annotations

import os
import re
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_ROOT = Path(__file__).resolve().parents[1] / "migrations"
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "data" / "facts" / "pmc_facts.db"


class PersistentFactStore(ABC):
    """数据库方言无关的最小 DB-API 包装。"""

    dialect: str
    placeholder: str

    @abstractmethod
    def connect(self) -> Any:
        """返回一个新的 DB-API 连接。"""

    def adapt_query(self, query: str) -> str:
        if self.placeholder == "?":
            return query
        return query.replace("?", self.placeholder)

    def execute(self, connection: Any, query: str, params: Sequence[object] = ()) -> Any:
        return connection.execute(self.adapt_query(query), tuple(params))

    @contextmanager
    def transaction(self, *, write_lock: bool = False) -> Iterator[Any]:
        connection = self.connect()
        try:
            self.begin_transaction(connection, write_lock=write_lock)
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def begin_transaction(self, connection: Any, *, write_lock: bool) -> None:
        """允许方言在关键写事务开始时提前获得锁。"""

    def _split_migration(self, sql: str) -> list[str]:
        return [statement.strip() for statement in sql.split("-- statement-break") if statement.strip()]

    @contextmanager
    def migration_transaction(self) -> Iterator[Any]:
        """Execute migrations atomically; dialects may enable safe table rebuilds."""
        with self.transaction(write_lock=True) as connection:
            yield connection

    def migrate(self) -> list[str]:
        """按文件名顺序应用本方言尚未执行的迁移。"""
        migration_dir = MIGRATIONS_ROOT / self.dialect
        if not migration_dir.is_dir():
            raise RuntimeError(f"缺少 {self.dialect} 事实层迁移目录")

        applied: list[str] = []
        with self.migration_transaction() as connection:
            applied_at_type = "TIMESTAMPTZ" if self.dialect == "postgresql" else "TEXT"
            self.execute(
                connection,
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                f"(version TEXT PRIMARY KEY, applied_at {applied_at_type} NOT NULL)",
            )
            existing_cursor = self.execute(connection, "SELECT version FROM schema_migrations")
            existing = {str(row[0]) for row in existing_cursor.fetchall()}
            for migration_path in sorted(migration_dir.glob("*.sql")):
                version = migration_path.stem
                if version in existing:
                    continue
                migration_sql = migration_path.read_text(encoding="utf-8")
                for statement in self._split_migration(migration_sql):
                    self.execute(connection, statement)
                self.execute(
                    connection,
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(UTC).isoformat()),
                )
                applied.append(version)
        return applied


class SQLiteFactStore(PersistentFactStore):
    dialect = "sqlite"
    placeholder = "?"

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if self.database_path == ":memory:":
            raise ValueError(
                "SQLite :memory: 不适用于多连接事实存储；请使用临时文件数据库"
            )
        Path(self.database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def begin_transaction(self, connection: sqlite3.Connection, *, write_lock: bool) -> None:
        if write_lock:
            connection.execute("BEGIN IMMEDIATE")

    @contextmanager
    def migration_transaction(self) -> Iterator[sqlite3.Connection]:
        """SQLite rebuilds must disable FK enforcement before starting a transaction.

        All migrated facts are checked before commit. Ordinary connections continue
        to enforce foreign keys, and a failed rebuild rolls back the entire migration.
        """
        connection = self.connect()
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError("事实层迁移外键校验失败，已回滚")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.close()


class PostgreSQLFactStore(PersistentFactStore):
    dialect = "postgresql"
    placeholder = "%s"

    def __init__(self, database_url: str, *, schema: str | None = None) -> None:
        self.database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        if schema is not None and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema) is None:
            raise ValueError("PostgreSQL schema 名称不合法")
        self.schema = schema

    def connect(self) -> Any:
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError(
                "PostgreSQL 事实存储需要安装 requirements.txt 中的 psycopg 驱动"
            ) from error
        connection = psycopg.connect(self.database_url)
        if self.schema:
            connection.execute(f'SET search_path TO "{self.schema}"')
        return connection


def create_fact_store(database_url: str | None = None) -> PersistentFactStore:
    """根据 URL 创建事实存储；未配置时仅为本地开发选择 SQLite。"""
    if os.getenv("PMC_TRIAL_MODE") == "1":
        raise RuntimeError("Remote trial cannot open an enterprise fact store")
    configured_url = database_url or os.getenv(
        "PMC_FACT_DATABASE_URL",
        f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}",
    )
    if configured_url.startswith("sqlite:///"):
        database_path = configured_url.removeprefix("sqlite:///")
        return SQLiteFactStore(database_path)
    if configured_url.startswith(("postgresql://", "postgresql+psycopg://")):
        return PostgreSQLFactStore(configured_url)
    raise ValueError("PMC_FACT_DATABASE_URL 仅支持 sqlite:/// 或 postgresql://")


__all__ = [
    "DEFAULT_SQLITE_PATH",
    "PersistentFactStore",
    "PostgreSQLFactStore",
    "SQLiteFactStore",
    "create_fact_store",
]
