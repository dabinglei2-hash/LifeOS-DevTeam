"""Private engineering telemetry, schema 1. Never opens a product database."""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, event, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.pool import NullPool

SCHEMA = 1
APP_ID = 0x4C434331
ROLES = {"gpt-controller", "scanner", "reviewer", "builder", "qa-tester"}
PROJECTS = {"lifeos", "devteam"}
EVENTS = {"AGENT_STARTED", "AGENT_HEARTBEAT", "AGENT_TASK_STARTED", "AGENT_TASK_COMPLETED",
          "AGENT_TASK_FAILED", "AGENT_STOPPED", "AGENT_ERROR", "TASK_EVENT", "MODEL_USAGE"}
SAFE_PAYLOAD = {"state", "role", "phase", "status", "exit_code", "timed_out", "error_code",
                "input_tokens", "output_tokens", "cached_tokens", "request_id", "usage_source"}
metadata = MetaData()
events = Table("task_events", metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("event_id", String, nullable=False, unique=True), Column("project", String, nullable=False, index=True),
    Column("task_id", String, nullable=False, index=True), Column("agent_id", String, nullable=False),
    Column("run_id", String, nullable=False, index=True), Column("event_type", String, nullable=False),
    Column("timestamp", String, nullable=False), Column("received_at", String, nullable=False),
    Column("source", String, nullable=False), Column("provider", String), Column("model_name", String),
    Column("process_id", Integer), Column("process_created", String), Column("payload", Text, nullable=False))
cache = Table("source_cache", metadata, Column("key", String, primary_key=True),
              Column("sampled_at", String, nullable=False), Column("payload", Text, nullable=False))
