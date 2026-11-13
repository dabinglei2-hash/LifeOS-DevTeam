"""Database ownership checks, migrations, and exact schema validation."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import struct
import subprocess
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine, event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import StaticPool

from .models import Base
from .safety import KnowledgeError, utc_now_iso


APPLICATION_ID = 0x4C414453
HEAD_REVISION = "0002"
SCHEMA_REVISIONS = frozenset({"0001", "0002"})
RELATIONAL_TABLES = {
    "projects",
    "dev_tasks",
    "task_events",
    "agent_runs",
    "code_changes",
    "test_runs",
    "architecture_decisions",
    "known_issues",
    "knowledge_items",
    "model_usage",
}
FTS_TABLES = {
    "knowledge_fts",
    "knowledge_fts_data",
    "knowledge_fts_idx",
    "knowledge_fts_content",
    "knowledge_fts_docsize",
    "knowledge_fts_config",
}
FTS_TRIGGERS = {"knowledge_fts_ai", "knowledge_fts_au", "knowledge_fts_ad"}
FTS_TRIGGER_SQL = {
    "knowledge_fts_ai": """
        CREATE TRIGGER knowledge_fts_ai
        AFTER INSERT ON knowledge_items
        WHEN NEW.status = 'active'
        BEGIN
            INSERT INTO knowledge_fts(
                knowledge_id, project_id, title, body, source_ref
            ) VALUES (
                NEW.id, NEW.project_id, NEW.title, NEW.body, NEW.source_ref
            );
        END
    """,
    "knowledge_fts_au": """
        CREATE TRIGGER knowledge_fts_au
        AFTER UPDATE ON knowledge_items
        BEGIN
            DELETE FROM knowledge_fts WHERE knowledge_id = OLD.id;
            INSERT INTO knowledge_fts(
                knowledge_id, project_id, title, body, source_ref
            )
            SELECT NEW.id, NEW.project_id, NEW.title, NEW.body, NEW.source_ref
            WHERE NEW.status = 'active';
        END
    """,
    "knowledge_fts_ad": """
        CREATE TRIGGER knowledge_fts_ad
        AFTER DELETE ON knowledge_items
        BEGIN
            DELETE FROM knowledge_fts WHERE knowledge_id = OLD.id;
        END
    """,
}
ALIAS_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}\.sqlite3$")
MARKER_NAME = ".lifeos-lads-knowledge-owner.json"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class DatabaseBoundaryError(KnowledgeError):
    """Raised before a foreign or unsafe database can be opened."""


class SchemaValidationError(DatabaseBoundaryError):
    """Raised when a LADS-family database differs from its frozen schema."""


def default_home() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "LifeOS-LADS" / "knowledge"
    return Path.home() / ".local" / "share" / "lifeos-lads" / "knowledge"


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _reject_reparse_chain(path: Path) -> None:
    current = path
    while True:
        if _is_reparse(current):
            raise DatabaseBoundaryError("Reparse points are not allowed in knowledge paths")
        if current.parent == current:
            return
        current = current.parent


def validate_home(home: str | os.PathLike[str] | None = None) -> Path:
    selected = Path(home) if home is not None else default_home()
    if not selected.is_absolute():
        raise DatabaseBoundaryError("Knowledge home must be an absolute path")
    _reject_reparse_chain(selected)
    resolved = selected.resolve(strict=False)
    git_roots = [ancestor for ancestor in (resolved, *resolved.parents) if (ancestor / ".git").exists()]
    private_base = default_home().parent.resolve(strict=False)
    # Some machines version their entire user home. Only our fixed private
    # namespace may coexist with that broad ancestor, with deny-all Git exclusion
    # checked below. Actual project/nested Git worktrees are always rejected.
    if git_roots and not (resolved.is_relative_to(private_base) and all(item == Path.home().resolve() for item in git_roots)):
        raise DatabaseBoundaryError("Knowledge home must be outside project Git worktrees")
    repository = REPOSITORY_ROOT.resolve(strict=True)
    if resolved == repository or repository in resolved.parents:
        raise DatabaseBoundaryError("Knowledge home must be outside the repository")
    lowered_parts = {part.casefold() for part in resolved.parts}
    if lowered_parts & {"static", "notion", "notion-bridge", "notes-bridge"}:
        raise DatabaseBoundaryError("Knowledge home overlaps a forbidden content root")
    return resolved


def validate_alias(alias: str) -> str:
    value = str(alias).strip().lower()
    if not ALIAS_RE.fullmatch(value):
        raise DatabaseBoundaryError("Database alias is not controlled")
    return value


def database_path(home: Path, alias: str) -> Path:
    return home / validate_alias(alias)


def _assert_sidecars_safe(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists() and _is_reparse(sidecar):
            raise DatabaseBoundaryError("SQLite sidecar is a reparse point")


def _assert_no_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        if Path(f"{path}{suffix}").exists():
            raise DatabaseBoundaryError("Orphan SQLite sidecars are not accepted")


def read_application_id(path: Path) -> int:
    try:
        size = path.stat().st_size
    except FileNotFoundError as exc:
        raise DatabaseBoundaryError("Database file is missing") from exc
    if size == 0:
        raise DatabaseBoundaryError("Zero-byte databases are not accepted")
    if size < 100:
        raise DatabaseBoundaryError("Short or corrupt SQLite file")
    with path.open("rb") as handle:
        header = handle.read(100)
    if not header.startswith(b"SQLite format 3\x00"):
        raise DatabaseBoundaryError("File is not SQLite")
    return struct.unpack(">I", header[68:72])[0]


def _ensure_marker(home: Path, *, allow_create: bool) -> None:
    marker = home / MARKER_NAME
    if marker.exists():
        if _is_reparse(marker):
            raise DatabaseBoundaryError("Knowledge ownership marker is a reparse point")
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DatabaseBoundaryError("Knowledge ownership marker is unreadable") from exc
        if payload.get("application_id") != APPLICATION_ID:
            raise DatabaseBoundaryError("Knowledge ownership marker belongs to another app")
        _ensure_git_exclusion(home, allow_create=allow_create)
        return
    if home.exists() and any(home.iterdir()):
        raise DatabaseBoundaryError("Nonempty knowledge home is unmarked")
    if not allow_create:
        raise DatabaseBoundaryError("Knowledge home is not initialized")
    home.mkdir(parents=True, exist_ok=True)
    try:
        home.chmod(0o700)
    except OSError:
        pass
    payload = {
        "application_id": APPLICATION_ID,
        "created_at": utc_now_iso(),
        "format": "lads-knowledge-home-v1",
    }
    marker.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    try:
        marker.chmod(0o600)
    except OSError:
        pass
    _ensure_git_exclusion(home, allow_create=True)


def _ensure_git_exclusion(home: Path, *, allow_create: bool) -> None:
    """Owned metadata only: exclude every engineering DB/backup, never global Git config."""
    ignore = home / ".gitignore"
    if not ignore.exists():
        if not allow_create:
            raise DatabaseBoundaryError("Open the owned store writable once to initialize Git exclusion")
        with ignore.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write("*\n")
    if _is_reparse(ignore) or ignore.read_bytes() != b"*\n":
        raise DatabaseBoundaryError("Private namespace requires its owned deny-all Git exclusion")
    git_roots = [item for item in (home, *home.parents) if (item / ".git").exists()]
    for repository in git_roots:
        prefix = ["git", "-c", "core.fsmonitor=false", "-C", str(repository)]
        try:
            tracked = subprocess.run([*prefix, "ls-files", "--error-unmatch", "--", str(home)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            excluded = subprocess.run([*prefix, "check-ignore", "--no-index", "-q", "--", str(home / "lads-private-sentinel.sqlite3")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DatabaseBoundaryError("Cannot verify private namespace Git exclusion") from exc
        if tracked.returncode != 1 or excluded.returncode != 0:
            raise DatabaseBoundaryError("Private namespace is tracked or not excluded from Git")


def _assert_private_namespace(home: Path) -> None:
    _reject_reparse_chain(home)
    if not home.is_dir():
        raise DatabaseBoundaryError("Knowledge home is not a directory")
    home_stat = home.stat()
    mode = stat.S_IMODE(home_stat.st_mode)
    if os.name != "nt":
        if home_stat.st_uid != os.getuid():
            raise DatabaseBoundaryError("Knowledge home must be owned by this process user")
        if mode & 0o077:
            raise DatabaseBoundaryError("Knowledge home must be private to its owner")
    _ensure_marker(home, allow_create=False)


def _rows(connection: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if hasattr(connection, "exec_driver_sql"):
        result = connection.exec_driver_sql(sql, params)
        return [dict(row) for row in result.mappings()]
    cursor = connection.execute(sql, params)
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _scalar(connection: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    rows = _rows(connection, sql, params)
    if not rows:
        return None
    return next(iter(rows[0].values()))


def _execute(connection: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    if hasattr(connection, "exec_driver_sql"):
        return connection.exec_driver_sql(sql, params)
    return connection.execute(sql, params)


def _quoted(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", identifier):
        raise SchemaValidationError("Unsafe schema identifier")
    return f'"{identifier}"'


def _normalized_sql(value: str | None) -> str:
    return re.sub(
        r"\s+", " ", (value or "").replace('"', "").replace("`", "")
    ).strip().lower()


@lru_cache(maxsize=len(SCHEMA_REVISIONS))
def _frozen_table_sql(revision: str) -> tuple[tuple[str, str], ...]:
    """Build immutable expected table SQL from frozen migrations, not validation."""

    if revision not in SCHEMA_REVISIONS:
        raise SchemaValidationError("Unknown LADS schema revision")
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        with engine.connect() as connection:
            command.upgrade(_alembic_config(connection), revision)
            rows = _rows(
                connection,
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name",
            )
            return tuple(
                (str(row["name"]), _normalized_sql(row["sql"])) for row in rows
            )
    finally:
        engine.dispose()


def _fts_multiset(
    connection: Any,
) -> tuple[Counter[tuple[Any, ...]], Counter[tuple[Any, ...]]]:
    expected_rows = _rows(
        connection,
        "SELECT id AS knowledge_id, project_id, title, body, source_ref "
        "FROM knowledge_items WHERE status='active'",
    )
    indexed_rows = _rows(
        connection,
        "SELECT knowledge_id, project_id, title, body, source_ref FROM knowledge_fts",
    )
    keys = ("knowledge_id", "project_id", "title", "body", "source_ref")
    expected = Counter(tuple(row[key] for key in keys) for row in expected_rows)
    indexed = Counter(tuple(row[key] for key in keys) for row in indexed_rows)
    return expected, indexed


def _check_fts_internal_integrity(connection: Any) -> None:
    """Run FTS5's check when the connection permits it, without persisting writes."""

    if _scalar(connection, "PRAGMA query_only"):
        return
    savepoint = "lads_knowledge_fts_integrity"
    _execute(connection, f"SAVEPOINT {savepoint}")
    try:
        _execute(
            connection,
            "INSERT INTO knowledge_fts(knowledge_fts) VALUES('integrity-check')"
        )
    except Exception as exc:
        _execute(connection, f"ROLLBACK TO {savepoint}")
        _execute(connection, f"RELEASE {savepoint}")
        raise SchemaValidationError("FTS internal integrity check failed") from exc
    _execute(connection, f"ROLLBACK TO {savepoint}")
    _execute(connection, f"RELEASE {savepoint}")


