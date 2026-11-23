"""Read-only, project-isolated telemetry snapshots for LADS Control Center."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from lads_automation.project_registry import ProjectRegistry, RegistryError
from lads_knowledge.queries import KnowledgeReader
from lads_knowledge.safety import sanitize_text

PROJECT_KEYS = ("lifeos", "devteam")
PROJECT_LABELS = {"lifeos": "Life OS", "devteam": "LADS DevTeam"}
PROJECT_REPOSITORIES = {
    "lifeos": "dabinglei2-hash/LifeOS",
    "devteam": "dabinglei2-hash/LifeOS-DevTeam",
}
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE = ROOT.parent / "LADS-Workspace"

AGENT_SPECS = (
    ("gpt-controller", "GPT / Codex", "Controller"),
    ("scanner", "Scanner", "DeepSeek"),
    ("reviewer", "Reviewer", "DeepSeek"),
    ("builder", "Builder", "DeepSeek"),
    ("qa-tester", "QA Tester", "DeepSeek"),
)
RECORDED_AGENT_ROLES = frozenset({"scanner", "reviewer", "builder", "qa-tester"})
ASSIGNED_AGENT_LIMIT = 100
SUMMARY_LIMIT = 600
MODEL_LIMIT = 100
RECENT_LIMIT = 20

LIMITATIONS = (
    "Agent status is UNKNOWN because Phase 1 has no heartbeat connection.",
    "Token counts are historical values recorded in model_usage and may be incomplete; null is unknown, not zero.",
    "QA pass rate counts test_runs with status passed or failed; other statuses are excluded and individual cases are not counted.",
    "Cost is UNKNOWN. Nullable cost_usd is not treated as invoice or billing data.",
    "Remote synchronization and GitHub network status are UNKNOWN; only bounded local Git metadata is read.",
    "The 15-second browser refresh is a snapshot refresh, not an Agent heartbeat.",
)


class _UnavailableReader:
    """A reader-shaped sentinel that fails closed without exposing details."""

    def _rows(self, _sql: str, _params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        raise RuntimeError("control-center reader unavailable")


def _clean(value: Any, limit: int, default: str | None = None) -> str | None:
    if value is None:
        return default
    text = sanitize_text(value, limit=limit)
    return text if text else default


def _as_int(value: Any, default: int = 0) -> int:
    if type(value) is int:
        return value
    return default


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    text = _clean(value, 48)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parsed_timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return (
        parsed.astimezone(UTC)
        if parsed.tzinfo is not None and parsed.utcoffset() is not None
        else datetime.min.replace(tzinfo=UTC)
    )


def _safe_uuid(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text) else None


def _provider(model_name: str | None) -> str:
    if not model_name:
        return "unknown"
    lowered = model_name.casefold()
    if lowered.startswith("deepseek"):
        return "deepseek"
    if lowered.startswith("gpt"):
        return "gpt"
    return "unknown"


def _today_bounds(now: datetime) -> tuple[str, str]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Control Center sampling time must be timezone-aware")
    local_now = now.astimezone(SHANGHAI)
    start = datetime.combine(local_now.date(), time.min, tzinfo=SHANGHAI)
    end = start + timedelta(days=1)
    return (
        start.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        end.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
    )


def _empty_agents(*, historical_available: bool, reason: str) -> list[dict[str, Any]]:
    rows = []
    for role, label, family in AGENT_SPECS:
        rows.append(
            {
                "id": role,
                "label": label,
                "family": family,
                "current_status": "unknown",
                "heartbeat_connected": False,
                "status_note": "Phase 1 snapshot only; heartbeat not connected.",
                "historical_available": historical_available and role in RECORDED_AGENT_ROLES,
                "historical_model": None,
                "historical_provider": "unknown",
                "historical_last_run_at": None,
                "historical_status": None,
                "history_note": reason,
            }
        )
    return rows


def _project_metric_rows(reader: Any, start: str, end: str) -> dict[str, int]:
    params = {"project_id": getattr(reader, "_project_id", None)}
    active = reader._rows(
        "SELECT COUNT(*) AS value FROM dev_tasks "
        "WHERE project_id=:project_id AND state='active'",
        params,
    )
    completed = reader._rows(
        "SELECT COUNT(*) AS value FROM dev_tasks "
        "WHERE project_id=:project_id AND state='completed' "
        "AND completed_time>=:start AND completed_time<:end",
        {**params, "start": start, "end": end},
    )
    failed_blocked = reader._rows(
        "SELECT COUNT(*) AS value FROM dev_tasks "
        "WHERE project_id=:project_id AND state IN ('failed','blocked') "
        "AND updated_at>=:start AND updated_at<:end",
        {**params, "start": start, "end": end},
    )
    return {
        "active_tasks": _as_int(active[0].get("value") if active else None),
        "completed_today": _as_int(completed[0].get("value") if completed else None),
        "failed_blocked_today": _as_int(failed_blocked[0].get("value") if failed_blocked else None),
    }


def _project_agent_rows(reader: Any) -> list[dict[str, Any]]:
    rows = []
    for role in sorted(RECORDED_AGENT_ROLES):
        latest = reader._rows(
            "SELECT role, model_name, status, started_at, completed_at "
            "FROM agent_runs WHERE project_id=:project_id AND role=:role "
            "ORDER BY started_at DESC, id DESC LIMIT :limit",
            {
                "project_id": getattr(reader, "_project_id", None),
                "role": role,
                "limit": 1,
            },
        )
        if not latest:
            continue
        row = latest[0]
        model = _clean(row.get("model_name"), MODEL_LIMIT)
        started = _timestamp(row.get("started_at"))
        completed = _timestamp(row.get("completed_at"))
        rows.append(
            {
                "id": role,
                "model_name": model,
                "provider": _provider(model),
                "last_run_at": completed or started,
                "last_run_status": _clean(row.get("status"), 32, "unknown"),
            }
        )
    return rows


def _project_usage(reader: Any) -> dict[str, Any]:
    rows = reader._rows(
        "SELECT COUNT(*) AS total_rows, "
        "SUM(CASE WHEN input_tokens IS NOT NULL THEN 1 ELSE 0 END) AS input_known_rows, "
        "SUM(CASE WHEN output_tokens IS NOT NULL THEN 1 ELSE 0 END) AS output_known_rows, "
        "SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens "
        "FROM model_usage WHERE project_id=:project_id",
        {"project_id": getattr(reader, "_project_id", None)},
    )
    row = rows[0] if rows else {}
    total = _as_int(row.get("total_rows"))
    input_known = _as_int(row.get("input_known_rows"))
    output_known = _as_int(row.get("output_known_rows"))
    return {
        "total_rows": total,
        "input_known_rows": input_known,
        "output_known_rows": output_known,
        "input_unknown_rows": max(0, total - input_known),
        "output_unknown_rows": max(0, total - output_known),
        "input_tokens": _as_int(row.get("input_tokens")) if input_known else None,
        "output_tokens": _as_int(row.get("output_tokens")) if output_known else None,
    }


def _project_qa(reader: Any) -> dict[str, Any]:
    rows = reader._rows(
        "SELECT COUNT(*) AS run_rows, "
        "SUM(CASE WHEN lower(trim(status))='passed' THEN 1 ELSE 0 END) AS passed, "
        "SUM(CASE WHEN lower(trim(status))='failed' THEN 1 ELSE 0 END) AS failed, "
        "SUM(CASE WHEN lower(trim(status)) NOT IN ('passed','failed') THEN 1 ELSE 0 END) AS excluded "
        "FROM test_runs WHERE project_id=:project_id",
        {"project_id": getattr(reader, "_project_id", None)},
    )
    row = rows[0] if rows else {}
    passed = _as_int(row.get("passed"))
    failed = _as_int(row.get("failed"))
    denominator = passed + failed
    return {
        "run_rows": _as_int(row.get("run_rows")),
        "passed": passed,
        "failed": failed,
        "excluded": _as_int(row.get("excluded")),
        "pass_rate_percent": round((passed / denominator) * 100, 1) if denominator else None,
    }


def _project_knowledge(reader: Any) -> dict[str, Any]:
    rows = reader._rows(
        "SELECT "
        "SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active_count, "
        "SUM(CASE WHEN status='tombstoned' THEN 1 ELSE 0 END) AS tombstoned_count, "
        "MAX(indexed_at) AS latest_indexed_at "
        "FROM knowledge_items WHERE project_id=:project_id",
        {"project_id": getattr(reader, "_project_id", None)},
    )
    row = rows[0] if rows else {}
    return {
        "active_count": _as_int(row.get("active_count")),
        "tombstoned_count": _as_int(row.get("tombstoned_count")),
        "latest_indexed_at": _timestamp(row.get("latest_indexed_at")),
    }


def _project_recent_tasks(reader: Any, key: str) -> list[dict[str, Any]]:
    rows = reader._rows(
        "SELECT id, state, requirement_summary, assigned_agent, updated_at, completed_time "
        "FROM dev_tasks WHERE project_id=:project_id "
        "ORDER BY updated_at DESC, id DESC LIMIT :limit",
        {
            "project_id": getattr(reader, "_project_id", None),
            "limit": RECENT_LIMIT,
        },
    )
    result = []
    for row in rows:
        result.append(
            {
                "project": key,
                "task_id": _safe_uuid(row.get("id")),
                "summary": _clean(row.get("requirement_summary"), SUMMARY_LIMIT, "No summary recorded") or "No summary recorded",
                "state": (_clean(row.get("state"), 32, "unknown") or "unknown").lower(),
                "assigned_agent": _clean(row.get("assigned_agent"), ASSIGNED_AGENT_LIMIT),
                "updated_at": _timestamp(row.get("updated_at")),
                "completed_at": _timestamp(row.get("completed_time")),
            }
        )
    return result


def _collect_project(reader: Any, key: str, start: str, end: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": PROJECT_LABELS[key],
        "project_id": getattr(reader, "_project_id", None),
        "status": "known",
        "metrics": _project_metric_rows(reader, start, end),
        "agents": _project_agent_rows(reader),
        "usage": _project_usage(reader),
        "qa": _project_qa(reader),
        "knowledge": _project_knowledge(reader),
        "recent_tasks": _project_recent_tasks(reader, key),
        "error_code": None,
    }


def _empty_project(key: str, code: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": PROJECT_LABELS[key],
        "project_id": None,
        "status": "error",
        "metrics": {
            "active_tasks": None,
            "completed_today": None,
            "failed_blocked_today": None,
        },
        "agents": [],
        "usage": {
            "total_rows": None,
            "input_known_rows": None,
            "output_known_rows": None,
            "input_unknown_rows": None,
            "output_unknown_rows": None,
            "input_tokens": None,
            "output_tokens": None,
        },
        "qa": {
            "run_rows": None,
            "passed": None,
            "failed": None,
            "excluded": None,
            "pass_rate_percent": None,
        },
        "knowledge": {
            "active_count": None,
            "tombstoned_count": None,
            "latest_indexed_at": None,
        },
        "recent_tasks": [],
        "error_code": code,
    }


def _sum_known(values: list[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    if not known:
        return None
    return sum(int(value) for value in known)


def _aggregate_projects(projects: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [row for row in projects if row["status"] == "error"]
    if errors:
        return {
            "metrics": {
                "active_tasks": None,
                "completed_today": None,
                "failed_blocked_today": None,
            },
            "usage": {
                "total_rows": None,
                "input_known_rows": None,
                "output_known_rows": None,
                "input_unknown_rows": None,
                "output_unknown_rows": None,
                "input_tokens": None,
                "output_tokens": None,
            },
            "qa": {
                "run_rows": None,
                "passed": None,
                "failed": None,
                "excluded": None,
                "pass_rate_percent": None,
            },
            "knowledge": {
                "active_count": None,
                "tombstoned_count": None,
                "latest_indexed_at": None,
            },
        }

    passed = sum(row["qa"]["passed"] for row in projects)
    failed = sum(row["qa"]["failed"] for row in projects)
    denominator = passed + failed
    input_tokens = [row["usage"]["input_tokens"] for row in projects]
    output_tokens = [row["usage"]["output_tokens"] for row in projects]
    latest = [
        _parsed_timestamp(row["knowledge"]["latest_indexed_at"])
        for row in projects
        if row["knowledge"]["latest_indexed_at"] is not None
    ]
    latest_index = max(latest) if latest else None
    return {
        "metrics": {
            name: sum(row["metrics"][name] for row in projects)
            for name in ("active_tasks", "completed_today", "failed_blocked_today")
        },
        "usage": {
            "total_rows": sum(row["usage"]["total_rows"] for row in projects),
            "input_known_rows": sum(row["usage"]["input_known_rows"] for row in projects),
            "output_known_rows": sum(row["usage"]["output_known_rows"] for row in projects),
            "input_unknown_rows": sum(row["usage"]["input_unknown_rows"] for row in projects),
            "output_unknown_rows": sum(row["usage"]["output_unknown_rows"] for row in projects),
            "input_tokens": _sum_known(input_tokens),
            "output_tokens": _sum_known(output_tokens),
        },
        "qa": {
            "run_rows": sum(row["qa"]["run_rows"] for row in projects),
            "passed": passed,
            "failed": failed,
            "excluded": sum(row["qa"]["excluded"] for row in projects),
            "pass_rate_percent": round((passed / denominator) * 100, 1) if denominator else None,
        },
        "knowledge": {
            "active_count": sum(row["knowledge"]["active_count"] for row in projects),
            "tombstoned_count": sum(row["knowledge"]["tombstoned_count"] for row in projects),
            "latest_indexed_at": (
                latest_index.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
                if latest_index
                else None
            ),
        },
    }


def _aggregate_agents(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    complete = all(row["status"] == "known" for row in projects)
    if not complete:
        return _empty_agents(
            historical_available=False,
            reason="Project source is incomplete; historical run metadata is unavailable.",
        )
    historical: dict[str, dict[str, Any]] = {}
    for project in projects:
        for row in project["agents"]:
            previous = historical.get(row["id"])
            if previous is None or _parsed_timestamp(row["last_run_at"]) > _parsed_timestamp(previous["last_run_at"]):
                historical[row["id"]] = row
    result = []
    for role, label, family in AGENT_SPECS:
        row = historical.get(role)
        result.append(
            {
                "id": role,
                "label": label,
                "family": family,
                "current_status": "unknown",
                "heartbeat_connected": False,
                "status_note": "Phase 1 snapshot only; heartbeat not connected.",
                "historical_available": row is not None,
                "historical_model": row["model_name"] if row else None,
                "historical_provider": row["provider"] if row else "unknown",
                "historical_last_run_at": row["last_run_at"] if row else None,
                "historical_status": row["last_run_status"] if row else None,
                "history_note": (
                    "Latest recorded agent_run; not evidence of current activity."
                    if row
                    else "No recorded run in the selected project scope."
                ),
            }
        )
    return result


def build_snapshot(readers: Mapping[str, KnowledgeReader], now: datetime) -> dict[str, Any]:
    """Build one pure, read-only snapshot from project-bound readers."""

    selected = tuple(key for key in PROJECT_KEYS if key in readers)
    if not selected:
        raise ValueError("At least one registered project reader is required")
    start, end = _today_bounds(now)
    projects = []
    for key in selected:
        reader = readers[key]
        try:
            projects.append(_collect_project(reader, key, start, end))
        except Exception:
            projects.append(_empty_project(key, "CONTROL_DATA_UNAVAILABLE"))

    totals = _aggregate_projects(projects)
    errors = [
        {"project": row["key"], "code": row["error_code"]}
        for row in projects
        if row["status"] == "error"
    ]
    known_count = sum(row["status"] == "known" for row in projects)
    if errors and known_count:
        data_status = "partial"
    elif errors:
        data_status = "error"
    else:
        data_status = "known"

    recent = [
        item
        for project in projects
        if project["status"] == "known"
        for item in project["recent_tasks"]
    ]
    recent.sort(
        key=lambda item: (_parsed_timestamp(item["updated_at"]), item["task_id"] or ""),
        reverse=True,
    )

    return {
        "schema": "lads-control-center-stage1-v1",
        "stage": 1,
        "read_only": True,
        "timezone": "Asia/Shanghai",
        "sampled_at": now.astimezone(SHANGHAI).isoformat(timespec="seconds"),
        "project_filter": selected[0] if len(selected) == 1 else "all",
        "data_status": data_status,
        "known_project_count": known_count,
        "selected_project_count": len(projects),
        "errors": errors,
        "metrics": {
            **totals["metrics"],
            "today_definition": "Asia/Shanghai calendar day; failed/blocked uses last task update time.",
        },
        "tokens": {
            **totals["usage"],
            "semantics": "historical_recorded_incomplete",
            "cost": "unknown",
            "cost_note": "No billing estimate is produced; nullable cost_usd is not invoice data.",
        },
        "qa": {
            **totals["qa"],
            "semantics": "test_run_rate",
            "note": "test_runs passed / (passed + failed); unknown states excluded; no individual-case count.",
        },
        "knowledge": totals["knowledge"],
        "agents": _aggregate_agents(projects),
        "repositories": [],
        "recent_tasks": recent[:RECENT_LIMIT],
        "recent_complete": not errors,
        "projects": projects,
        "sources": [
            {
                "key": row["key"],
                "label": row["label"],
                "project_id": row["project_id"],
                "status": row["status"],
                "database": "dual-repository.sqlite3",
            }
            for row in projects
        ],
        "limitations": list(LIMITATIONS),
    }


def _bounded_git(root: Path, *arguments: str) -> str | None:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(
            [
                "git",
                "--no-pager",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(root),
                *arguments,
            ],
            capture_output=True,
            timeout=4,
            check=False,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or len(result.stdout.encode("utf-8", "replace")) > 2048:
        return None
    return result.stdout.strip()


def _repository_summary(key: str, registry: ProjectRegistry | None, sampled_at: str) -> dict[str, Any]:
    if registry is None:
        return {
            "project": key,
            "label": PROJECT_LABELS[key],
            "repository": PROJECT_REPOSITORIES[key],
            "local_available": False,
            "branch": None,
            "head": None,
            "remote_configured": None,
            "remote_status": "unknown",
            "last_checked_at": sampled_at,
        }
    try:
        project = registry.project(key)
    except RegistryError:
        project = None
    if project is None:
        return {
            "project": key,
            "label": PROJECT_LABELS[key],
            "repository": PROJECT_REPOSITORIES[key],
            "local_available": False,
            "branch": None,
            "head": None,
            "remote_configured": None,
            "remote_status": "unknown",
            "last_checked_at": sampled_at,
        }
    head = _bounded_git(project.root, "rev-parse", "HEAD")
    if head is not None and re.fullmatch(r"[0-9a-f]{40,64}", head) is None:
        head = None
    branch = _bounded_git(project.root, "branch", "--show-current")
    branch = _clean(branch, 120) if branch else None
    remote = _bounded_git(project.root, "remote", "get-url", "origin")
    expected_remote = f"https://github.com/{project.repository}.git"
    return {
        "project": key,
        "label": PROJECT_LABELS[key],
        "repository": project.repository,
        "local_available": head is not None,
        "branch": branch,
        "head": head,
        "remote_configured": remote == expected_remote if remote is not None else None,
        "remote_status": "unknown",
        "last_checked_at": sampled_at,
    }


class SnapshotService:
    """Canonical read-only service over the fixed project registry."""

    def __init__(
        self,
        *,
        registry: ProjectRegistry | None = None,
        readers: Mapping[str, KnowledgeReader] | None = None,
        root: Path | None = None,
        now_provider: Callable[[], datetime] | None = None,
        include_git: bool = True,
    ) -> None:
        self.root = Path(root) if root is not None else ROOT
        self._registry = registry
        self._injected_readers = dict(readers) if readers is not None else None
        self._now_provider = now_provider or (lambda: datetime.now(SHANGHAI))
        self._include_git = include_git
        self._registry_error = False

    def _load_registry(self) -> ProjectRegistry | None:
        if self._registry is not None:
            return self._registry
        try:
            self._registry = ProjectRegistry.load(
                self.root / "config" / "project-registry.json",
                base=self.root.parent,
                workspace=self.root.parent / "LADS-Workspace",
            )
        except Exception:
            self._registry_error = True
            self._registry = None
        return self._registry

    def snapshot(self, project: str = "all") -> dict[str, Any]:
        if project not in {"all", *PROJECT_KEYS}:
            raise ValueError("Invalid project filter")
        selected = PROJECT_KEYS if project == "all" else (project,)
        registry = self._load_registry()
        readers: dict[str, Any] = {}
        owned: list[KnowledgeReader] = []
        for key in selected:
            injected = self._injected_readers.get(key) if self._injected_readers is not None else None
            if injected is not None:
                readers[key] = injected
                continue
            if registry is None:
                readers[key] = _UnavailableReader()
                continue
            try:
                reader = KnowledgeReader.open(
                    registry.knowledge_home(),
                    "dual-repository.sqlite3",
                    project_id=registry.project(key).project_id,
                )
            except Exception:
                readers[key] = _UnavailableReader()
            else:
                readers[key] = reader
                owned.append(reader)
        try:
            now = self._now_provider()
            snapshot = build_snapshot(readers, now)
            sampled_at = snapshot["sampled_at"]
            if self._include_git:
                snapshot["repositories"] = [
                    _repository_summary(key, registry, sampled_at) for key in selected
                ]
            else:
                snapshot["repositories"] = [
                    _repository_summary(key, None, sampled_at) for key in selected
                ]
            if self._registry_error:
                snapshot["limitations"].append(
                    "The fixed project registry could not be loaded; project scope remains unavailable."
                )
            return snapshot
        finally:
            for reader in owned:
                reader.close()