system = Table("system_events", metadata, Column("id", Integer, primary_key=True),
               Column("timestamp", String, nullable=False), Column("code", String, nullable=False))


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def safe_path(path):
    path = Path(path).absolute()
    for node in (path, *path.parents):
        if node.exists() and (node.is_symlink() or getattr(node.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("Reparse paths are forbidden")
    return path


def clean(value, limit=160):
    # Browser-bound engineering summaries must never carry credentials.
    import re
    text = str(value or "")[:limit]
    text = re.sub(r"(?i)(?:sk-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{12,}", "[REDACTED]", text)
    text = re.sub(r"(?i)(?:bearer|api[_-]?key|token|password|secret)\s*[:=]\s*\S+", "[REDACTED]", text)
    return "".join(c for c in text if c >= " " or c in "\n\t")


def validate_event(value):
    if not isinstance(value, dict) or len(json.dumps(value)) > 12000:
        raise ValueError("Invalid event")
    allowed = {"event_id", "project", "task_id", "agent_id", "run_id", "event_type", "timestamp", "source",
               "provider", "model_name", "process_id", "process_created", "payload"}
    if set(value) - allowed:
        raise ValueError("Unexpected event field")
    row = dict(value)
    for field in ("event_id", "task_id", "run_id"):
        row[field] = str(uuid.UUID(row[field]))
    if row["project"] not in PROJECTS or row["agent_id"] not in ROLES or row["event_type"] not in EVENTS:
        raise ValueError("Invalid event scope")
    stamp = datetime.fromisoformat(row["timestamp"])
    if stamp.tzinfo is None:
        raise ValueError("Timezone required")
    if abs((datetime.now(timezone.utc) - stamp).total_seconds()) > 366 * 86400:
        raise ValueError("Event time outside retention bounds")
    if row["source"] not in {"lads-worker-host", "lads-controller-host", "lads-worker-host-v2", "lads-controller-host-v2", "control-center-test"}:
        raise ValueError("Invalid event source")
    if row.get("provider") not in {None, "deepseek", "openai"}:
        raise ValueError("Invalid provider")
    if row.get("process_id") is not None and (type(row["process_id"]) is not int or row["process_id"] <= 0):
        raise ValueError("Invalid PID")
    row["model_name"] = clean(row.get("model_name")) or None
    payload = row.get("payload", {})
    if not isinstance(payload, dict) or set(payload) - SAFE_PAYLOAD:
        raise ValueError("Invalid payload")
    if any(v is not None and type(v) not in (str, int, bool) for v in payload.values()):
        raise ValueError("Payload values must be bounded primitives")
    if row.get("process_created") is not None and (not isinstance(row["process_created"], str) or not row["process_created"].isdecimal() or len(row["process_created"]) > 24):
        raise ValueError("Invalid process identity")
    for key in ("input_tokens", "output_tokens", "cached_tokens"):
        if payload.get(key) is not None and (type(payload[key]) is not int or not 0 <= payload[key] <= 10**12):
            raise ValueError("Invalid usage")
    row["payload"] = json.dumps({k: clean(v, 180) if isinstance(v, str) else v for k, v in payload.items()}, ensure_ascii=False)
    row["received_at"] = utcnow()
    return row


class MonitorStore:
    def __init__(self, home):
        self.home = safe_path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = safe_path(self.home / "control-center.sqlite3")
        for suffix in ("-wal", "-shm", "-journal"):
            safe_path(str(self.path) + suffix)
        self.lock = threading.RLock()
        self.engine = create_engine("sqlite:///" + self.path.as_posix(), poolclass=NullPool,
                                    connect_args={"timeout": 5})
        @event.listens_for(self.engine, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA foreign_keys=ON")
        # No automatic migration of unknown or newer databases.
        with self.engine.begin() as connection:
            version = connection.exec_driver_sql("PRAGMA user_version").scalar()
            app = connection.exec_driver_sql("PRAGMA application_id").scalar()
            tables = connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").all()
            if version not in (0, SCHEMA) or app not in (0, APP_ID) or (tables and (version != SCHEMA or app != APP_ID)):
                raise ValueError("Unsupported monitoring database")
            metadata.create_all(connection)
            connection.exec_driver_sql(f"PRAGMA user_version={SCHEMA}")
            connection.exec_driver_sql(f"PRAGMA application_id={APP_ID}")

    def append(self, value):
        row = validate_event(value)
        with self.lock, self.engine.begin() as connection:
            result = connection.execute(insert(events).values(**row).on_conflict_do_nothing(index_elements=["event_id"]))
            return bool(result.rowcount)

    def rows(self, *, project="all", task_id=None, after=0, limit=100, descending=True, trusted_only=False):
        if project not in {"all", *PROJECTS} or not 1 <= limit <= 200 or after < 0:
            raise ValueError("Invalid query bounds")
        statement = select(events).where(events.c.sequence > after)
        if trusted_only:
            statement = statement.where(events.c.source.in_(("lads-worker-host-v2", "lads-controller-host-v2")))
        if project != "all":
            statement = statement.where(events.c.project == project)
        if task_id:
            statement = statement.where(events.c.task_id == str(uuid.UUID(task_id)))
        statement = statement.order_by(events.c.sequence.desc() if descending else events.c.sequence).limit(limit)
        with self.engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(statement).mappings()]
        for row in rows:
            row["payload"] = json.loads(row["payload"])
        return rows

    def latest_agents(self, project="all", *, trusted_only=False):
        # One latest sample per role/run, bounded by recent active window, then group in Python.
        from sqlalchemy import func
        statement = select(func.max(events.c.sequence)).where(events.c.event_type.like("AGENT_%"))
        if trusted_only:
            statement = statement.where(events.c.source.in_(("lads-worker-host-v2", "lads-controller-host-v2")))
        if project != "all":
            statement = statement.where(events.c.project == project)
        statement = statement.group_by(events.c.agent_id, events.c.run_id).scalar_subquery()
        with self.engine.connect() as connection:
            rows = [dict(r) for r in connection.execute(select(events).where(events.c.sequence.in_(statement))
                    .order_by(events.c.sequence.desc()).limit(100)).mappings()]
        for row in rows:
            row["payload"] = json.loads(row["payload"])
        return rows

    def put_cache(self, key, value):
        with self.lock, self.engine.begin() as connection:
            connection.execute(insert(cache).values(key=key, sampled_at=utcnow(), payload=json.dumps(value))
                               .on_conflict_do_update(index_elements=["key"], set_={"sampled_at": utcnow(), "payload": json.dumps(value)}))

    def get_cache(self, key):
        with self.engine.connect() as connection:
            row = connection.execute(select(cache).where(cache.c.key == key)).mappings().first()
        return None if row is None else {"sampled_at": row["sampled_at"], "data": json.loads(row["payload"])}

    def error(self, code):
        with self.lock, self.engine.begin() as connection:
            connection.execute(system.insert().values(timestamp=utcnow(), code=clean(code, 120)))

    def errors(self):
        with self.engine.connect() as connection:
            return [dict(r) for r in connection.execute(select(system).order_by(system.c.id.desc()).limit(30)).mappings()]

    def backup(self):
        folder = safe_path(self.home / "backups")
        folder.mkdir(exist_ok=True)
        target = safe_path(folder / ("monitor-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex + ".sqlite3"))
        with self.lock, closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
        return target

    @classmethod
    def restore(cls, backup, empty_home):
        """Offline restore into a NEW directory only; never overwrite a live database."""
        target = safe_path(empty_home)
        if target.exists():
            raise ValueError("Restore requires a new directory")
        source = safe_path(backup)
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as connection:
            if connection.execute("PRAGMA application_id").fetchone()[0] != APP_ID or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA:
                raise ValueError("Invalid backup")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Damaged backup")
            target.mkdir(parents=True)
            with closing(sqlite3.connect(target / "control-center.sqlite3")) as destination:
                connection.backup(destination)
        return cls(target)

    def ingest_spool(self):
        spool = safe_path(self.home / "spool")
        if not spool.exists():
            return 0
        count = 0
        for path in sorted(spool.glob("*.json"))[:500]:
            try:
                safe_path(path)
                if str(uuid.UUID(path.stem)) != path.stem:
                    continue
                if path.stat().st_size > 12000:
                    raise ValueError("Oversize event")
                envelope = json.loads(path.read_text(encoding="utf-8"))
                if envelope.get("event_id") != path.stem:
                    raise ValueError("Mismatched outbox envelope")
                self.append(envelope)
            except FileNotFoundError:
                continue  # A concurrent collector may already have acknowledged this envelope.
            except (OSError, ValueError, KeyError, TypeError) as exc:
                # Keep failed bytes for diagnosis/retry; never discard failed writes.
                self.error("SPOOL_" + type(exc).__name__)
                # Validation errors are retained for manual recovery, not retried every 2s.
                if path.stem.count("-") == 4 and _is_regular(path):
                    path.rename(path.with_suffix(".rejected"))
                continue
            path.unlink(missing_ok=True)  # Only acknowledged telemetry envelopes, never user files.
            count += 1
        return count


def _is_regular(path):
    return path.is_file() and not path.is_symlink() and not getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