def _expected_fks() -> dict[str, set[tuple[str, str, str, str]]]:
    expected: dict[str, set[tuple[str, str, str, str]]] = {}
    for table in Base.metadata.sorted_tables:
        items: set[tuple[str, str, str, str]] = set()
        for fk in table.foreign_key_constraints:
            for element in fk.elements:
                items.add(
                    (
                        element.parent.name,
                        element.target_fullname.rsplit(".", 1)[-1],
                        fk.referred_table.name,
                        (fk.ondelete or "").upper(),
                    )
                )
        expected[table.name] = items
    return expected


def _expected_columns() -> dict[str, list[dict[str, Any]]]:
    return {
        table.name: [
            {
                "name": column.name,
                "type": str(column.type).replace("VARCHAR", "VARCHAR"),
                "notnull": bool(column.nullable is False),
                "pk": bool(column.primary_key),
            }
            for column in table.columns
        ]
        for table in Base.metadata.sorted_tables
    }


def _expected_unique_constraints() -> dict[str, set[tuple[str, ...]]]:
    return {
        table.name: {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        for table in Base.metadata.sorted_tables
    }


def _expected_indexes() -> dict[str, dict[str, tuple[bool, tuple[str, ...]]]]:
    return {
        table.name: {
            index.name: (
                bool(index.unique),
                tuple(column.name for column in index.columns),
            )
            for index in table.indexes
            if index.name
        }
        for table in Base.metadata.sorted_tables
    }


def validate_schema(
    connection: Any,
    *,
    expected_revision: str | None = None,
    integrity: bool = True,
) -> str:
    """Validate an already identified LADS database without modifying it."""

    version_rows = _rows(
        connection,
        "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'",
    )
    if not version_rows:
        raise SchemaValidationError("Alembic revision table is missing")
    revision = _scalar(connection, "SELECT version_num FROM alembic_version")
    if revision not in SCHEMA_REVISIONS:
        raise SchemaValidationError("Unknown LADS schema revision")
    if expected_revision is not None and revision != expected_revision:
        raise SchemaValidationError("Unexpected LADS schema revision")

    app_id = _scalar(connection, "PRAGMA application_id")
    if app_id != APPLICATION_ID:
        raise SchemaValidationError("SQLite application id does not match LADS")
    if _scalar(connection, "PRAGMA foreign_keys") not in (1, True):
        raise SchemaValidationError("Foreign keys must be enabled for validation")

    master = _rows(
        connection,
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%'",
    )
    table_names = {row["name"] for row in master if row["type"] == "table"}
    expected_table_sql = dict(_frozen_table_sql(str(revision)))
    expected_tables = RELATIONAL_TABLES | {"alembic_version"}
    if revision == "0002":
        expected_tables |= FTS_TABLES
    if set(expected_table_sql) != expected_tables:
        raise SchemaValidationError("Frozen schema table set is invalid")
    if table_names != expected_tables:
        raise SchemaValidationError("Database table set differs from the frozen schema")

    expected_columns = _expected_columns()
    for table_name, columns in expected_columns.items():
        actual = _rows(connection, f"PRAGMA table_info({_quoted(table_name)})")
        actual_signature = [
            {
                "name": row["name"],
                "type": row["type"].upper(),
                "notnull": bool(row["notnull"]),
                "pk": bool(row["pk"]),
            }
            for row in actual
        ]
        expected_signature = [
            {
                "name": row["name"],
                "type": row["type"].upper(),
                "notnull": row["notnull"],
                "pk": row["pk"],
            }
            for row in columns
        ]
        if actual_signature != expected_signature:
            raise SchemaValidationError(f"Column definition mismatch in {table_name}")

    expected_fks = _expected_fks()
    for table_name, expected in expected_fks.items():
        rows = _rows(connection, f"PRAGMA foreign_key_list({_quoted(table_name)})")
        actual = {
            (
                row["from"],
                row["to"],
                row["table"],
                (row["on_delete"] or "").upper(),
            )
            for row in rows
        }
        expected_upper = {
            (source, target, table, on_delete.upper())
            for source, target, table, on_delete in expected
        }
        if actual != expected_upper:
            raise SchemaValidationError(f"Foreign key mismatch in {table_name}")

    expected_unique = _expected_unique_constraints()
    for table_name, expected in expected_unique.items():
        actual: set[tuple[str, ...]] = set()
        for index in _rows(connection, f"PRAGMA index_list({_quoted(table_name)})"):
            if index.get("origin") != "u" and not index.get("unique"):
                continue
            if index.get("origin") == "pk":
                continue
            index_name = str(index["name"]).replace("'", "''")
            columns = tuple(
                row["name"]
                for row in _rows(
                    connection, f"PRAGMA index_info('{index_name}')"
                )
            )
            if index.get("unique"):
                actual.add(columns)
        if actual != expected:
            raise SchemaValidationError(f"Unique constraint mismatch in {table_name}")

    expected_indexes = _expected_indexes()
    for table_name, expected in expected_indexes.items():
        actual: dict[str, tuple[bool, tuple[str, ...]]] = {}
        for row in _rows(connection, f"PRAGMA index_list({_quoted(table_name)})"):
            if row.get("origin") != "c":
                continue
            index_name = str(row["name"])
            index_columns = sorted(
                _rows(connection, f"PRAGMA index_info({_quoted(index_name)})"),
                key=lambda item: item["seqno"],
            )
            actual[index_name] = (
                bool(row.get("unique")),
                tuple(str(item["name"]) for item in index_columns),
            )
        if expected != actual:
            raise SchemaValidationError(f"Explicit index mismatch in {table_name}")

    sql_by_table = {
        row["name"]: _normalized_sql(row["sql"])
        for row in master
        if row["type"] == "table"
    }
    for table_name, expected_sql in expected_table_sql.items():
        if sql_by_table[table_name] != expected_sql:
            raise SchemaValidationError(
                f"Table schema differs from frozen revision in {table_name}"
            )

    for table in Base.metadata.sorted_tables:
        sql = sql_by_table[table.name]
        for fk in table.foreign_key_constraints:
            if fk.name and fk.name.casefold() not in sql:
                raise SchemaValidationError(f"Foreign key constraint {fk.name} is missing")

    if revision == "0002":
        if not _scalar(connection, "SELECT sqlite_compileoption_used('ENABLE_FTS5')"):
            raise SchemaValidationError("SQLite FTS5 support is unavailable")
        fts_sql = sql_by_table.get("knowledge_fts", "")
        if "using fts5" not in fts_sql or "tokenize='trigram'" not in fts_sql:
            raise SchemaValidationError("FTS5 trigram schema is invalid")
        trigger_sql = {
            row["name"]: row["sql"] for row in master if row["type"] == "trigger"
        }
        if set(trigger_sql) != FTS_TRIGGERS:
            raise SchemaValidationError("FTS maintenance trigger set is invalid")
        for trigger_name, expected_sql in FTS_TRIGGER_SQL.items():
            if _normalized_sql(trigger_sql[trigger_name]) != _normalized_sql(
                expected_sql
            ):
                raise SchemaValidationError(
                    f"FTS maintenance trigger {trigger_name} is invalid"
                )
        expected_fts, indexed_fts = _fts_multiset(connection)
        expected_count = sum(expected_fts.values())
        indexed_count = sum(indexed_fts.values())
        if expected_count != indexed_count or expected_fts != indexed_fts:
            raise SchemaValidationError(
                "FTS content multiset differs from active knowledge items"
            )
        _check_fts_internal_integrity(connection)

    if integrity:
        integrity_result = _scalar(connection, "PRAGMA integrity_check")
        if integrity_result != "ok":
            raise SchemaValidationError("SQLite integrity check failed")
        if _rows(connection, "PRAGMA foreign_key_check"):
            raise SchemaValidationError("SQLite foreign key check failed")
    return str(revision)


def _alembic_config(connection: Connection) -> Config:
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parent / "migrations")
    )
    config.attributes["connection"] = connection
    return config


