"""Transactional knowledge-store services and stable write APIs."""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .database import (
    HEAD_REVISION,
    validate_schema,
)
from .safety import (
    KnowledgeError,
    NoisePayloadError,
    canonical_json,
    normalize_kind,
    normalize_sha,
    normalize_source_ref,
    normalize_utc_iso,
    normalize_uuid,
    sanitize_metadata,
    sanitize_text,
    sha256_text,
    utc_now_iso,
)


class ConflictError(KnowledgeError):
    """Raised for conflicting idempotency keys or invalid state transitions."""


class NotFoundError(KnowledgeError):
    """Raised when a project or task is not visible in the bound scope."""


_TASK_TRANSITIONS = {
    "planned": {"active", "cancelled"},
    "active": {"testing", "blocked", "failed", "completed", "cancelled"},
    "testing": {"review", "blocked", "failed", "completed"},
    "review": {"active", "testing", "blocked", "failed", "completed"},
    "blocked": {"active", "cancelled"},
    "failed": {"active", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


def _new_id() -> str:
    return str(uuid.uuid4())


def _assert_replay(existing: dict[str, Any], values: dict[str, Any], *, ignore: set[str]) -> None:
    if any(existing[key] != value for key, value in values.items() if key not in ignore):
        raise ConflictError("Record identity already has a different canonical payload")


def _query_rows(
    connection: Connection, sql: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    rows = connection.execute(text(sql), params or {}).mappings()
    return [dict(row) for row in rows]


def _query_one(
    connection: Connection, sql: str, params: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    rows = _query_rows(connection, sql, params)
    return rows[0] if rows else None


def _insert(connection: Connection, table: str, values: dict[str, Any]) -> None:
    if not table.replace("_", "").isalnum():
        raise KnowledgeError("Unsafe internal table name")
    columns = ", ".join(values)
    parameters = ", ".join(f":{name}" for name in values)
    connection.execute(
        text(f"INSERT INTO {table} ({columns}) VALUES ({parameters})"), values
    )


def _update(
    connection: Connection, table: str, values: dict[str, Any], where: dict[str, Any]
) -> None:
    if not table.replace("_", "").isalnum():
        raise KnowledgeError("Unsafe internal table name")
    assignments = ", ".join(f"{name}=:{name}" for name in values)
    predicates = " AND ".join(f"{name}=:{name}" for name in where)
    parameters = {**values, **where}
    connection.execute(
        text(f"UPDATE {table} SET {assignments} WHERE {predicates}"), parameters
    )


def _normalize_optional_tokens(value: int | None) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    if parsed < 0:
        raise KnowledgeError("Token counts may not be negative")
    return parsed


class KnowledgeStore:
    """Owns one explicitly selected LADS knowledge database."""

    def __init__(
        self,
        engine: Engine,
        *,
        home: Any = None,
        alias: str | None = None,
        path: Any = None,
        kind: str = "memory",
    ) -> None:
        self._engine = engine
        self.home = home
        self.alias = alias
        self.path = path
        self.kind = kind
        self._write_lock = threading.RLock()
        self._local = threading.local()

    @classmethod
    def memory(cls) -> "KnowledgeStore":
        from .database import create_memory_engine

        return cls(create_memory_engine(), kind="memory")

    @classmethod
    def _from_open_file(cls, opened: Any) -> "KnowledgeStore":
        return cls(
            opened.engine,
            home=opened.home,
            alias=opened.alias,
            path=opened.path,
            kind="file",
        )

    def close(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> "KnowledgeStore":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    @contextmanager
    def batch(self) -> Iterator["KnowledgeStore"]:
        """One serialized transaction; any nested failure poisons the whole batch."""
        with self._write_lock:
            if getattr(self._local, "connection", None) is not None:
                try:
                    yield self
                except BaseException:
                    self._local.failed = True
                    raise
                return
            with self._engine.connect() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                self._local.connection = connection
                self._local.failed = False
                try:
                    if validate_schema(connection, integrity=True) != HEAD_REVISION:
                        raise ConflictError("Writes require the current schema revision")
                    yield self
                    if self._local.failed:
                        raise ConflictError("Batch was aborted by a nested operation")
                    validate_schema(connection, integrity=True)
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    self._local.connection = None
                    self._local.failed = False

    @contextmanager
    def _write(self) -> Iterator[Connection]:
        with self.batch():
            yield self._local.connection

    def bind_project_root(self, project_id: str, root_ref: str) -> dict[str, Any]:
        """Explicit one-time binding; indexing never silently binds a project."""
        canonical = normalize_source_ref("path", root_ref)
        import re
        if not re.fullmatch(r"repositories/[0-9a-f]{64}", canonical):
            raise KnowledgeError("Root reference must be a repository fingerprint")
        with self._write() as connection:
            project = self._project(connection, project_id)
            if project["root_ref"] not in (None, canonical):
                raise ConflictError("Project is bound to another repository")
            if project["root_ref"] is None:
                _update(connection, "projects", {"root_ref": canonical, "updated_at": utc_now_iso()}, {"id": project["id"]})
            return self._project(connection, project_id)

    def _project(self, connection: Connection, project_id: str) -> dict[str, Any]:
        canonical_id = normalize_uuid(project_id)
        project = _query_one(
            connection, "SELECT * FROM projects WHERE id=:id", {"id": canonical_id}
        )
        if project is None:
            raise NotFoundError("Project is not present in this store")
        return project

    def _task(
        self, connection: Connection, project_id: str, task_id: str
    ) -> dict[str, Any]:
        canonical_project = normalize_uuid(project_id)
        canonical_task = normalize_uuid(task_id)
        row = _query_one(
            connection,
            "SELECT * FROM dev_tasks WHERE project_id=:project_id AND id=:task_id",
            {"project_id": canonical_project, "task_id": canonical_task},
        )
        if row is None:
            raise NotFoundError("Task does not belong to the selected project")
        return row

    def _run(
        self, connection: Connection, project_id: str, task_id: str, run_id: str
    ) -> dict[str, Any]:
        canonical_run = normalize_uuid(run_id)
        row = _query_one(
            connection,
            "SELECT * FROM agent_runs "
            "WHERE project_id=:project_id AND task_id=:task_id AND id=:run_id",
            {
                "project_id": normalize_uuid(project_id),
                "task_id": normalize_uuid(task_id),
                "run_id": canonical_run,
            },
        )
        if row is None:
            raise NotFoundError("Run does not belong to the selected task")
        return row

    def register_project(
        self,
        name: str,
        *,
        root_ref: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        clean_name = sanitize_text(name, limit=200, allow_empty=False)
        clean_root = (
            normalize_source_ref("path", root_ref) if root_ref is not None else None
        )
        now = utc_now_iso()
        canonical_id = normalize_uuid(project_id) if project_id else _new_id()
        with self._write() as connection:
            existing = _query_one(
                connection, "SELECT * FROM projects WHERE name=:name", {"name": clean_name}
            )
            if existing:
                if existing["root_ref"] not in (None, clean_root):
                    raise ConflictError("Project name already has a different root")
                return existing
            _insert(
                connection,
                "projects",
                {
                    "id": canonical_id,
                    "name": clean_name,
                    "root_ref": clean_root,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            return _query_one(
                connection, "SELECT * FROM projects WHERE id=:id", {"id": canonical_id}
            ) or {}

    def create_task(
        self,
        project_id: str,
        requirement_summary: str,
        *,
        task_id: str | None = None,
        assigned_agent: str | None = None,
    ) -> dict[str, Any]:
        canonical_project = normalize_uuid(project_id)
        canonical_task = normalize_uuid(task_id) if task_id else _new_id()
        summary = sanitize_text(
            requirement_summary, limit=8_000, allow_empty=False
        )
        agent = sanitize_text(assigned_agent, limit=100) if assigned_agent else None
        now = utc_now_iso()
        with self._write() as connection:
            self._project(connection, canonical_project)
            existing = _query_one(
                connection,
                "SELECT * FROM dev_tasks WHERE project_id=:project_id AND id=:id",
                {"project_id": canonical_project, "id": canonical_task},
            )
            if existing:
                if existing["requirement_summary"] != summary:
                    raise ConflictError("Task id already has a different requirement")
                return existing
            _insert(
                connection,
                "dev_tasks",
                {
                    "project_id": canonical_project,
                    "id": canonical_task,
                    "state": "planned",
                    "requirement_summary": summary,
                    "assigned_agent": agent,
                    "commit_sha": None,
                    "pr_ref": None,
                    "test_result": None,
                    "review_result": None,
                    "error_reason": None,
                    "completed_time": None,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            return _query_one(
                connection,
                "SELECT * FROM dev_tasks WHERE project_id=:project_id AND id=:id",
                {"project_id": canonical_project, "id": canonical_task},
            ) or {}

    def update_task(
        self,
        project_id: str,
        task_id: str,
        *,
        requirement_summary: str | None = None,
        assigned_agent: str | None = None,
        commit_sha: str | None = None,
        pr_ref: str | None = None,
        test_result: str | None = None,
        review_result: str | None = None,
        error_reason: str | None = None,
    ) -> dict[str, Any]:
        project = normalize_uuid(project_id)
        task = normalize_uuid(task_id)
        values: dict[str, Any] = {"updated_at": utc_now_iso()}
        if requirement_summary is not None:
            values["requirement_summary"] = sanitize_text(
                requirement_summary, limit=8_000, allow_empty=False
            )
        if assigned_agent is not None:
            values["assigned_agent"] = sanitize_text(assigned_agent, limit=100)
        if commit_sha is not None:
            values["commit_sha"] = normalize_sha(commit_sha)
        if pr_ref is not None:
            values["pr_ref"] = sanitize_text(pr_ref, limit=200)
        if test_result is not None:
            values["test_result"] = sanitize_text(test_result, limit=200)
        if review_result is not None:
            values["review_result"] = sanitize_text(review_result, limit=200)
        if error_reason is not None:
            values["error_reason"] = sanitize_text(error_reason, limit=2_000)
        with self._write() as connection:
            self._task(connection, project, task)
            _update(
                connection,
                "dev_tasks",
                values,
                {"project_id": project, "id": task},
            )
            return _query_one(
                connection,
                "SELECT * FROM dev_tasks WHERE project_id=:project_id AND id=:id",
                {"project_id": project, "id": task},
            ) or {}

    def transition_task(
        self,
        project_id: str,
        task_id: str,
        to_state: str,
        *,
        event_payload: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        project = normalize_uuid(project_id)
        task = normalize_uuid(task_id)
        state = normalize_kind(to_state)
        if state not in _TASK_TRANSITIONS:
            raise KnowledgeError("Unknown task state")
        details = sanitize_metadata(event_payload if event_payload is not None else {})
        payload = sanitize_metadata({"to_state": state, "details": details})
        now = utc_now_iso()
        with self._write() as connection:
            current = self._task(connection, project, task)
            event = self._insert_event(
                connection,
                project,
                task,
                "state_transition",
                payload,
                event_id=event_id,
                occurred_at=now,
            )
            if event["idempotent"]:
                return current
            if state not in _TASK_TRANSITIONS[current["state"]]:
                raise ConflictError(
                    f"Task transition {current['state']} -> {state} is not allowed"
                )
            completed = now if state == "completed" else current["completed_time"]
            _update(
                connection,
                "dev_tasks",
                {
                    "state": state,
                    "completed_time": completed,
                    "updated_at": now,
                },
                {"project_id": project, "id": task},
            )
            return _query_one(
                connection,
                "SELECT * FROM dev_tasks WHERE project_id=:project_id AND id=:id",
                {"project_id": project, "id": task},
            ) or {}

    def _insert_event(
        self,
        connection: Connection,
        project_id: str,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        event_id: str | None,
        occurred_at: str,
    ) -> dict[str, Any]:
        identity = normalize_uuid(event_id) if event_id else _new_id()
        clean_type = sanitize_text(event_type, limit=100, allow_empty=False)
        payload_json = canonical_json(sanitize_metadata(payload))
        existing = _query_one(
            connection, "SELECT * FROM task_events WHERE id=:id", {"id": identity}
        )
        if existing:
            if (
                existing["project_id"] != project_id
                or existing["task_id"] != task_id
                or existing["event_type"] != clean_type
                or existing["payload_json"] != payload_json
            ):
                raise ConflictError("Event id has a conflicting payload")
            return {**existing, "idempotent": True}
        _insert(
            connection,
            "task_events",
            {
                "id": identity,
                "project_id": project_id,
                "task_id": task_id,
                "event_type": clean_type,
                "payload_json": payload_json,
                "occurred_at": occurred_at,
            },
        )
        return {
            "id": identity,
            "project_id": project_id,
            "task_id": task_id,
            "event_type": clean_type,
            "payload_json": payload_json,
            "occurred_at": occurred_at,
            "idempotent": False,
        }

    def record_event(
        self,
        project_id: str,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        project = normalize_uuid(project_id)
        task = normalize_uuid(task_id)
        when = normalize_utc_iso(occurred_at) if occurred_at else utc_now_iso()
        with self._write() as connection:
            self._task(connection, project, task)
            return self._insert_event(
                connection,
                project,
                task,
                event_type,
                payload,
                event_id=event_id,
                occurred_at=when,
            )

    def record_run(
        self,
        project_id: str,
        task_id: str,
        *,
        role: str,
        model_name: str,
        build_number: int,
        attempt: int,
        status: str,
        started_at: str | None = None,
        completed_at: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        error_reason: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        project = normalize_uuid(project_id)
        task = normalize_uuid(task_id)
        identity = normalize_uuid(run_id) if run_id else _new_id()
        build = int(build_number)
        attempt_number = int(attempt)
        if build < 1 or attempt_number < 1:
            raise KnowledgeError("Build and attempt must be positive")
        values = {
            "id": identity,
            "project_id": project,
            "task_id": task,
            "role": sanitize_text(role, limit=50, allow_empty=False),
            "model_name": sanitize_text(model_name, limit=100, allow_empty=False),
            "build_number": build,
            "attempt": attempt_number,
            "status": sanitize_text(status, limit=32, allow_empty=False),
            "started_at": normalize_utc_iso(started_at) if started_at else utc_now_iso(),
            "completed_at": normalize_utc_iso(completed_at) if completed_at else None,
            "input_tokens": _normalize_optional_tokens(input_tokens),
            "output_tokens": _normalize_optional_tokens(output_tokens),
            "error_reason": sanitize_text(error_reason, limit=2_000) if error_reason else None,
        }
        with self._write() as connection:
            self._task(connection, project, task)
            by_id = _query_one(
                connection, "SELECT * FROM agent_runs WHERE id=:id", {"id": identity}
            )
            by_unique = _query_one(
                connection,
                "SELECT * FROM agent_runs WHERE project_id=:project_id "
                "AND task_id=:task_id AND role=:role AND build_number=:build_number "
                "AND attempt=:attempt",
                values,
            )
            existing = by_id or by_unique
            if existing:
                _assert_replay(existing, values, ignore={"id"} | ({"started_at"} if started_at is None else set()))
                comparable = {
                    key: existing[key]
                    for key in (
                        "project_id",
                        "task_id",
                        "role",
                        "model_name",
                        "build_number",
                        "attempt",
                        "status",
                        "input_tokens",
                        "output_tokens",
                    )
                }
                expected = {key: values[key] for key in comparable}
                if comparable != expected:
                    raise ConflictError("Run identity already has a conflicting payload")
                return {**existing, "idempotent": True}
            _insert(connection, "agent_runs", values)
            return {
                **values,
                "idempotent": False,
            }

    def record_test(
        self,
        project_id: str,
        task_id: str,
        *,
        test_name: str,
        command: str,
        status: str,
        summary: str | None = None,
        output_sha: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        run_id: str | None = None,
        test_id: str | None = None,
    ) -> dict[str, Any]:
        project = normalize_uuid(project_id)
        task = normalize_uuid(task_id)
        identity = normalize_uuid(test_id) if test_id else _new_id()
        canonical_run = normalize_uuid(run_id) if run_id else None
        values = {
            "id": identity,
            "project_id": project,
            "task_id": task,
            "run_id": canonical_run,
            "test_name": sanitize_text(test_name, limit=300, allow_empty=False),
            "command": sanitize_text(command, limit=1_000, allow_empty=False),
            "status": sanitize_text(status, limit=32, allow_empty=False),
            "summary": sanitize_text(summary, limit=4_000) if summary else None,
            "output_sha": normalize_sha(output_sha),
            "started_at": normalize_utc_iso(started_at) if started_at else utc_now_iso(),
            "completed_at": normalize_utc_iso(completed_at) if completed_at else None,
        }
        with self._write() as connection:
            self._task(connection, project, task)
            if canonical_run:
                self._run(connection, project, task, canonical_run)
            existing = _query_one(
                connection, "SELECT * FROM test_runs WHERE id=:id", {"id": identity}
            )
            if existing:
                if (
                    existing["project_id"] != project
                    or existing["task_id"] != task
                    or existing["run_id"] != canonical_run
                ):
                    raise ConflictError(
                        "Test id belongs to another project, task, or run"
                    )
                if existing["test_name"] != values["test_name"] or existing[
                    "status"
                ] != values["status"]:
                    raise ConflictError("Test id already has a conflicting payload")
                _assert_replay(existing, values, ignore={"id"} | ({"started_at"} if started_at is None else set()))
                return {**existing, "idempotent": True}
            _insert(connection, "test_runs", values)
            return {**values, "idempotent": False}

    def record_change(
        self,
        project_id: str,
        task_id: str,
        *,
        file_path: str,
        change_type: str,
        source_ref: str | None = None,
        run_id: str | None = None,
        source_raw_sha: str | None = None,
        observed_head: str | None = None,
        actual_content_commit_sha: str | None = None,
        dirty: bool = False,
        status: str = "current",
        diff_sha: str | None = None,
        change_id: str | None = None,
    ) -> dict[str, Any]:
        project = normalize_uuid(project_id)
        task = normalize_uuid(task_id)
        identity = normalize_uuid(change_id) if change_id else _new_id()
        canonical_path = normalize_source_ref("path", file_path)
        canonical_source = normalize_source_ref(
            "path", source_ref if source_ref is not None else file_path
        )
        canonical_run = normalize_uuid(run_id) if run_id else None
        now = utc_now_iso()
        values = {
            "id": identity,
            "project_id": project,
            "task_id": task,
            "run_id": canonical_run,
            "file_path": canonical_path,
            "change_type": sanitize_text(change_type, limit=32, allow_empty=False),
            "diff_sha": normalize_sha(diff_sha),
            "source_ref": canonical_source,
            "source_raw_sha": normalize_sha(source_raw_sha),
            "sanitized_text_sha": None,
            "observed_head": normalize_sha(observed_head),
            "actual_content_commit_sha": normalize_sha(actual_content_commit_sha),
            "dirty": bool(dirty),
            "status": sanitize_text(status, limit=32, allow_empty=False),
            "indexed_at": now,
        }
        with self._write() as connection:
            self._task(connection, project, task)
            if canonical_run:
                self._run(connection, project, task, canonical_run)
            existing = _query_one(
                connection, "SELECT * FROM code_changes WHERE id=:id", {"id": identity}
            )
            if existing:
                if (
                    existing["project_id"] != project
                    or existing["task_id"] != task
                    or existing["run_id"] != canonical_run
                ):
                    raise ConflictError(
                        "Change id belongs to another project, task, or run"
                    )
                if (
                    existing["source_ref"] != canonical_source
                    or existing["file_path"] != canonical_path
                    or existing["change_type"] != values["change_type"]
                ):
                    raise ConflictError("Change id already has a conflicting payload")
                _assert_replay(existing, values, ignore={"id", "indexed_at"})
                return {**existing, "idempotent": True}
            _insert(connection, "code_changes", values)
            return {**values, "idempotent": False}

    def record_usage(
        self,
        project_id: str,
        task_id: str,
        run_id: str,
        *,
        model_name: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        usage_id: str | None = None,
    ) -> dict[str, Any]:
        project = normalize_uuid(project_id)
        task = normalize_uuid(task_id)
        run = normalize_uuid(run_id)
        identity = normalize_uuid(usage_id) if usage_id else _new_id()
        values = {
            "id": identity,
            "project_id": project,
            "task_id": task,
            "run_id": run,
            "model_name": sanitize_text(model_name, limit=100, allow_empty=False),
            "input_tokens": _normalize_optional_tokens(input_tokens),
            "output_tokens": _normalize_optional_tokens(output_tokens),
            "cost_usd": cost_usd,
            "created_at": utc_now_iso(),
        }
        with self._write() as connection:
            self._run(connection, project, task, run)
            existing = _query_one(
                connection, "SELECT * FROM model_usage WHERE id=:id", {"id": identity}
            ) or _query_one(
                connection,
                "SELECT * FROM model_usage WHERE project_id=:project_id AND run_id=:run_id",
                {"project_id": project, "run_id": run},
            )
            if existing:
                if (
                    existing["project_id"] != project
                    or existing["task_id"] != task
                    or existing["run_id"] != run
                ):
                    raise ConflictError(
                        "Usage id belongs to another project, task, or run"
                    )
                if existing["model_name"] != values["model_name"] or existing[
                    "input_tokens"
                ] != values["input_tokens"] or existing["output_tokens"] != values[
                    "output_tokens"
                ]:
                    raise ConflictError("Run usage already has a conflicting payload")
                _assert_replay(existing, values, ignore={"id", "created_at"})
                return {**existing, "idempotent": True}
            _insert(connection, "model_usage", values)
            return {**values, "idempotent": False}

    def _optional_task(
        self, connection: Connection, project: str, task_id: str | None
    ) -> str | None:
        if task_id is None:
            return None
        task = normalize_uuid(task_id)
        self._task(connection, project, task)
        return task

    def _source_values(
        self,
        *,
        title: str,
        body: str,
        source_ref: str,
        source_kind: str,
        source_raw_sha: str | None,
        observed_head: str | None,
        actual_content_commit_sha: str | None,
        dirty: bool,
        status: str,
        now: str,
    ) -> dict[str, Any]:
        clean_title = sanitize_text(title, limit=300, allow_empty=False)
        clean_body = sanitize_text(body, limit=8_000)
        canonical_ref = normalize_source_ref(source_kind, source_ref)
        return {
            "source_ref": canonical_ref,
            "source_raw_sha": normalize_sha(source_raw_sha),
            "sanitized_text_sha": sha256_text(f"{clean_title}\n{clean_body}"),
            "observed_head": normalize_sha(observed_head),
            "actual_content_commit_sha": normalize_sha(actual_content_commit_sha),
            "dirty": bool(dirty),
            "status": sanitize_text(status, limit=32, allow_empty=False),
            "indexed_at": now,
        }

    def upsert_knowledge(
        self,
        project_id: str,
        *,
        kind: str,
        source_ref: str,
        title: str,
        body: str,
        task_id: str | None = None,
        source_raw_sha: str | None = None,
        observed_head: str | None = None,
        actual_content_commit_sha: str | None = None,
        dirty: bool = False,
        status: str = "active",
    ) -> dict[str, Any]:
        project = normalize_uuid(project_id)
        clean_kind = normalize_kind(kind)
        clean_title = sanitize_text(title, limit=300, allow_empty=False)
        clean_body = sanitize_text(body, limit=8_000)
        canonical_ref = normalize_source_ref(clean_kind, source_ref)
        if status not in {"active", "tombstoned"}:
            raise KnowledgeError("Knowledge status must be active or tombstoned")
        now = utc_now_iso()
        sources = self._source_values(
            title=clean_title,
            body=clean_body,
            source_ref=canonical_ref,
            source_kind=clean_kind,
            source_raw_sha=source_raw_sha,
            observed_head=observed_head,
            actual_content_commit_sha=actual_content_commit_sha,
            dirty=dirty,
            status=status,
            now=now,
        )
        with self._write() as connection:
            self._project(connection, project)
            task = self._optional_task(connection, project, task_id)
            existing = _query_one(
                connection,
                "SELECT * FROM knowledge_items WHERE project_id=:project_id "
                "AND kind=:kind AND source_ref=:source_ref",
                {
                    "project_id": project,
                    "kind": clean_kind,
                    "source_ref": canonical_ref,
                },
            )
            if existing:
                identity = existing["id"]
                updated = {
                    **sources,
                    "task_id": task,
                    "title": clean_title,
                    "body": clean_body,
                    "updated_at": now,
                }
                _update(
                    connection,
                    "knowledge_items",
                    updated,
                    {"project_id": project, "id": identity},
                )
                return {**existing, **updated, "id": identity, "idempotent": True}
            identity = _new_id()
            values = {
                "id": identity,
                "project_id": project,
                "task_id": task,
                "kind": clean_kind,
                "title": clean_title,
                "body": clean_body,
                **sources,
                "created_at": now,
                "updated_at": now,
            }
            _insert(connection, "knowledge_items", values)
            return {**values, "idempotent": False}

    def tombstone_knowledge(
        self, project_id: str, *, kind: str, source_ref: str
    ) -> dict[str, Any] | None:
        project = normalize_uuid(project_id)
        clean_kind = normalize_kind(kind)
        canonical_ref = normalize_source_ref(clean_kind, source_ref)
        now = utc_now_iso()
        with self._write() as connection:
            self._project(connection, project)
            existing = _query_one(
                connection,
                "SELECT * FROM knowledge_items WHERE project_id=:project_id "
                "AND kind=:kind AND source_ref=:source_ref",
                {
                    "project_id": project,
                    "kind": clean_kind,
                    "source_ref": canonical_ref,
                },
            )
            if existing is None:
                return None
            _update(
                connection,
                "knowledge_items",
                {"status": "tombstoned", "updated_at": now, "indexed_at": now},
                {"project_id": project, "id": existing["id"]},
            )
            return {
                **existing,
                "status": "tombstoned",
                "updated_at": now,
                "indexed_at": now,
                "idempotent": existing["status"] == "tombstoned",
            }

    def upsert_decision(
        self,
        project_id: str,
        *,
        source_ref: str,
        title: str,
        decision_text: str,
        task_id: str | None = None,
        status: str = "proposed",
        source_raw_sha: str | None = None,
        observed_head: str | None = None,
        actual_content_commit_sha: str | None = None,
        dirty: bool = False,
    ) -> dict[str, Any]:
        project = normalize_uuid(project_id)
        clean_title = sanitize_text(title, limit=300, allow_empty=False)
        clean_text = sanitize_text(decision_text, limit=8_000, allow_empty=False)
        canonical_ref = normalize_source_ref("decision", source_ref)
        now = utc_now_iso()
        sources = self._source_values(
            title=clean_title,
            body=clean_text,
            source_ref=canonical_ref,
            source_kind="decision",
            source_raw_sha=source_raw_sha,
            observed_head=observed_head,
            actual_content_commit_sha=actual_content_commit_sha,
            dirty=dirty,
            status=status,
            now=now,
        )
        with self._write() as connection:
            self._project(connection, project)
            task = self._optional_task(connection, project, task_id)
            existing = _query_one(
                connection,
                "SELECT * FROM architecture_decisions "
                "WHERE project_id=:project_id AND source_ref=:source_ref",
                {"project_id": project, "source_ref": canonical_ref},
            )
            if existing:
                updated = {
                    **sources,
                    "task_id": task,
                    "title": clean_title,
                    "decision_text": clean_text,
                    "updated_at": now,
                }
                _update(
                    connection,
                    "architecture_decisions",
                    updated,
                    {"project_id": project, "id": existing["id"]},
                )
                return {**existing, **updated, "idempotent": True}
            values = {
                "id": _new_id(),
                "project_id": project,
                "task_id": task,
                "title": clean_title,
                "decision_text": clean_text,
                **sources,
                "created_at": now,
                "updated_at": now,
            }
            _insert(connection, "architecture_decisions", values)
            return {**values, "idempotent": False}

    def upsert_issue(
        self,
        project_id: str,
        *,
        source_ref: str,
        title: str,
        details: str,
        severity: str,
        task_id: str | None = None,
        status: str = "open",
        source_raw_sha: str | None = None,
        observed_head: str | None = None,
        actual_content_commit_sha: str | None = None,
        dirty: bool = False,
    ) -> dict[str, Any]:
        project = normalize_uuid(project_id)
        clean_title = sanitize_text(title, limit=300, allow_empty=False)
        clean_details = sanitize_text(details, limit=8_000, allow_empty=False)
        clean_severity = normalize_kind(severity)
        if clean_severity not in {"low", "medium", "high", "critical"}:
            raise KnowledgeError("Unknown issue severity")
        canonical_ref = normalize_source_ref("issue", source_ref)
        now = utc_now_iso()
        sources = self._source_values(
            title=clean_title,
            body=clean_details,
            source_ref=canonical_ref,
            source_kind="issue",
            source_raw_sha=source_raw_sha,
            observed_head=observed_head,
            actual_content_commit_sha=actual_content_commit_sha,
            dirty=dirty,
            status=status,
            now=now,
        )
        with self._write() as connection:
            self._project(connection, project)
            task = self._optional_task(connection, project, task_id)
            existing = _query_one(
                connection,
                "SELECT * FROM known_issues "
                "WHERE project_id=:project_id AND source_ref=:source_ref",
                {"project_id": project, "source_ref": canonical_ref},
            )
            if existing:
                updated = {
                    **sources,
                    "task_id": task,
                    "title": clean_title,
                    "details": clean_details,
                    "severity": clean_severity,
                    "updated_at": now,
                }
                _update(
                    connection,
                    "known_issues",
                    updated,
                    {"project_id": project, "id": existing["id"]},
                )
                return {**existing, **updated, "idempotent": True}
            values = {
                "id": _new_id(),
                "project_id": project,
                "task_id": task,
                "title": clean_title,
                "details": clean_details,
                "severity": clean_severity,
                **sources,
                "created_at": now,
                "updated_at": now,
            }
            _insert(connection, "known_issues", values)
            return {**values, "idempotent": False}

    def reader(self, project_id: str) -> Any:
        from .queries import KnowledgeReader

        return KnowledgeReader.from_store(self, normalize_uuid(project_id))

    def backup(self) -> Any:
        from .backup import create_backup

        return create_backup(self)

