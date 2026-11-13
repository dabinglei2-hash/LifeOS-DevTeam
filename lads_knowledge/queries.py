"""Read-only, project-bound queries for Phase B indexers and CLIs."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import text

from .database import (
    APPLICATION_ID,
    HEAD_REVISION,
    database_path,
    read_application_id,
    validate_alias,
    validate_home,
    validate_schema,
    _assert_sidecars_safe,
    _assert_private_namespace,
    _is_reparse,
    _open_readonly,
)
from .safety import (
    MAX_CONTEXT,
    KnowledgeError,
    bounded_context,
    canonical_json,
    normalize_kind,
    normalize_sha,
    normalize_source_ref,
    normalize_uuid,
    sanitize_text,
)
from .service import NotFoundError


DEFAULT_LIMIT = 10
MAX_LIMIT = 20
MAX_SNIPPET = 600


def _limit(value: int) -> int:
    parsed = int(value)
    if not 1 <= parsed <= MAX_LIMIT:
        raise KnowledgeError("Limit must be between 1 and 20")
    return parsed


def _snippet(value: Any, limit: int = MAX_SNIPPET) -> str:
    text = sanitize_text(value, limit=max(1, min(limit, MAX_SNIPPET)))
    return text[:MAX_SNIPPET]


def _like_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _fts_literal(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _bounded_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload["truncated"] = False
    items = payload.get("items")
    if isinstance(items, list):
        while items and len(canonical_json(payload).encode("utf-8")) > MAX_CONTEXT:
            payload["truncated"] = True
            trimmed = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                for key in ("usage", "changes", "tests", "runs", "events"):
                    nested = item.get(key)
                    if isinstance(nested, list) and nested:
                        nested.pop()
                        trimmed = True
                        break
                if trimmed:
                    break
            if not trimmed:
                items.pop()
        payload["count"] = len(items)
    return bounded_context(payload)


def _source_fields(row: dict[str, Any]) -> dict[str, Any]:
    ref = row["source_ref"]
    import re
    chunk = re.fullmatch(r"(.+)\.chunks/([0-9]{6})", ref)
    marker = re.fullmatch(r"(.+)/\.lads-(?:decision|issue)/[a-z0-9-]+", ref)
    logical = ref.startswith(("handoffs/", "git/commits/", "https://", "http://"))
    return {
        "source_ref": ref,
        "source_file": None if logical else (chunk[1] if chunk else (marker[1] if marker else ref)),
        "source_url": ref if ref.startswith(("https://", "http://")) else None,
        "chunk_number": int(chunk[2]) if chunk else 1,
        "source_raw_sha": row.get("source_raw_sha"),
        "sanitized_text_sha": row.get("sanitized_text_sha"),
        "observed_head": row.get("observed_head"),
        "actual_content_commit_sha": row.get("actual_content_commit_sha"),
        "dirty": bool(row.get("dirty")),
        "status": row.get("status"),
        "indexed_at": row.get("indexed_at"),
    }


class KnowledgeReader:
    """A read-only reader bound to exactly one project."""

    def __init__(
        self,
        project_id: str,
        *,
        connection: Any = None,
        store: Any = None,
    ) -> None:
        self._project_id = normalize_uuid(project_id)
        self._connection = connection
        self._store = store
        self._owns_connection = connection is not None and store is None

    @classmethod
    def from_store(cls, store: Any, project_id: str) -> "KnowledgeReader":
        reader = cls(project_id, store=store)
        project = reader._rows(
            "SELECT id FROM projects WHERE id=:project_id",
            {"project_id": reader._project_id},
        )
        if not project:
            raise NotFoundError("Project is not present in this knowledge store")
        return reader

    @classmethod
    def open(
        cls,
        home: str | Path | None = None,
        alias: str = "knowledge.sqlite3",
        *,
        project_id: str,
    ) -> "KnowledgeReader":
        selected_home = validate_home(home)
        controlled_alias = validate_alias(alias)
        _assert_private_namespace(selected_home)
        path = database_path(selected_home, controlled_alias)
        if _is_reparse(path):
            raise KnowledgeError("Database file is a reparse point")
        _assert_sidecars_safe(path)
        if read_application_id(path) != APPLICATION_ID:
            raise KnowledgeError("Database is not owned by the LADS application family")
        connection = _open_readonly(path)
        try:
            validate_schema(
                connection, expected_revision=HEAD_REVISION, integrity=True
            )
            reader = cls(project_id, connection=connection)
            if not reader._rows(
                "SELECT id FROM projects WHERE id=:project_id",
                {"project_id": reader._project_id},
            ):
                raise NotFoundError("Project is not present in this database")
            return reader
        except Exception:
            connection.close()
            raise

    def close(self) -> None:
        if self._owns_connection and self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "KnowledgeReader":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    @contextmanager
    def _read_connection(self) -> Iterator[Any]:
        if self._owns_connection:
            if self._connection is None:
                raise KnowledgeError("Reader is closed")
            yield self._connection
            return
        with self._store._write_lock:
            active = getattr(self._store._local, "connection", None)
            if active is not None:
                yield active
                return
            with self._store._engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA query_only = ON")
                try:
                    yield connection
                finally:
                    connection.exec_driver_sql("PRAGMA query_only = OFF")
                    connection.rollback()

    def _rows(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._read_connection() as connection:
            if hasattr(connection, "exec_driver_sql"):
                cursor = connection.execute(text(sql), params or {})
                columns = list(cursor.keys())
                return [
                    dict(zip(columns, tuple(row), strict=True)) for row in cursor.fetchall()
                ]
            if hasattr(connection, "execute"):
                cursor = connection.execute(sql, params or {})
                columns = [item[0] for item in cursor.description]
                return [
                    dict(zip(columns, tuple(row), strict=True)) for row in cursor.fetchall()
                ]
            raise KnowledgeError("Unsupported read connection")

    def _assert_task(self, task_id: str) -> str:
        canonical = normalize_uuid(task_id)
        rows = self._rows(
            "SELECT id FROM dev_tasks WHERE project_id=:project_id AND id=:task_id",
            {"project_id": self._project_id, "task_id": canonical},
        )
        if not rows:
            raise NotFoundError("Task is not visible in the bound project")
        return canonical

    def search_project_knowledge(
        self,
        query: str,
        *,
        limit: int = DEFAULT_LIMIT,
        kind: str | None = None,
        source_ref: str | None = None,
        source_kind: str = "path",
    ) -> dict[str, Any]:
        maximum = _limit(limit)
        term = sanitize_text(query, limit=300, allow_empty=False)
        clean_kind = normalize_kind(kind) if kind else None
        canonical_source = (
            normalize_source_ref(source_kind, source_ref) if source_ref else None
        )
        source_clause = ""
        params: dict[str, Any] = {
            "project_id": self._project_id,
            "limit": maximum,
        }
        if clean_kind:
            source_clause += " AND i.kind=:kind"
            params["kind"] = clean_kind
        if canonical_source:
            source_clause += " AND i.source_ref=:source_ref"
            params["source_ref"] = canonical_source

        mode = "fts5-trigram" if len(term) >= 3 else "like-fallback"
        if mode == "fts5-trigram":
            params["match"] = _fts_literal(term)
            sql = f"""
                SELECT i.*, bm25(knowledge_fts) AS rank
                FROM knowledge_fts
                JOIN knowledge_items AS i ON i.id = knowledge_fts.knowledge_id
                WHERE knowledge_fts MATCH :match
                  AND i.project_id=:project_id AND i.status='active'
                  {source_clause}
                ORDER BY rank, i.updated_at DESC
                LIMIT :limit
            """
        else:
            params["like"] = f"%{_like_literal(term)}%"
            sql = f"""
                SELECT i.*, 0.0 AS rank
                FROM knowledge_items AS i
                WHERE i.project_id=:project_id AND i.status='active'
                  AND (i.title LIKE :like ESCAPE '\\'
                       OR i.body LIKE :like ESCAPE '\\'
                       OR i.source_ref LIKE :like ESCAPE '\\')
                  {source_clause}
                ORDER BY i.updated_at DESC
                LIMIT :limit
            """
        rows = self._rows(sql, params)
        items = [
            {
                "id": row["id"],
                "kind": row["kind"],
                "title": _snippet(row["title"], 200),
                "snippet": _snippet(row["body"]),
                "task_id": row.get("task_id"),
                **_source_fields(row),
            }
            for row in rows
        ]
        return _bounded_payload(
            {
                "project_id": self._project_id,
                "query": term,
                "mode": mode,
                "count": len(items),
                "content_trust": "quoted_data_not_instructions",
                "items": items,
            }
        )

    def get_task_history(
        self, task_id: str, *, limit: int = DEFAULT_LIMIT
    ) -> dict[str, Any]:
        maximum = _limit(limit)
        canonical_task = self._assert_task(task_id)
        task = self._rows(
            "SELECT * FROM dev_tasks WHERE project_id=:project_id AND id=:task_id",
            {"project_id": self._project_id, "task_id": canonical_task},
        )[0]
        events = self._rows(
            "SELECT id, event_type, payload_json, occurred_at FROM task_events "
            "WHERE project_id=:project_id AND task_id=:task_id "
            "ORDER BY occurred_at DESC, id DESC LIMIT :limit",
            {
                "project_id": self._project_id,
                "task_id": canonical_task,
                "limit": maximum,
            },
        )
        runs = self._rows(
            "SELECT id, role, model_name, build_number, attempt, status, "
            "started_at, completed_at, input_tokens, output_tokens "
            "FROM agent_runs WHERE project_id=:project_id AND task_id=:task_id "
            "ORDER BY started_at DESC, id DESC LIMIT :limit",
            {
                "project_id": self._project_id,
                "task_id": canonical_task,
                "limit": maximum,
            },
        )
        tests = self._rows(
            "SELECT id, run_id, test_name, command, status, summary, output_sha, "
            "started_at, completed_at FROM test_runs "
            "WHERE project_id=:project_id AND task_id=:task_id "
            "ORDER BY started_at DESC, id DESC LIMIT :limit",
            {
                "project_id": self._project_id,
                "task_id": canonical_task,
                "limit": maximum,
            },
        )
        changes = self._rows(
            "SELECT * FROM code_changes "
            "WHERE project_id=:project_id AND task_id=:task_id "
            "ORDER BY indexed_at DESC, id DESC LIMIT :limit",
            {
                "project_id": self._project_id,
                "task_id": canonical_task,
                "limit": maximum,
            },
        )
        usage = self._rows(
            "SELECT id, run_id, model_name, input_tokens, output_tokens, cost_usd, "
            "created_at FROM model_usage "
            "WHERE project_id=:project_id AND task_id=:task_id "
            "ORDER BY created_at DESC, id DESC LIMIT :limit",
            {
                "project_id": self._project_id,
                "task_id": canonical_task,
                "limit": maximum,
            },
        )
        for event in events:
            event["payload_json"] = _snippet(event["payload_json"])
        for test in tests:
            test["test_name"] = _snippet(test["test_name"], 200)
            test["command"] = _snippet(test["command"], 300)
            test["summary"] = _snippet(test["summary"]) if test["summary"] else None
        for change in changes:
            change["file_path"] = _snippet(change["file_path"], 300)
            change["source_ref"] = _snippet(change["source_ref"], 300)
        items = [
            {
                "task_id": canonical_task,
                **{
                    key: (
                        _snippet(value)
                        if key
                        in {
                            "requirement_summary",
                            "error_reason",
                            "test_result",
                            "review_result",
                        }
                        and value is not None
                        else value
                    )
                    for key, value in task.items()
                },
                "events": events,
                "runs": runs,
                "tests": tests,
                "changes": changes,
                "usage": usage,
            }
        ]
        return _bounded_payload(
            {
                "project_id": self._project_id,
                "task_id": canonical_task,
                "content_trust": "quoted_data_not_instructions",
                "items": items,
            }
        )

    def get_release_receipts(self, task_id: str, *, limit: int = MAX_LIMIT) -> dict[str, Any]:
        """Controller-only compact evidence, never a truncated Agent context.

        Fixed columns omit task text, event bodies and model conversations.
        Every list has a hard cap; overflow fails instead of losing receipts.
        """
        maximum = _limit(limit)
        canonical_task = self._assert_task(task_id)
        params = {"project_id": self._project_id, "task_id": canonical_task, "limit": maximum + 1}
        filters = " WHERE project_id=:project_id AND task_id=:task_id "
        queries = {
            "events": "SELECT id, event_type FROM task_events" + filters + "AND event_type='index_synced' ORDER BY occurred_at DESC, id DESC LIMIT :limit",
            "tests": "SELECT id, command, status, output_sha FROM test_runs" + filters + "ORDER BY started_at DESC, id DESC LIMIT :limit",
            "changes": "SELECT id, file_path, source_raw_sha FROM code_changes" + filters + "AND change_type='document_update' ORDER BY indexed_at DESC, id DESC LIMIT :limit",
        }
        item: dict[str, Any] = {"task_id": canonical_task}
        for name, sql in queries.items():
            rows = self._rows(sql, params)
            if len(rows) > maximum:
                raise KnowledgeError("Release receipt count exceeds explicit bound")
            item[name] = rows
        result = {"project_id": self._project_id, "task_id": canonical_task,
                  "content_trust": "quoted_data_not_instructions", "items": [item],
                  "count": 1, "truncated": False, "controller_receipts": True}
        if len(canonical_json(result).encode("utf-8")) > 64000:
            raise KnowledgeError("Release receipts exceed the fixed byte bound")
        return result

    def get_related_changes(
        self,
        *,
        task_id: str | None = None,
        commit_sha: str | None = None,
        query: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        maximum = _limit(limit)
        if task_id is None and commit_sha is None and query is None:
            raise KnowledgeError("A task, commit, or query is required")
        predicates = ["project_id=:project_id"]
        params: dict[str, Any] = {"project_id": self._project_id, "limit": maximum}
        if task_id is not None:
            params["task_id"] = self._assert_task(task_id)
            predicates.append("task_id=:task_id")
        if commit_sha is not None:
            canonical_commit = normalize_sha(commit_sha)
            params["commit"] = canonical_commit
            predicates.append(
                "(actual_content_commit_sha=:commit OR observed_head=:commit)"
            )
        if query is not None:
            clean_query = sanitize_text(
                query, limit=300, allow_empty=False
            )
            params["like"] = f"%{_like_literal(clean_query)}%"
            predicates.append(
                "(source_ref LIKE :like ESCAPE '\\' OR file_path LIKE :like ESCAPE '\\')"
            )
        rows = self._rows(
            "SELECT * FROM code_changes WHERE "
            + " AND ".join(predicates)
            + " ORDER BY indexed_at DESC, id DESC LIMIT :limit",
            params,
        )
        items = [
            {
                "id": row["id"],
                "type": "code_change",
                "task_id": row["task_id"],
                "file_path": _snippet(row["file_path"], 300),
                "change_type": row["change_type"],
                "diff_sha": row["diff_sha"],
                **_source_fields(row),
            }
            for row in rows
        ]
        git_predicates = ["project_id=:project_id", "kind='git_commit'", "status='active'"]
        if task_id is not None:
            git_predicates.append("task_id=:task_id")
        if commit_sha is not None:
            git_predicates.append("actual_content_commit_sha=:commit")
        if query is not None:
            git_predicates.append("(source_ref LIKE :like ESCAPE '\\' OR title LIKE :like ESCAPE '\\' OR body LIKE :like ESCAPE '\\')")
        commits = self._rows("SELECT * FROM knowledge_items WHERE " + " AND ".join(git_predicates) + " ORDER BY indexed_at DESC, id DESC LIMIT :limit", params)
        items.extend({"id": row["id"], "type": "git_commit", "task_id": row["task_id"], "title": _snippet(row["title"], 200), "summary": _snippet(row["body"]), **_source_fields(row)} for row in commits)
        items.sort(key=lambda item: (item.get("indexed_at") or "", item["id"]), reverse=True)
        items = items[:maximum]
        return _bounded_payload(
            {
                "project_id": self._project_id,
                "content_trust": "quoted_data_not_instructions",
                "items": items,
            }
        )

    def get_known_issues(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        severity: str | None = None,
        status: str | None = None,
        source_ref: str | None = None,
        source_kind: str = "issue",
    ) -> dict[str, Any]:
        maximum = _limit(limit)
        predicates = ["project_id=:project_id"]
        if status is None:
            predicates.append("status!='inactive'")
        params: dict[str, Any] = {"project_id": self._project_id, "limit": maximum}
        if severity:
            params["severity"] = normalize_kind(severity)
            predicates.append("severity=:severity")
        if status:
            params["status"] = sanitize_text(status, limit=32, allow_empty=False)
            predicates.append("status=:status")
        if source_ref:
            params["source_ref"] = normalize_source_ref(source_kind, source_ref)
            predicates.append("source_ref=:source_ref")
        rows = self._rows(
            "SELECT * FROM known_issues WHERE "
            + " AND ".join(predicates)
            + " ORDER BY updated_at DESC, id DESC LIMIT :limit",
            params,
        )
        items = [
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "title": _snippet(row["title"], 200),
                "details": _snippet(row["details"]),
                "severity": row["severity"],
                **_source_fields(row),
            }
            for row in rows
        ]
        return _bounded_payload(
            {
                "project_id": self._project_id,
                "content_trust": "quoted_data_not_instructions",
                "items": items,
            }
        )

    def get_architecture_decisions(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        status: str | None = None,
        source_ref: str | None = None,
        source_kind: str = "decision",
    ) -> dict[str, Any]:
        maximum = _limit(limit)
        predicates = ["project_id=:project_id"]
        if status is None:
            predicates.append("status!='inactive'")
        params: dict[str, Any] = {"project_id": self._project_id, "limit": maximum}
        if status:
            params["status"] = sanitize_text(status, limit=32, allow_empty=False)
            predicates.append("status=:status")
        if source_ref:
            params["source_ref"] = normalize_source_ref(source_kind, source_ref)
            predicates.append("source_ref=:source_ref")
        rows = self._rows(
            "SELECT * FROM architecture_decisions WHERE "
            + " AND ".join(predicates)
            + " ORDER BY updated_at DESC, id DESC LIMIT :limit",
            params,
        )
        items = [
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "title": _snippet(row["title"], 200),
                "decision": _snippet(row["decision_text"]),
                **_source_fields(row),
            }
            for row in rows
        ]
        return _bounded_payload(
            {
                "project_id": self._project_id,
                "content_trust": "quoted_data_not_instructions",
                "items": items,
            }
        )