def upgrade_connection(connection: Connection, revision: str) -> None:
    config = _alembic_config(connection)
    command.upgrade(config, revision)


def downgrade_connection(connection: Connection, revision: str) -> None:
    config = _alembic_config(connection)
    command.downgrade(config, revision)


def _upgrade_to_head(connection: Connection) -> None:
    command.upgrade(_alembic_config(connection), "head")


def _open_readonly(path: Path) -> sqlite3.Connection:
    uri = path.resolve(strict=True).as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def inspect_file_readonly(path: Path) -> str:
    if _is_reparse(path):
        raise DatabaseBoundaryError("Database file is a reparse point")
    _assert_sidecars_safe(path)
    if read_application_id(path) != APPLICATION_ID:
        raise DatabaseBoundaryError("Database application id is not LADS")
    with _open_readonly(path) as connection:
        return validate_schema(connection, integrity=True)


def _configure_engine(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 5000")
        cursor.close()


def create_memory_engine(*, migrate: bool = True) -> Engine:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _configure_engine(engine)
    if migrate:
        with engine.connect() as connection:
            _upgrade_to_head(connection)
            connection.commit()
    return engine


@dataclass(slots=True)
class OpenedFile:
    home: Path
    alias: str
    path: Path
    engine: Engine


class FileStore:
    """Explicit file-store initializer. Read-only readers use a separate class."""

    @classmethod
    def open(
        cls,
        home: str | os.PathLike[str] | None = None,
        alias: str = "knowledge.sqlite3",
    ) -> Any:
        selected_home = validate_home(home)
        controlled_alias = validate_alias(alias)
        _ensure_marker(selected_home, allow_create=True)
        _assert_private_namespace(selected_home)
        path = database_path(selected_home, controlled_alias)
        was_existing = path.exists()
        if was_existing:
            if _is_reparse(path):
                raise DatabaseBoundaryError("Database file is a reparse point")
            _assert_sidecars_safe(path)
            if read_application_id(path) != APPLICATION_ID:
                raise DatabaseBoundaryError("Existing database belongs to another app")
            inspect_file_readonly(path)
            created_identity = None
        else:
            _assert_no_sidecars(path)
            flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
            try:
                descriptor = os.open(path, flags, 0o600)
            except FileExistsError as exc:
                raise DatabaseBoundaryError(
                    "Database path appeared concurrently; explicit open must be retried"
                ) from exc
            else:
                os.close(descriptor)
            created_stat = path.stat()
            created_identity = (created_stat.st_dev, created_stat.st_ino)

        engine = create_engine(URL.create("sqlite+pysqlite", database=str(path)))
        _configure_engine(engine)
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                if path.stat().st_size:
                    validate_schema(connection, integrity=True)
                _upgrade_to_head(connection)
                validate_schema(connection, expected_revision=HEAD_REVISION)
                connection.commit()
            path.chmod(0o600)
        except Exception:
            engine.dispose()
            if created_identity is not None and path.exists():
                current = path.stat()
                if (current.st_dev, current.st_ino) == created_identity:
                    path.unlink(missing_ok=True)
            raise
        opened = OpenedFile(selected_home, controlled_alias, path, engine)
        from .service import KnowledgeStore

        return KnowledgeStore._from_open_file(opened)
