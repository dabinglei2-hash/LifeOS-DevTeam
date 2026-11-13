"""Evidence-bound GitHub publication for reviewed LADS engineering changes.

This module owns release orchestration, not product data. It starts only from a
validated Stage 3 ``DELIVERY_READY`` journal, copies approved bytes into a
private release task directory, commits in an isolated worktree, and may enable
native GitHub auto-merge. It never force-pushes, merges immediately, changes
repository protection, or reads credential values.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import quote

from lads_automation import delivery, qa, unit_qa
from lads_automation.evidence import validate_stage_acceptance
from lads_automation.state import (
    FileJournal,
    JournalError,
)
from lads_automation.team import BASE
from lads_knowledge.database import _is_reparse, _reject_reparse_chain
from lads_knowledge.queries import KnowledgeReader
from lads_knowledge.safety import canonical_json, sanitize_metadata, sanitize_text
from lads_knowledge.service import KnowledgeStore


CANONICAL_REPOSITORY = "dabinglei2-hash/LifeOS"
APPROVED_REPOSITORIES = frozenset({CANONICAL_REPOSITORY, "dabinglei2-hash/LifeOS-DevTeam"})
CANONICAL_OWNER = "dabinglei2-hash"
DEFAULT_BASE_BRANCH = "main"
AUTHORIZATION_SCHEMA = "lads-github-release-authorization-v1"
RELEASE_SCHEMA = "lads-github-release-state-v1"
RELEASE_MARKER_FORMAT = "lads-github-release-home-v1"
RELEASE_APPLICATION_ID = "LifeOS-LADS-github-release"
STAGE3_WORKER_PROVIDER = "lifeos_deepseek"
STAGE3_WORKER_MODEL = "deepseek-flash"
STAGE3_WORKER_ROLES = frozenset({"scanner", "reviewer", "builder", "qa-tester"})
KNOWN_SYNTHETIC_FIXTURE_SHA256 = (
    "defa8d583c51f7c8e48e88a918a7e852400aba72305165bc563e4f204d5d4fc9"
)
KNOWN_AUDIT_EXEMPTIONS = frozenset(
    {
        (
            "tests/knowledge/test_indexer.py",
            KNOWN_SYNTHETIC_FIXTURE_SHA256,
            "credential_or_key_material",
        ),
        (
            "docs/LADS_AUTOMATION.md",
            "ef6d071f36fb69c0ac2035ffce9141bd1dde3935b9ed20fed5c08b8f904c35c5",
            "history_rewrite_or_force_command",
        ),
        (
            "engineering/lads-stage3-install/tests/lads/test_orchestrator.py",
            "3b971770d38f7af2e3a63f23ab7ee4095676d12b35516b44b0afc943011eca5c",
            "credential_or_key_material",
        ),
        (
            "lads_automation/hooks.py",
            "37b4bb767b75a23b0513fa7e74108234e3f0192e0146e6ca2008e82ea78f92a7",
            "history_rewrite_or_force_command",
        ),
        (
            "tests/automation/test_hooks.py",
            "d9f12ef556ce1b568cad09f8226d0007863e368b8782ddf0a9455204634368bc",
            "history_rewrite_or_force_command",
        ),
        (
            "tests/automation/test_rpc.py",
            "ff2e400560d1aee09003c27b22f0adc8153f4c1bdca1346ea4b4257359754273",
            "credential_or_key_material",
        ),
        (
            "tests/lads/test_orchestrator.py",
            "241d4e445bdd8eee9f54563c361a8b6f42dd41f050f84d0c6243948c24017742",
            "credential_or_key_material",
        ),
    }
)

MAX_AUTHORIZATION_BYTES = 2 * 1024 * 1024
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_EVENTS_BYTES = 8 * 1024 * 1024
MAX_RELEASE_EVENTS = 4096
MAX_STORED_NETWORK_FAILURES = 256
MAX_NETWORK_FAILURE_TOTAL = 4096
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_COMMAND_OUTPUT = 2 * 1024 * 1024
MAX_JSON_RESPONSE = 8 * 1024 * 1024
MAX_RETRIES = 5
MAX_POLL_ATTEMPTS = 40
MAX_PROTECTED_CHECKS = 50
MAX_SCAN_BYTES = 16 * 1024 * 1024

CANONICAL_ORIGIN_URLS = frozenset(
    {
        f"https://github.com/{CANONICAL_REPOSITORY}.git",
        f"https://github.com/{CANONICAL_REPOSITORY}",
    }
)
LEGACY_ORIGIN_URLS = frozenset(
    {
        "https://github.com/dabinglei2-hash/-.git",
        "https://github.com/dabinglei2-hash/-",
    }
)
RELEASE_STATUSES = frozenset(
    {
        "INITIALIZED",
        "NETWORK_RETRY",
        "LOCAL_GATE_VERIFIED",
        "COMMIT_INTENT",
        "COMMITTED",
        "PUSH_INTENT",
        "PUSHED",
        "PUSH_PENDING",
        "PR_INTENT",
        "PR_PENDING",
        "PR_OPEN",
        "SUCCESS",
        "WAITING_CI",
        "CI_FAILED",
        "CONTROLLER_STATUS_INTENT",
        "CONTROLLER_STATUS_READY",
        "AUTO_MERGE_INTENT",
        "AUTO_MERGE_ENABLED",
        "AWAITING_APPROVAL",
        "MERGED_INDEX_PENDING",
        "MERGED",
        "BLOCKED",
    }
)
STAGE_METADATA_FIELDS = frozenset(
    {
        "role",
        "provider",
        "model",
        "sandbox",
        "start_time",
        "end_time",
        "input_tokens",
        "output_tokens",
        "config_snapshot_sha256",
        "home_mode",
        "home_sha256",
        "authority_sha256",
    }
)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MODE_RE = re.compile(r"^(?:100644|100755)$")
_RISK_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class ReleaseError(RuntimeError):
    """Base class for a release operation that failed closed."""


class ReleaseAuthorizationError(ReleaseError):
    """The controller authorization is malformed or does not bind the change."""


class ReleaseGateError(ReleaseError):
    """The local Stage 3 delivery evidence is not release-ready."""


class ReleaseBoundaryError(ReleaseError):
    """A private state or repository path crossed its approved boundary."""


class ReleaseStateNotFound(ReleaseBoundaryError):
    """Only the exact owned state file is absent; prior artifacts may remain."""


class ReleaseConflict(ReleaseError):
    """Durable release state conflicts with an actual local or remote result."""


class GitHubTransportError(ReleaseError):
    """A GitHub transport failed with a bounded, nonsecret error."""


class GitHubTransientError(GitHubTransportError):
    """A bounded read request may be retried."""

    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class ReleaseBlocked(ReleaseError):
    """The state machine reached a durable stop requiring controller action."""

    def __init__(self, message: str, *, state: str = "BLOCKED", reason: str | None = None) -> None:
        super().__init__(message)
        self.state = state
        self.reason = reason or message


def _canonical(value: Any) -> str:
    return canonical_json(value)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_json_file(path: Path, maximum: int, label: str) -> dict[str, Any]:
    if (
        not path.is_absolute()
        or _is_reparse(path)
        or path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > maximum
    ):
        raise ReleaseAuthorizationError(f"{label} is missing, unsafe, or oversized")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ReleaseAuthorizationError(f"{label} contains duplicate keys")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_bytes().decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ReleaseAuthorizationError(f"{label} contains non-JSON numbers")
            ),
        )
    except (UnicodeError, ValueError) as exc:
        raise ReleaseAuthorizationError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ReleaseAuthorizationError(f"{label} root must be an object")
    return value


def _safe_relative(value: Any, label: str, *, maximum: int = 300) -> str:
    if not isinstance(value, str):
        raise ReleaseAuthorizationError(f"{label} must be a string")
    text = value.strip()
    if (
        not text
        or len(text) > maximum
        or "\x00" in text
        or "\\" in text
        or text.startswith("/")
        or any(part in {"", ".", ".."} for part in text.split("/"))
        or re.match(r"^[A-Za-z]:", text)
    ):
        raise ReleaseAuthorizationError(f"{label} must be a contained POSIX relative path")
    return text


def _sha(value: Any, label: str, *, allow_none: bool = False, length: int = 64) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        raise ReleaseAuthorizationError(f"{label} must be a SHA")
    text = value.lower()
    pattern = _SHA64_RE if length == 64 else _SHA40_RE
    if text != value or not pattern.fullmatch(text):
        raise ReleaseAuthorizationError(f"{label} has an invalid SHA")
    return text


def _bounded_text(value: Any, label: str, *, maximum: int = 8000) -> str:
    if not isinstance(value, str):
        raise ReleaseAuthorizationError(f"{label} must be text")
    cleaned = sanitize_text(value, limit=maximum, allow_empty=False)
    if not cleaned or len(cleaned) > maximum:
        raise ReleaseAuthorizationError(f"{label} is empty or too long")
    return cleaned


def _assert_finite_json(value: Any, label: str, *, depth: int = 0) -> None:
    if depth > 64:
        raise ReleaseBoundaryError(f"{label} is nested too deeply")
    if value is None or type(value) in {bool, int, str}:
        return
    if type(value) is float:
        if value != value or value in {float("inf"), float("-inf")}:
            raise ReleaseBoundaryError(f"{label} contains a non-finite number")
        return
    if isinstance(value, list):
        if len(value) > 100_000:
            raise ReleaseBoundaryError(f"{label} contains an oversized list")
        for item in value:
            _assert_finite_json(item, label, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 10_000:
            raise ReleaseBoundaryError(f"{label} contains an oversized object")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 1000:
                raise ReleaseBoundaryError(f"{label} contains an invalid object key")
            _assert_finite_json(item, label, depth=depth + 1)
        return
    raise ReleaseBoundaryError(f"{label} contains an unsupported JSON value")


def _strict_private_json_bytes(data: bytes, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ReleaseBoundaryError(f"{label} contains duplicate keys")
            result[key] = value
        return result

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ReleaseBoundaryError(f"{label} contains non-JSON numbers")
            ),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseBoundaryError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ReleaseBoundaryError(f"{label} root must be an object")
    _assert_finite_json(value, label)
    return value


def _validate_state_shape(value: Mapping[str, Any]) -> None:
    task_id = value.get("task_id")
    status = value.get("status")
    if (
        value.get("schema") != RELEASE_SCHEMA
        or not isinstance(task_id, str)
        or not _UUID_RE.fullmatch(task_id)
        or not isinstance(status, str)
        or status not in RELEASE_STATUSES
        or type(value.get("ready")) is not bool
    ):
        raise ReleaseBoundaryError("Release state shape or status is invalid")
    failures = value.get("network_failures", [])
    if (
        not isinstance(failures, list)
        or len(failures) > MAX_STORED_NETWORK_FAILURES
        or any(not isinstance(item, dict) for item in failures)
    ):
        raise ReleaseBoundaryError("Release network failure history is malformed")
    total = value.get("network_failure_total", 0)
    if type(total) is not int or not 0 <= total <= MAX_NETWORK_FAILURE_TOTAL:
        raise ReleaseBoundaryError("Release network failure counter is invalid")
    if "network_failure_total" in value and total < len(failures):
        raise ReleaseBoundaryError("Release network failure counter loses history")


def _validate_events_shape(value: Mapping[str, Any]) -> None:
    if set(value) != {"version", "events"} or value.get("version") != 1:
        raise ReleaseBoundaryError("Release event log shape is invalid")
    rows = value.get("events")
    if not isinstance(rows, list) or len(rows) > MAX_RELEASE_EVENTS:
        raise ReleaseBoundaryError("Release event log is oversized or malformed")
    identifiers: set[str] = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"id", "task_id", "event_type", "payload", "occurred_at"}
            or not isinstance(row.get("id"), str)
            or not _UUID_RE.fullmatch(row["id"])
            or not isinstance(row.get("task_id"), str)
            or not _UUID_RE.fullmatch(row["task_id"])
            or not isinstance(row.get("event_type"), str)
            or not _RISK_RE.fullmatch(row["event_type"])
            or not isinstance(row.get("payload"), dict)
            or not isinstance(row.get("occurred_at"), str)
            or not row["occurred_at"]
            or len(row["occurred_at"]) > 100
            or row["id"] in identifiers
        ):
            raise ReleaseBoundaryError("Release event row is malformed or duplicated")
        identifiers.add(row["id"])


@dataclass(frozen=True)
class FileChange:
    path: str
    source_before_sha256: str | None
    git_before_sha256: str | None
    after_sha256: str
    mode: str = "100644"

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _safe_relative(self.path, "change path"))
        object.__setattr__(
            self,
            "source_before_sha256",
            _sha(
                self.source_before_sha256,
                "source before SHA",
                allow_none=True,
            ),
        )
        object.__setattr__(
            self,
            "git_before_sha256",
            _sha(self.git_before_sha256, "Git before SHA", allow_none=True),
        )
        object.__setattr__(self, "after_sha256", _sha(self.after_sha256, "after SHA"))
        if not _MODE_RE.fullmatch(self.mode):
            raise ReleaseAuthorizationError("Only regular 0644/0755 Git modes are allowed")

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "source_before_sha256": self.source_before_sha256,
            "git_before_sha256": self.git_before_sha256,
            "after_sha256": self.after_sha256,
            "mode": self.mode,
        }


@dataclass(frozen=True)
class Deletion:
    path: str
    source_before_sha256: str
    git_before_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _safe_relative(self.path, "deletion path"))
        object.__setattr__(
            self,
            "source_before_sha256",
            _sha(self.source_before_sha256, "deletion source SHA"),
        )
        object.__setattr__(
            self,
            "git_before_sha256",
            _sha(self.git_before_sha256, "deletion Git SHA"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "source_before_sha256": self.source_before_sha256,
            "git_before_sha256": self.git_before_sha256,
        }


@dataclass(frozen=True)
class ReleaseAuthorization:
    task_id: str
    source_head: str
    candidate_sha256: str
    repository: str
    base_branch: str
    base_sha: str
    short_description: str
    demand: str
    summary: str
    reason: str
    risk_flags: tuple[str, ...]
    candidate_files: tuple[FileChange, ...]
    prerequisite_files: tuple[FileChange, ...]
    deletions: tuple[Deletion, ...]
    binary_assets: tuple[tuple[str, str], ...]
    authorization_sha256: str = ""

    @property
    def branch(self) -> str:
        return f"codex/{self.task_id[:8]}-{self.short_description}"

    @property
    def changes(self) -> tuple[FileChange, ...]:
        return (*self.candidate_files, *self.prerequisite_files)

    @property
    def all_paths(self) -> tuple[str, ...]:
        return tuple(sorted((*(item.path for item in self.changes), *(item.path for item in self.deletions))))

    def as_dict(self) -> dict[str, Any]:
        body = {
            "schema": AUTHORIZATION_SCHEMA,
            "task_id": self.task_id,
            "source_head": self.source_head,
            "candidate_sha256": self.candidate_sha256,
            "repository": self.repository,
            "base_branch": self.base_branch,
            "base_sha": self.base_sha,
            "short_description": self.short_description,
            "demand": self.demand,
            "summary": self.summary,
            "reason": self.reason,
            "risk_flags": list(self.risk_flags),
            "candidate_files": [item.as_dict() for item in self.candidate_files],
            "prerequisite_files": [item.as_dict() for item in self.prerequisite_files],
            "deletions": [item.as_dict() for item in self.deletions],
            "binary_assets": [
                {"path": path, "sha256": sha256}
                for path, sha256 in self.binary_assets
            ],
        }
        if self.authorization_sha256:
            body["authorization_sha256"] = self.authorization_sha256
        return body


def _file_changes(value: Any, label: str) -> tuple[FileChange, ...]:
    if not isinstance(value, list) or len(value) > 512:
        raise ReleaseAuthorizationError(f"{label} must be a bounded list")
    changes: list[FileChange] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "path",
            "source_before_sha256",
            "git_before_sha256",
            "after_sha256",
            "mode",
        }:
            raise ReleaseAuthorizationError(f"{label} entry is malformed")
        changes.append(
            FileChange(
                path=item["path"],
                source_before_sha256=item["source_before_sha256"],
                git_before_sha256=item["git_before_sha256"],
                after_sha256=item["after_sha256"],
                mode=item["mode"],
            )
        )
    paths = [item.path for item in changes]
    if len(paths) != len(set(paths)):
        raise ReleaseAuthorizationError(f"{label} contains duplicate paths")
    return tuple(sorted(changes, key=lambda item: item.path))


def parse_authorization(value: Mapping[str, Any], *, expected_task_id: str | None = None) -> ReleaseAuthorization:
    expected_keys = {
        "schema",
        "task_id",
        "source_head",
        "candidate_sha256",
        "repository",
        "base_branch",
        "base_sha",
        "short_description",
        "demand",
        "summary",
        "reason",
        "risk_flags",
        "candidate_files",
        "prerequisite_files",
        "deletions",
        "binary_assets",
    }
    if set(value) - (expected_keys | {"authorization_sha256"}):
        raise ReleaseAuthorizationError("Authorization contains unknown fields")
    if value.get("schema") != AUTHORIZATION_SCHEMA:
        raise ReleaseAuthorizationError("Authorization schema is not supported")
    task_id = value.get("task_id")
    if not isinstance(task_id, str) or not _UUID_RE.fullmatch(task_id):
        raise ReleaseAuthorizationError("Authorization task_id must be a canonical UUID")
    if expected_task_id is not None and task_id != expected_task_id:
        raise ReleaseAuthorizationError("Authorization task_id does not match the requested task")
    source_head = _sha(value.get("source_head"), "source_head", length=40)
    candidate = _sha(value.get("candidate_sha256"), "candidate_sha256")
    repository = value.get("repository")
    if repository not in APPROVED_REPOSITORIES:
        raise ReleaseAuthorizationError("Authorization must bind one of the two approved repositories")
    base_branch = value.get("base_branch")
    if base_branch != DEFAULT_BASE_BRANCH:
        raise ReleaseAuthorizationError("Authorization must use the reviewed main base branch")
    base_sha = _sha(value.get("base_sha"), "base_sha", length=40)
    slug = value.get("short_description")
    if not isinstance(slug, str) or len(slug) > 48 or not _SLUG_RE.fullmatch(slug):
        raise ReleaseAuthorizationError("short_description must be a strict lowercase slug")
    risk_flags = value.get("risk_flags")
    if (
        not isinstance(risk_flags, list)
        or len(risk_flags) > 32
        or len(risk_flags) != len(set(risk_flags))
        or any(not isinstance(item, str) or not _RISK_RE.fullmatch(item) for item in risk_flags)
    ):
        raise ReleaseAuthorizationError("risk_flags are malformed or duplicated")
    candidate_files = _file_changes(value.get("candidate_files"), "candidate_files")
    prerequisites = _file_changes(value.get("prerequisite_files"), "prerequisite_files")
    overlap = {item.path for item in candidate_files} & {item.path for item in prerequisites}
    if overlap:
        raise ReleaseAuthorizationError("Candidate and prerequisite paths overlap")
    raw_deletions = value.get("deletions")
    if not isinstance(raw_deletions, list) or len(raw_deletions) > 128:
        raise ReleaseAuthorizationError("deletions must be a bounded list")
    deletions: list[Deletion] = []
    for item in raw_deletions:
        if not isinstance(item, dict) or set(item) != {
            "path",
            "source_before_sha256",
            "git_before_sha256",
        }:
            raise ReleaseAuthorizationError("Deletion entry is malformed")
        deletions.append(
            Deletion(
                item["path"],
                item["source_before_sha256"],
                item["git_before_sha256"],
            )
        )
    deletion_paths = [item.path for item in deletions]
    if len(deletion_paths) != len(set(deletion_paths)):
        raise ReleaseAuthorizationError("Deletion paths are duplicated")
    if set(deletion_paths) & ({item.path for item in candidate_files} | {item.path for item in prerequisites}):
        raise ReleaseAuthorizationError("Deletion path also carries replacement bytes")
    all_paths = [
        *(item.path for item in candidate_files),
        *(item.path for item in prerequisites),
        *deletion_paths,
    ]
    if len(all_paths) != len({path.casefold() for path in all_paths}):
        raise ReleaseAuthorizationError("Authorization contains case-colliding paths")
    raw_assets = value.get("binary_assets")
    if not isinstance(raw_assets, list) or len(raw_assets) > 128:
        raise ReleaseAuthorizationError("binary_assets must be a bounded list")
    assets: list[tuple[str, str]] = []
    for item in raw_assets:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ReleaseAuthorizationError("Binary asset entry is malformed")
        assets.append(
            (
                _safe_relative(item["path"], "binary asset path"),
                _sha(item["sha256"], "binary asset SHA") or "",
            )
        )
    if len(assets) != len({path for path, _sha256 in assets}):
        raise ReleaseAuthorizationError("Binary asset paths are duplicated")
    change_paths = {item.path for item in (*candidate_files, *prerequisites)}
    if any(path not in change_paths for path, _sha256 in assets):
        raise ReleaseAuthorizationError("Every binary asset must be an exact change")
    authorization = ReleaseAuthorization(
        task_id=task_id,
        source_head=source_head or "",
        candidate_sha256=candidate or "",
        repository=repository,
        base_branch=base_branch,
        base_sha=base_sha or "",
        short_description=slug,
        demand=_bounded_text(value.get("demand"), "demand", maximum=2000),
        summary=_bounded_text(value.get("summary"), "summary", maximum=4000),
        reason=_bounded_text(value.get("reason"), "reason", maximum=2000),
        risk_flags=tuple(sorted(risk_flags)),
        candidate_files=candidate_files,
        prerequisite_files=prerequisites,
        deletions=tuple(sorted(deletions, key=lambda item: item.path)),
        binary_assets=tuple(sorted(assets)),
    )
    body = authorization.as_dict()
    actual = _digest(body)
    supplied = value.get("authorization_sha256")
    if supplied is not None and supplied != actual:
        raise ReleaseAuthorizationError("Authorization digest does not bind its canonical fields")
    return replace(authorization, authorization_sha256=actual)


def load_authorization(path: str | Path, *, expected_task_id: str | None = None) -> ReleaseAuthorization:
    selected = Path(path)
    if not selected.is_absolute():
        raise ReleaseAuthorizationError("Authorization path must be absolute")
    try:
        _reject_reparse_chain(selected)
    except Exception as exc:
        raise ReleaseAuthorizationError("Authorization path traverses a link or reparse point") from exc
    value = _strict_json_file(selected.resolve(strict=True), MAX_AUTHORIZATION_BYTES, "Authorization")
    return parse_authorization(value, expected_task_id=expected_task_id)


def default_release_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local).resolve(strict=False) / "LifeOS-LADS" / "releases"
    return Path.home().resolve(strict=False) / ".local" / "share" / "lifeos-lads" / "releases"


class _ReleaseLock:
    def __init__(self, path: Path, timeout: float = 10.0) -> None:
        self.path = path
        self.timeout = timeout
        self.handle: Any = None
        self.windows = os.name == "nt"

    def __enter__(self) -> "_ReleaseLock":
        self.handle = self.path.open("a+b")
        if self.handle.tell() == 0:
            self.handle.write(b"\0")
            self.handle.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if self.windows:
                    import msvcrt

                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.handle.close()
                    self.handle = None
                    raise ReleaseConflict("Release task lock timed out")
                time.sleep(0.025)

    def __exit__(self, *_args: Any) -> None:
        assert self.handle is not None
        try:
            if self.windows:
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


class ReleaseStore:
    """Durable private release state under ``LOCALAPPDATA/LifeOS-LADS``."""

    def __init__(self, root: str | Path | None = None) -> None:
        selected = Path(root) if root is not None else default_release_root()
        if not selected.is_absolute():
            raise ReleaseBoundaryError("Release root must be absolute")
        self.root = selected.resolve(strict=False)

    def initialize(self) -> "ReleaseStore":
        try:
            _reject_reparse_chain(self.root)
        except Exception as exc:
            raise ReleaseBoundaryError("Release root traverses a reparse point") from exc
        if self.root.exists():
            if not self.root.is_dir() or _is_reparse(self.root):
                raise ReleaseBoundaryError("Release root is not a private directory")
            if any(self.root.iterdir()) and not (self.root / ".lads-release.json").exists():
                raise ReleaseBoundaryError("Nonempty release root is not LADS-owned")
        else:
            self.root.mkdir(parents=True, exist_ok=False)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        marker = self.root / ".lads-release.json"
        marker_body = {"application_id": RELEASE_APPLICATION_ID, "format": RELEASE_MARKER_FORMAT}
        if marker.exists():
            if _is_reparse(marker) or marker.is_symlink() or not marker.is_file():
                raise ReleaseBoundaryError("Release ownership marker is unsafe")
            current = _strict_json_file(marker, 4096, "Release marker")
            if current != marker_body:
                raise ReleaseBoundaryError("Release root belongs to a different owner")
        else:
            self._atomic_write(marker, (_canonical(marker_body) + "\n").encode("utf-8"))
        ignore = self.root / ".gitignore"
        if ignore.exists():
            if _is_reparse(ignore) or ignore.is_symlink() or ignore.read_bytes() != b"*\n":
                raise ReleaseBoundaryError("Release deny-all .gitignore is invalid")
        else:
            self._atomic_write(ignore, b"*\n")
        tasks = self.root / "tasks"
        if tasks.exists():
            if not tasks.is_dir() or _is_reparse(tasks) or tasks.is_symlink():
                raise ReleaseBoundaryError("Release tasks directory is unsafe")
        else:
            tasks.mkdir()
        return self

    def _assert_owned(self) -> None:
        marker = _strict_json_file(self.root / ".lads-release.json", 4096, "Release marker")
        if marker != {"application_id": RELEASE_APPLICATION_ID, "format": RELEASE_MARKER_FORMAT}:
            raise ReleaseBoundaryError("Release ownership marker changed")
        ignore = self.root / ".gitignore"
        if not ignore.is_file() or _is_reparse(ignore) or ignore.read_bytes() != b"*\n":
            raise ReleaseBoundaryError("Release deny-all .gitignore changed")
        try:
            _reject_reparse_chain(self.root)
        except Exception as exc:
            raise ReleaseBoundaryError("Release root became a reparse path") from exc

    def task_dir(self, task_id: str, *, create: bool = False) -> Path:
        self._assert_owned()
        if not isinstance(task_id, str) or not _UUID_RE.fullmatch(task_id):
            raise ReleaseBoundaryError("Release task ID must be a canonical UUID")
        selected = self.root / "tasks" / task_id
        if create:
            selected.mkdir(exist_ok=True)
            for name in (".lads-release.json", ".gitignore", "staging", "worktree", "evidence"):
                if name.startswith("."):
                    continue
        if not selected.exists():
            raise ReleaseBoundaryError("Release task is not present")
        try:
            _reject_reparse_chain(selected)
        except Exception as exc:
            raise ReleaseBoundaryError("Release task path traverses a reparse point") from exc
        if not selected.is_dir() or selected.is_symlink():
            raise ReleaseBoundaryError("Release task path is not a safe directory")
        resolved = selected.resolve(strict=True)
        if self.root not in resolved.parents:
            raise ReleaseBoundaryError("Release task escaped its private root")
        return resolved

    def lock(self, task_id: str) -> _ReleaseLock:
        directory = self.task_dir(task_id, create=True)
        return _ReleaseLock(directory / ".lock")

    def _private_json_path(self, directory: Path, name: str) -> Path:
        if name not in {"state.json", "events.json"}:
            raise ReleaseBoundaryError("Release JSON filename is not managed")
        selected = directory / name
        if selected.parent != directory or selected.name != name:
            raise ReleaseBoundaryError("Release JSON path is not lexical")
        try:
            _reject_reparse_chain(selected.absolute())
        except Exception as exc:
            raise ReleaseBoundaryError("Release JSON path traverses a reparse point") from exc
        if selected.is_symlink() or _is_reparse(selected):
            raise ReleaseBoundaryError("Release JSON path is a symlink or reparse point")
        return selected

    @staticmethod
    def _private_file_exists(path: Path) -> bool:
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise ReleaseBoundaryError("Release JSON path cannot be inspected") from exc
        return True

    def _read_private_json(
        self,
        path: Path,
        maximum: int,
        label: str,
        *,
        missing: type[ReleaseError] | None = None,
    ) -> dict[str, Any]:
        if not self._private_file_exists(path):
            if missing is not None:
                raise missing(f"{label} does not exist")
            raise ReleaseBoundaryError(f"{label} is missing")
        if path.is_symlink() or _is_reparse(path) or not path.is_file():
            raise ReleaseBoundaryError(f"{label} is not a regular non-reparse file")
        try:
            with path.open("rb") as handle:
                data = handle.read(maximum + 1)
        except OSError as exc:
            raise ReleaseBoundaryError(f"{label} cannot be read") from exc
        if len(data) > maximum:
            raise ReleaseBoundaryError(f"{label} is oversized")
        return _strict_private_json_bytes(data, label)

    def _atomic_write(self, path: Path, data: bytes) -> None:
        if not path.is_absolute():
            raise ReleaseBoundaryError("Release write target must be absolute")
        try:
            _reject_reparse_chain(path.parent.absolute())
            parent = path.parent.resolve(strict=True)
        except Exception as exc:
            raise ReleaseBoundaryError("Release write parent is unavailable or unsafe") from exc
        if parent != self.root and self.root not in parent.parents:
            raise ReleaseBoundaryError("Release write target escaped its private root")
        if path.is_symlink() or _is_reparse(path):
            raise ReleaseBoundaryError("Release write target is a symlink or reparse point")
        descriptor, temporary_name = tempfile.mkstemp(prefix=".lads-release-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if path.is_symlink() or _is_reparse(path):
                raise ReleaseBoundaryError("Release write target changed to a link")
            os.replace(temporary, path)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

    def load(self, task_id: str) -> dict[str, Any]:
        self._assert_owned()
        if not isinstance(task_id, str) or not _UUID_RE.fullmatch(task_id):
            raise ReleaseBoundaryError("Release task ID must be a canonical UUID")
        tasks = self.root / "tasks"
        if not tasks.is_dir() or tasks.is_symlink() or _is_reparse(tasks):
            raise ReleaseBoundaryError("Release tasks directory is unsafe")
        selected = tasks / task_id
        if not selected.exists():
            try:
                _reject_reparse_chain(selected.absolute())
            except Exception as exc:
                raise ReleaseBoundaryError("Release task path traverses a reparse point") from exc
            raise ReleaseStateNotFound("Release state does not exist")
        directory = self.task_dir(task_id)
        path = self._private_json_path(directory, "state.json")
        try:
            value = self._read_private_json(
                path,
                MAX_STATE_BYTES,
                "Release state",
                missing=ReleaseStateNotFound,
            )
        except ReleaseStateNotFound:
            raise
        _validate_state_shape(value)
        return value

    def save(self, task_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
        directory = self.task_dir(task_id, create=True)
        payload = dict(value)
        payload["schema"] = RELEASE_SCHEMA
        payload["updated_at"] = datetime.now(UTC).isoformat()
        payload.setdefault("ready", payload.get("status") == "MERGED")
        payload.setdefault(
            "network_failure_total",
            len(payload.get("network_failures", []))
            if isinstance(payload.get("network_failures", []), list)
            else 0,
        )
        _assert_finite_json(payload, "Release state")
        _validate_state_shape(payload)
        path = self._private_json_path(directory, "state.json")
        if self._private_file_exists(path):
            current = self._read_private_json(path, MAX_STATE_BYTES, "Release state")
            _validate_state_shape(current)
        else:
            artifacts = [item.name for item in directory.iterdir() if item.name != ".lock"]
            if artifacts:
                raise ReleaseBoundaryError(
                    "Release state is missing while prior task artifacts remain"
                )
        data = (_canonical(payload) + "\n").encode("utf-8")
        if len(data) > MAX_STATE_BYTES:
            raise ReleaseBoundaryError("Release state exceeds its bound")
        self._atomic_write(path, data)
        return payload

    def record_event(self, task_id: str, event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        directory = self.task_dir(task_id, create=True)
        if not isinstance(event_type, str) or not _RISK_RE.fullmatch(event_type):
            raise ReleaseBoundaryError("Release event type is malformed")
        path = self._private_json_path(directory, "events.json")
        if self._private_file_exists(path):
            value = self._read_private_json(path, MAX_EVENTS_BYTES, "Release event log")
            _validate_events_shape(value)
        else:
            value = {"version": 1, "events": []}
        rows = value.get("events")
        if not isinstance(rows, list) or len(rows) >= MAX_RELEASE_EVENTS:
            raise ReleaseBoundaryError("Release event log reached its bound")
        clean = sanitize_metadata(dict(payload))
        _assert_finite_json(clean, "Release event payload")
        event_id = str(uuid.uuid5(uuid.UUID(task_id), event_type + "|" + _canonical(clean)))
        for row in rows:
            if row.get("id") == event_id:
                if row.get("event_type") != event_type or row.get("payload") != clean:
                    raise ReleaseConflict("Release event ID has a conflicting payload")
                return {**row, "idempotent": True}
        row = {
            "id": event_id,
            "task_id": task_id,
            "event_type": event_type,
            "payload": clean,
            "occurred_at": datetime.now(UTC).isoformat(),
        }
        updated = {"version": 1, "events": [*rows, row]}
        _validate_events_shape(updated)
        data = (_canonical(updated) + "\n").encode("utf-8")
        if len(data) > MAX_EVENTS_BYTES:
            raise ReleaseBoundaryError("Release event log exceeds its bound")
        self._atomic_write(
            path,
            data,
        )
        return {**row, "idempotent": False}


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    bounded: bool = False


class CommandRunner:
    """Bounded argv-array subprocess execution without a shell."""

    def __init__(self, *, maximum_output: int = MAX_COMMAND_OUTPUT) -> None:
        if not 1024 <= maximum_output <= 32 * 1024 * 1024:
            raise ReleaseError("Command output bound is invalid")
        self.maximum_output = maximum_output

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | Path | None = None,
        timeout: float = 30.0,
        stdin: bytes | None = None,
    ) -> CommandResult:
        if isinstance(argv, (str, bytes)) or not argv or any(not isinstance(item, str) or "\x00" in item for item in argv):
            raise ReleaseError("Commands must be nonempty argv string arrays")
        if not 0.1 <= timeout <= 300:
            raise ReleaseError("Command timeout is outside its bound")
        process = subprocess.Popen(
            list(argv),
            cwd=str(cwd) if cwd is not None else None,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        buffers = [bytearray(), bytearray()]
        saturated = threading.Event()

        def drain(stream: Any, index: int) -> None:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    return
                remaining = self.maximum_output - len(buffers[index])
                if remaining <= 0:
                    saturated.set()
                    process.kill()
                    return
                buffers[index].extend(chunk[:remaining])
                if len(chunk) > remaining:
                    saturated.set()
                    process.kill()
                    return

        readers = [
            threading.Thread(target=drain, args=(process.stdout, 0), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, 1), daemon=True),
        ]
        for reader in readers:
            reader.start()
        if stdin is not None:
            process.stdin.write(stdin)
            process.stdin.close()
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        for reader in readers:
            reader.join(timeout=3)
        process.stdout.close()
        process.stderr.close()
        return CommandResult(
            argv=tuple(argv),
            exit_code=process.returncode,
            stdout=bytes(buffers[0]),
            stderr=bytes(buffers[1]),
            timed_out=timed_out,
            bounded=saturated.is_set(),
        )


@dataclass(frozen=True)
class RequiredCheck:
    workflow: str
    job: str
    context: str


REQUIRED_CHECKS = (
    RequiredCheck("LADS CI", "ci", "LADS CI / ci"),
    RequiredCheck("LADS Security", "security", "LADS Security / security"),
    RequiredCheck("LADS Docs", "docs", "LADS Docs / docs"),
    RequiredCheck("LADS", "controller", "LADS / controller"),
)
REQUIRED_WORKFLOW_CHECKS = REQUIRED_CHECKS[:3]
CONTROLLER_CHECK = REQUIRED_CHECKS[3]
REQUIRED_WORKFLOW_PATHS = {
    "LADS CI / ci": ".github/workflows/ci.yml",
    "LADS Security / security": ".github/workflows/security.yml",
    "LADS Docs / docs": ".github/workflows/docs-check.yml",
}


@dataclass(frozen=True)
class PullRequest:
    number: int
    node_id: str
    url: str
    state: str
    draft: bool
    mergeable: bool | None
    head_sha: str
    head_ref: str
    base_ref: str
    merged: bool
    merge_commit_sha: str | None

    @classmethod
    def from_api(cls, value: Mapping[str, Any]) -> "PullRequest":
        head = value.get("head") if isinstance(value.get("head"), Mapping) else {}
        base = value.get("base") if isinstance(value.get("base"), Mapping) else {}
        return cls(
            number=int(value.get("number", 0)),
            node_id=str(value.get("node_id") or ""),
            url=str(value.get("html_url") or ""),
            state=str(value.get("state") or ""),
            draft=value.get("draft") is True,
            mergeable=value.get("mergeable") if isinstance(value.get("mergeable"), bool) else None,
            head_sha=str(head.get("sha") or ""),
            head_ref=str(head.get("ref") or ""),
            base_ref=str(base.get("ref") or ""),
            merged=value.get("merged") is True,
            merge_commit_sha=(
                str(value["merge_commit_sha"])
                if isinstance(value.get("merge_commit_sha"), str)
                else None
            ),
        )


class GitHubTransport(Protocol):
    def api(
        self,
        method: str,
        route: str,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        """Execute one bounded GitHub REST request."""

    def graphql(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        """Execute one bounded GitHub GraphQL request."""


class GhCliTransport:
    """Thin ``gh api`` transport. Credential values are never read by LADS."""

    def __init__(self, runner: CommandRunner | None = None, *, executable: str = "gh") -> None:
        self.runner = runner or CommandRunner()
        self.executable = executable

    @staticmethod
    def _error(result: CommandResult) -> GitHubTransportError:
        diagnostic = (result.stderr + b"\n" + result.stdout).decode("utf-8", errors="replace")
        match = re.search(r"\bHTTP\s+([1-5][0-9]{2})\b", diagnostic, re.IGNORECASE)
        status = int(match.group(1)) if match else None
        rate_limited = bool(
            re.search(r"(?i)(?:secondary )?rate limit|retry-after|temporarily unavailable", diagnostic)
        )
        transient = result.timed_out or status in {408, 425, 429, 500, 502, 503, 504} or rate_limited
        message = "GitHub request was rate limited" if rate_limited else "GitHub request failed"
        if status is not None:
            message += f" (HTTP {status})"
        cls = GitHubTransientError if transient else GitHubTransportError
        return cls(message, status=status) if transient else cls(message)

    @staticmethod
    def _parse(data: bytes) -> Any:
        if len(data) > MAX_JSON_RESPONSE:
            raise GitHubTransportError("GitHub response exceeded its bound")
        if not data.strip():
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise GitHubTransportError("GitHub response was not valid JSON") from exc

    def api(
        self,
        method: str,
        route: str,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise GitHubTransportError("Unsupported GitHub method")
        if not route.startswith("/") or ".." in route or "\x00" in route:
            raise GitHubTransportError("GitHub route is not a contained API path")
        argv = [self.executable, "api", "--method", method, route]
        stdin = None
        if body is not None:
            argv.extend(["--input", "-"])
            stdin = _canonical(body).encode("utf-8")
        result = self.runner.run(argv, timeout=45, stdin=stdin)
        if result.exit_code != 0 or result.timed_out or result.bounded:
            raise self._error(result)
        return self._parse(result.stdout)

    def graphql(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or len(query) > 100_000:
            raise GitHubTransportError("GraphQL query is malformed")
        payload = {"query": query, "variables": dict(variables)}
        result = self.runner.run(
            [self.executable, "api", "graphql", "--input", "-"],
            timeout=45,
            stdin=_canonical(payload).encode("utf-8"),
        )
        if result.exit_code != 0 or result.timed_out or result.bounded:
            raise self._error(result)
        parsed = self._parse(result.stdout)
        if not isinstance(parsed, dict):
            raise GitHubTransportError("GraphQL response root is malformed")
        return parsed


class GitHubClient:
    """Bounded REST reads, explicit mutations, and canonical repository pinning."""

    def __init__(
        self,
        transport: GitHubTransport,
        *,
        max_attempts: int = 3,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 1 <= max_attempts <= MAX_RETRIES:
            raise ReleaseError("GitHub read retry count is outside its bound")
        self.transport = transport
        self.max_attempts = max_attempts
        self.sleeper = sleeper
        self.network_failures: list[dict[str, Any]] = []
        self.repository_resolution: dict[str, Any] | None = None

    def get(self, route: str) -> Any:
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self.transport.api("GET", route)
            except GitHubTransientError as exc:
                self.network_failures.append(
                    {
                        "method": "GET",
                        "route": route,
                        "attempt": attempt,
                        "status": exc.status,
                    }
                )
                if len(self.network_failures) > MAX_STORED_NETWORK_FAILURES:
                    del self.network_failures[:-MAX_STORED_NETWORK_FAILURES]
                if attempt >= self.max_attempts:
                    raise
                delay = min(5.0, max(0.05, exc.retry_after or (0.15 * (2 ** (attempt - 1)))))
                self.sleeper(delay)
        raise AssertionError("unreachable")

    def mutate(self, method: str, route: str, body: Mapping[str, Any]) -> Any:
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            raise GitHubTransportError("Mutation method is not allowed")
        return self.transport.api(method, route, body)

    def resolve_repository(self, repository: str = CANONICAL_REPOSITORY) -> dict[str, Any]:
        # Deliberately resolve the audited historical short route once. GitHub
        # returns the canonical full_name, which every later route must use.
        if repository not in APPROVED_REPOSITORIES:
            raise ReleaseBlocked("Repository is not in the approved registry")
        route = "/repos/dabinglei2-hash/-" if repository == CANONICAL_REPOSITORY else "/repos/" + repository
        value = self.get(route)
        if not isinstance(value, dict) or value.get("full_name") != repository:
            raise ReleaseBlocked("GitHub repository canonical name did not resolve exactly")
        if value.get("private") is True or value.get("visibility") != "public":
            raise ReleaseBlocked("The audited public repository visibility changed")
        repository_id = value.get("id")
        if repository_id is not None and (
            type(repository_id) is not int or repository_id <= 0
        ):
            raise ReleaseBlocked("GitHub repository ID proof is malformed")
        if self.repository_resolution is not None:
            previous = self.repository_resolution
            if (
                previous.get("full_name") != value.get("full_name")
                or (
                    previous.get("id") is not None
                    and repository_id is not None
                    and previous.get("id") != repository_id
                )
            ):
                raise ReleaseBlocked("GitHub repository redirect identity changed")
        self.repository_resolution = {
            "full_name": value["full_name"],
            "id": repository_id,
            "private": False,
            "visibility": "public",
        }
        return value


_SECRET_PATTERNS = (
    re.compile("-----BEGIN " + r"[A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-(?:proj-|live-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret)"
        + r"\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{20,}"
    ),
)
_HISTORY_REWRITE_PATTERNS = (
    re.compile(r"\bgit\s+push\b[^\n]*(?:--force(?:-with-lease)?|\s-f(?:\s|$))", re.IGNORECASE),
    re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
    re.compile(r"\bgit\s+clean\s+-[A-Za-z]*f[A-Za-z]*\b", re.IGNORECASE),
)
_DENIED_PATH_PARTS = frozenset(
    {
        ".env",
        ".ssh",
        ".aws",
        ".git",
        "auth.json",
        "credentials",
        "credential",
        "personal-data",
        "data-backups",
        "browser-profile",
        "chrome-profile",
        "cookies",
        "backups",
        "id_rsa",
        "id_ed25519",
        ".netrc",
        ".npmrc",
        ".pypirc",
    }
)
_DENIED_SUFFIXES = frozenset(
    {
        ".db",
        ".sqlite",
        ".sqlite3",
        ".sqlite-wal",
        ".sqlite-shm",
        ".db-wal",
        ".db-shm",
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".kdbx",
    }
)
_TEXT_SUFFIXES = frozenset(
    {
        ".md",
        ".txt",
        ".py",
        ".js",
        ".mjs",
        ".cjs",
        ".css",
        ".html",
        ".json",
        ".yml",
        ".yaml",
        ".toml",
        ".webmanifest",
        ".svg",
        ".xml",
        ".sh",
        ".ps1",
    }
)


@dataclass(frozen=True)
class AuditFinding:
    path: str
    code: str
    severity: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code, "severity": self.severity}


@dataclass(frozen=True)
class ChangeAudit:
    risk_flags: tuple[str, ...]
    findings: tuple[AuditFinding, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "risk_flags": list(self.risk_flags),
            "findings": [item.as_dict() for item in self.findings],
        }


def audit_changes(repo_root: str | Path, authorization: ReleaseAuthorization) -> ChangeAudit:
    root = Path(repo_root).resolve(strict=True)
    binary_allowlist = dict(authorization.binary_assets)
    findings: list[AuditFinding] = []
    flags: set[str] = set()
    for deletion in authorization.deletions:
        flags.add("deletion")
        findings.append(AuditFinding(deletion.path, "authorized_deletion", "high"))
    for change in authorization.changes:
        path = change.path
        normalized = path.casefold()
        parts = set(normalized.split("/"))
        suffix = Path(normalized).suffix
        if parts & _DENIED_PATH_PARTS or suffix in _DENIED_SUFFIXES or Path(normalized).name.startswith(".env"):
            flags.add("private_or_secret_path")
            findings.append(AuditFinding(path, "private_or_secret_path", "critical"))
        if any(part in {"migration", "migrations", "alembic", "schema", "schemas"} for part in parts):
            flags.add("database_migration")
            findings.append(AuditFinding(path, "database_or_schema_change", "high"))
        if any(word in normalized for word in ("auth", "identity", "permission", "privilege", "credential")):
            flags.add("auth_or_identity")
            findings.append(AuditFinding(path, "auth_or_identity_change", "high"))
        if normalized.startswith(".github/workflows/") or normalized.startswith(".github/actions/"):
            flags.add("workflow_policy_change")
            findings.append(AuditFinding(path, "workflow_or_policy_change", "high"))
        if parts & {"deploy", "deployment", "production"}:
            flags.add("production_or_history_change")
            findings.append(AuditFinding(path, "production_or_history_surface", "high"))
        selected = root / path
        try:
            _reject_reparse_chain(selected)
        except Exception as exc:
            raise ReleaseBoundaryError("Approved change traverses a reparse point") from exc
        if not selected.is_file() or selected.is_symlink():
            raise ReleaseAuthorizationError("Approved text change is not a regular file")
        if selected.stat().st_size > MAX_SCAN_BYTES:
            raise ReleaseAuthorizationError("Approved change exceeds the source scan bound")
        if _sha256_file(selected) != change.after_sha256:
            raise ReleaseAuthorizationError("Approved change bytes drifted from authorization")
        data = selected.read_bytes()
        binary = b"\0" in data[:8192] or suffix not in _TEXT_SUFFIXES
        if suffix in _TEXT_SUFFIXES:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                binary = True
            else:
                credential_exempt = (
                    path,
                    change.after_sha256,
                    "credential_or_key_material",
                ) in KNOWN_AUDIT_EXEMPTIONS
                if not credential_exempt and any(
                    pattern.search(text) for pattern in _SECRET_PATTERNS
                ):
                    flags.add("secret_material")
                    findings.append(
                        AuditFinding(path, "credential_or_key_material", "critical")
                    )
                history_exempt = (
                    path,
                    change.after_sha256,
                    "history_rewrite_or_force_command",
                ) in KNOWN_AUDIT_EXEMPTIONS
                if not history_exempt and any(
                    pattern.search(text) for pattern in _HISTORY_REWRITE_PATTERNS
                ):
                    flags.add("history_rewrite_or_force")
                    findings.append(
                        AuditFinding(path, "history_rewrite_or_force_command", "critical")
                    )
        if binary:
            if binary_allowlist.get(path) != change.after_sha256:
                flags.add("unknown_binary")
                findings.append(AuditFinding(path, "unknown_binary_asset", "high"))
    return ChangeAudit(tuple(sorted(flags)), tuple(findings))


def _audit_review_gaps(
    audit: ChangeAudit,
    declared_flags: tuple[str, ...] | list[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    critical = tuple(
        sorted(
            {
                finding.code
                for finding in audit.findings
                if finding.severity == "critical"
            }
        )
    )
    if critical:
        return critical, ()
    undeclared = tuple(sorted(set(audit.risk_flags) - set(declared_flags)))
    return (), undeclared


def _validate_public_text(value: str, label: str) -> None:
    if re.search(r"(?i)(?:[A-Z]:[\\/]|/Users/|/home/|C:/Users/)", value):
        raise ReleaseAuthorizationError(f"{label} contains a local absolute user path")
    for pattern in _SECRET_PATTERNS:
        if pattern.search(value):
            raise ReleaseAuthorizationError(f"{label} contains credential-like material")


@dataclass(frozen=True)
class LocalGate:
    bundle: Any
    journal_state: dict[str, Any]
    journal_events: tuple[dict[str, Any], ...]
    current_files: dict[str, str]
    candidate_files: dict[str, str]
    main_ack_sha256: str
    docs_receipt_sha256: str
    index_receipt_sha256: str
    evidence: dict[str, Any]

    @property
    def task_id(self) -> str:
        return str(self.bundle.task_id)


def _journal_state(task_id: str, journal: Any | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        selected = journal or FileJournal.open()
        return selected.get_task(task_id), selected.list_events(task_id)
    except (JournalError, OSError) as exc:
        raise ReleaseGateError("Stage 3 DELIVERY_READY journal is unavailable") from exc


def _contained_stage_json(
    run_root: Path,
    reference: Mapping[str, Any],
) -> tuple[dict[str, Any], bytes, str, Path]:
    try:
        relative = _safe_relative(reference.get("path"), "worker stage path")
    except ReleaseAuthorizationError as exc:
        raise ReleaseGateError("Worker stage reference is not a contained path") from exc
    candidate = run_root / Path(relative)
    try:
        _reject_reparse_chain(candidate.absolute())
        resolved = candidate.resolve(strict=True)
    except Exception as exc:
        raise ReleaseGateError("Worker stage file is missing or traverses a reparse point") from exc
    if resolved != run_root and run_root not in resolved.parents:
        raise ReleaseGateError("Worker stage file escaped the coordinator run root")
    if (
        candidate.is_symlink()
        or _is_reparse(candidate)
        or _is_reparse(resolved)
        or not resolved.is_file()
    ):
        raise ReleaseGateError("Worker stage file is not a regular non-reparse file")
    try:
        with resolved.open("rb") as handle:
            data = handle.read(delivery.MAX_JSON_BYTES + 1)
    except OSError as exc:
        raise ReleaseGateError("Worker stage file cannot be read") from exc
    if len(data) > delivery.MAX_JSON_BYTES:
        raise ReleaseGateError("Worker stage file is oversized")
    try:
        value = _strict_private_json_bytes(data, "Worker stage file")
    except ReleaseBoundaryError as exc:
        raise ReleaseGateError("Worker stage file is invalid UTF-8 JSON") from exc
    return value, data, _sha256(data), resolved


def _expected_stage_workspace(
    run_root: Path,
    role: str,
    status: str,
    build: int,
) -> Path:
    if role == "scanner" and build == 0 and status == "SCAN_COMPLETE":
        return run_root / "source"
    if role == "reviewer" and build == 0 and status == "APPROVED":
        return run_root / "source"
    if role == "builder" and 1 <= build <= BASE.MAX_BUILDS and status == "BUILT":
        return run_root / "builder"
    if (
        role == "qa-tester"
        and 1 <= build <= BASE.MAX_BUILDS
        and status in {"PASS", "FAIL"}
    ):
        return run_root / "qa" / f"build-{build}"
    if (
        role == "reviewer"
        and 1 <= build <= BASE.MAX_BUILDS
        and status in {"APPROVED", "CHANGES_REQUESTED"}
    ):
        return run_root / "qa" / f"build-{build}"
    raise ReleaseGateError("Worker role, status, and build combination is invalid")


def _validated_stage_workspace(run_root: Path, expected: Path) -> Path:
    try:
        _reject_reparse_chain(expected.absolute())
        resolved = expected.resolve(strict=True)
    except Exception as exc:
        raise ReleaseGateError("Expected worker workspace is missing or unsafe") from exc
    if resolved != run_root and run_root not in resolved.parents:
        raise ReleaseGateError("Expected worker workspace escaped the run root")
    if (
        expected.is_symlink()
        or _is_reparse(expected)
        or _is_reparse(resolved)
        or not resolved.is_dir()
    ):
        raise ReleaseGateError("Expected worker workspace is not a safe directory")
    return resolved


def _command_flag_value(command: Sequence[str], flag: str, role: str) -> str:
    prefixed = [item for item in command if item.startswith(flag + "=")]
    indexes = [index for index, item in enumerate(command) if item == flag]
    if prefixed or len(indexes) != 1 or indexes[0] + 1 >= len(command):
        raise ReleaseGateError(f"{role} command {flag} flag is missing or ambiguous")
    return command[indexes[0] + 1]


def _validate_stage_command(
    command: Any,
    proof: Mapping[str, Any],
    *,
    role: str,
    expected_workspace: Path,
) -> None:
    if (
        not isinstance(command, list)
        or not command
        or len(command) > 512
        or any(not isinstance(item, str) or not item or len(item) > 4000 for item in command)
    ):
        raise ReleaseGateError("Worker stage command is missing or malformed")
    if any(item.startswith("--cd") for item in command):
        raise ReleaseGateError("Worker command contains a conflicting --cd override")
    workspace_flags = [item for item in command if item.startswith("-C")]
    if workspace_flags != ["-C"]:
        raise ReleaseGateError("Worker command must contain exactly one -C workspace")
    workspace_text = _command_flag_value(command, "-C", role)
    expected_text = os.path.normcase(os.path.abspath(str(expected_workspace)))
    supplied_text = os.path.normcase(os.path.abspath(workspace_text))
    if supplied_text != expected_text:
        raise ReleaseGateError("Worker command workspace is not the exact role/build root")
    try:
        supplied = Path(workspace_text).resolve(strict=True)
    except OSError as exc:
        raise ReleaseGateError("Worker command workspace is unavailable") from exc
    if supplied != expected_workspace:
        raise ReleaseGateError("Worker command workspace resolves to a different directory")
    sandbox = _command_flag_value(command, "--sandbox", role)
    expected_mode = "workspace-write" if role == "builder" else "read-only"
    if sandbox != expected_mode:
        raise ReleaseGateError("Worker command sandbox mode is broader than assigned")
    if _command_flag_value(command, "--ask-for-approval", role) != "never":
        raise ReleaseGateError("Worker command approval policy is not never")
    prefix_length = proof.get("worker_prefix_length")
    debug_argv = proof.get("debug_argv")
    if (
        type(prefix_length) is not int
        or prefix_length <= 0
        or not isinstance(debug_argv, list)
        or prefix_length > len(debug_argv)
        or command[:prefix_length] != debug_argv[:prefix_length]
    ):
        raise ReleaseGateError("Worker command does not start with the exact proven prefix")
    prefix = command[:prefix_length]
    if _sha256(_canonical(prefix).encode("utf-8")) != proof.get("worker_prefix_sha256"):
        raise ReleaseGateError("Worker command prefix digest changed")


def _validate_stage_metadata(
    raw: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    role: str,
    expected_workspace: Path,
) -> None:
    metadata = raw.get("metadata")
    preserved = receipt.get("stage_metadata")
    if (
        not isinstance(metadata, dict)
        or not isinstance(preserved, Mapping)
        or set(preserved) != STAGE_METADATA_FIELDS
    ):
        raise ReleaseGateError("Worker raw or normalized metadata is incomplete")
    selected = {name: metadata.get(name) for name in STAGE_METADATA_FIELDS}
    for name in STAGE_METADATA_FIELDS - {"input_tokens", "output_tokens"}:
        if not isinstance(selected[name], str) or not selected[name]:
            raise ReleaseGateError("Worker raw metadata text is malformed")
    for name in {"input_tokens", "output_tokens"}:
        value = selected[name]
        if value is not None and (type(value) is not int or value < 0):
            raise ReleaseGateError("Worker token metadata is malformed")
    if dict(preserved) != selected:
        raise ReleaseGateError("Normalized worker metadata differs from the raw stage")
    if (
        selected["role"] != role
        or selected["provider"] != STAGE3_WORKER_PROVIDER
        or selected["model"] != STAGE3_WORKER_MODEL
    ):
        raise ReleaseGateError("Worker metadata identity is not the assigned DeepSeek role")
    expected_mode = "workspace-write" if role == "builder" else "read-only"
    if selected["sandbox"] != expected_mode:
        raise ReleaseGateError("Worker metadata sandbox mode is not the assigned mode")
    proof = metadata.get("preflight_proof")
    try:
        validated = BASE.validate_preflight_proof(proof, role)
    except Exception as exc:
        raise ReleaseGateError("Worker successful preflight proof is missing or malformed") from exc
    permissions = validated["permissions"]
    if permissions["sandbox_mode"] != selected["sandbox"]:
        raise ReleaseGateError("Worker metadata and preflight sandbox modes differ")
    if (
        selected["home_mode"] != validated["home_mode"]
        or selected["home_sha256"] != validated["home_sha256"]
        or selected["authority_sha256"] != validated["authority_sha256"]
    ):
        raise ReleaseGateError("Worker home or authority evidence does not match preflight")
    runtime_home = metadata.get("runtime_home")
    if (
        not isinstance(runtime_home, str)
        or os.path.normcase(os.path.abspath(runtime_home))
        != os.path.normcase(os.path.abspath(validated["runtime_home"]))
    ):
        raise ReleaseGateError("Worker runtime home does not match preflight")
    try:
        derived_config = _sha256(
            _canonical(validated["config_sha256"]).encode("utf-8")
        )
    except Exception as exc:
        raise ReleaseGateError("Worker preflight config snapshots cannot be derived") from exc
    if selected["config_snapshot_sha256"] != derived_config:
        raise ReleaseGateError("Worker config snapshot hash does not match preflight")
    roots = permissions["writable_roots"]
    if role == "builder":
        if len(roots) != 1:
            raise ReleaseGateError("Builder preflight writable root is not unique")
        supplied_root = os.path.normcase(os.path.abspath(roots[0]))
        expected_root = os.path.normcase(os.path.abspath(str(expected_workspace)))
        if supplied_root != expected_root:
            raise ReleaseGateError("Builder preflight writable root is not the exact workspace")
        try:
            resolved_root = Path(roots[0]).resolve(strict=True)
        except OSError as exc:
            raise ReleaseGateError("Builder preflight writable root is unavailable") from exc
        if resolved_root != expected_workspace:
            raise ReleaseGateError("Builder preflight writable root resolves elsewhere")
    elif roots:
        raise ReleaseGateError("Read-only worker preflight unexpectedly has writable roots")
    _validate_stage_command(raw.get("command"), validated, role=role, expected_workspace=expected_workspace)


def _validate_worker_receipts(bundle: Any) -> None:
    if unit_qa.contract_version(getattr(bundle, "expected_commands", ())) is None:
        raise ReleaseGateError("QA command set is not an exact known v1 or v2 contract")
    receipts = tuple(getattr(bundle, "worker_stage_receipts", ()))
    if not 4 <= len(receipts) <= 100:
        raise ReleaseGateError("Actual DeepSeek worker history is missing or unbounded")
    run_root_value = getattr(bundle, "run_root", None)
    if run_root_value is None:
        raise ReleaseGateError("Coordinator run root is missing from worker receipts")
    try:
        run_root_path = Path(run_root_value)
        if not run_root_path.is_absolute():
            raise ReleaseGateError("Coordinator run root must be absolute")
        _reject_reparse_chain(run_root_path.absolute())
        if run_root_path.is_symlink() or _is_reparse(run_root_path):
            raise ReleaseGateError("Coordinator run root is a link")
        run_root = run_root_path.resolve(strict=True)
    except Exception as exc:
        raise ReleaseGateError("Coordinator run root is unavailable or unsafe") from exc
    if not run_root.is_dir() or _is_reparse(run_root) or run_root.is_symlink():
        raise ReleaseGateError("Coordinator run root is not a safe directory")
    handoff = getattr(bundle, "handoff", None)
    handoff_refs = handoff.get("stage_reports") if isinstance(handoff, Mapping) else None
    handoff_role_runs = handoff.get("role_runs") if isinstance(handoff, Mapping) else None
    if not isinstance(handoff_refs, list) or not 4 <= len(handoff_refs) <= 100:
        raise ReleaseGateError("Coordinator handoff stage refs are missing or unbounded")
    if (
        not isinstance(handoff_role_runs, list)
        or len(handoff_role_runs) != len(receipts)
        or any(not isinstance(item, Mapping) for item in handoff_role_runs)
    ):
        raise ReleaseGateError("Coordinator handoff worker metadata is missing or unbounded")
    role_run_identities = [_canonical(item) for item in handoff_role_runs]
    if len(role_run_identities) != len(set(role_run_identities)):
        raise ReleaseGateError("Coordinator handoff worker metadata is duplicated")
    remaining_role_runs = set(role_run_identities)
    refs_by_path: dict[str, dict[str, str]] = {}
    for reference in handoff_refs:
        if (
            not isinstance(reference, Mapping)
            or set(reference) != {"path", "sha256"}
            or not isinstance(reference.get("path"), str)
            or not _SHA64_RE.fullmatch(str(reference.get("sha256") or ""))
        ):
            raise ReleaseGateError("Coordinator handoff stage ref is malformed")
        try:
            relative = _safe_relative(reference["path"], "handoff stage path")
        except ReleaseAuthorizationError as exc:
            raise ReleaseGateError("Coordinator handoff stage ref is not contained") from exc
        if relative in refs_by_path:
            raise ReleaseGateError("Coordinator handoff stage refs are duplicated")
        refs_by_path[relative] = {
            "path": relative,
            "sha256": str(reference["sha256"]),
        }
    roles: list[str] = []
    stage_rows: list[tuple[Mapping[str, Any], dict[str, Any], int]] = []
    for item in receipts:
        if not isinstance(item, Mapping):
            raise ReleaseGateError("Worker receipt is not an object")
        role = item.get("role")
        stage_ref = item.get("stage_ref")
        stage_metadata = item.get("stage_metadata")
        result = item.get("result")
        if (
            item.get("task_id") != bundle.task_id
            or role not in STAGE3_WORKER_ROLES
            or not isinstance(item.get("status"), str)
            or not item["status"]
            or item.get("provider") != STAGE3_WORKER_PROVIDER
            or item.get("model") != STAGE3_WORKER_MODEL
            or not isinstance(stage_ref, Mapping)
            or set(stage_ref) != {"path", "sha256"}
            or not isinstance(stage_ref.get("path"), str)
            or not stage_ref["path"]
            or not _SHA64_RE.fullmatch(str(stage_ref.get("sha256") or ""))
            or not isinstance(stage_metadata, Mapping)
            or stage_metadata.get("role") != role
            or stage_metadata.get("provider") != STAGE3_WORKER_PROVIDER
            or stage_metadata.get("model") != STAGE3_WORKER_MODEL
            or result is None
            or type(getattr(result, "exit_code", None)) is not int
            or getattr(result, "exit_code", None) != 0
        ):
            raise ReleaseGateError("Worker receipt does not prove the assigned independent Stage3 role")
        reference = {"path": stage_ref["path"], "sha256": str(stage_ref["sha256"])}
        if refs_by_path.get(reference["path"]) != reference:
            raise ReleaseGateError("Worker stage ref differs from the coordinator handoff")
        raw_stage, stage_bytes, stage_digest, _stage_path = _contained_stage_json(
            run_root,
            reference,
        )
        if stage_digest != reference["sha256"]:
            raise ReleaseGateError("Worker stage raw digest changed")
        build = raw_stage.get("build")
        if (
            raw_stage.get("task_id") != bundle.task_id
            or raw_stage.get("role") != role
            or raw_stage.get("status") != item["status"]
            or type(build) is not int
            or raw_stage.get("exit_code") != 0
            or type(raw_stage.get("exit_code")) is not int
        ):
            raise ReleaseGateError("Raw worker stage does not bind its receipt")
        expected_workspace = _validated_stage_workspace(
            run_root,
            _expected_stage_workspace(
                run_root,
                str(role),
                str(item["status"]),
                build,
            ),
        )
        _validate_stage_metadata(
            raw_stage,
            item,
            role=str(role),
            expected_workspace=expected_workspace,
        )
        raw_metadata = raw_stage.get("metadata")
        if not isinstance(raw_metadata, Mapping):
            raise ReleaseGateError("Raw worker metadata is missing")
        metadata_identity = _canonical(raw_metadata)
        if metadata_identity not in remaining_role_runs:
            raise ReleaseGateError("Raw worker metadata is not bound by the coordinator handoff")
        remaining_role_runs.remove(metadata_identity)
        roles.append(str(role))
        stage_rows.append((item, raw_stage, build))
    if remaining_role_runs:
        raise ReleaseGateError("Coordinator handoff contains unbound worker metadata")
    if not STAGE3_WORKER_ROLES <= set(roles):
        raise ReleaseGateError("Actual worker stage history is missing an assigned role")
    if roles[0] != "scanner":
        raise ReleaseGateError("Actual worker history must start with Scanner")
    index = 1
    if index >= len(roles) or roles[index] != "reviewer":
        raise ReleaseGateError("Actual worker history requires a plan Reviewer after Scanner")
    index += 1
    if index >= len(roles) or roles[index] != "builder":
        raise ReleaseGateError("Plan Reviewer must precede the first Builder build")
    index += 1
    while index < len(roles):
        if tuple(roles[index : index + 3]) != ("qa-tester", "reviewer", "builder"):
            if tuple(roles[index:]) == ("qa-tester", "reviewer"):
                index += 2
                break
            raise ReleaseGateError("Worker rework history is not QA then final Reviewer ordered")
        index += 3
    if index != len(roles) or roles[-2:] != ["qa-tester", "reviewer"]:
        raise ReleaseGateError("Worker history must end with QA PASS then final Reviewer approval")
    builder_builds = [
        build
        for receipt, _raw, build in stage_rows
        if receipt.get("role") == "builder"
    ]
    if builder_builds != list(range(1, len(builder_builds) + 1)):
        raise ReleaseGateError("Builder build numbers are not contiguous from build 1")
    for position, (receipt, _raw, build) in enumerate(stage_rows):
        if receipt.get("role") != "qa-tester":
            continue
        if position + 1 >= len(stage_rows):
            raise ReleaseGateError("QA stage is not followed by final Reviewer evidence")
        reviewer_receipt, _reviewer_raw, reviewer_build = stage_rows[position + 1]
        expected_reviewer_status = (
            "APPROVED" if receipt.get("status") == "PASS" else "CHANGES_REQUESTED"
        )
        if (
            reviewer_receipt.get("role") != "reviewer"
            or reviewer_receipt.get("status") != expected_reviewer_status
            or reviewer_build != build
        ):
            raise ReleaseGateError("QA status and same-build Reviewer status do not match")
    final_build = stage_rows[-1][2]
    if isinstance(handoff, Mapping) and handoff.get("builds") is not None:
        if type(handoff.get("builds")) is not int or handoff.get("builds") != final_build:
            raise ReleaseGateError("Final Reviewer build differs from the coordinator handoff")
    qa_receipt = getattr(bundle, "qa_receipt", {})
    reviewer = getattr(bundle, "reviewer_receipt", {})
    if qa_receipt.get("status") != "PASS" or reviewer.get("status") != "APPROVED":
        raise ReleaseGateError("Independent QA PASS and final Reviewer APPROVED are required")
    if (
        receipts[-2].get("status") != "PASS"
        or receipts[-1].get("status") != "APPROVED"
        or receipts[-2]["stage_ref"]["sha256"]
        != getattr(bundle, "qa_stage_sha256", receipts[-2]["stage_ref"]["sha256"])
        or receipts[-1]["stage_ref"]["sha256"]
        != getattr(
            bundle,
            "reviewer_stage_sha256",
            receipts[-1]["stage_ref"]["sha256"],
        )
    ):
        raise ReleaseGateError("Final QA and Reviewer receipt digests are not the delivery proof")
    validate_stage_acceptance(getattr(bundle, "task_spec"), getattr(bundle, "receipts"))


def _validate_stage4_unit_proof(bundle: Any) -> dict[str, Any]:
    """Re-run the read-only v2 verifier against current delivery bytes."""
    version = unit_qa.contract_version(tuple(getattr(bundle, "expected_commands", ())))
    if version not in {2, 3}:
        raise ReleaseGateError("New Stage4 releases require the exact v2 unit contract")
    run_root = Path(getattr(bundle, "run_root", ""))
    candidate_dir = Path(getattr(bundle, "candidate_dir", ""))
    try:
        if version == 3:
            from .split_qa import verify_report as unit_verifier
        else:
            unit_verifier = unit_qa.load_and_verify_unit_report
        verified = unit_verifier(
            run_root / unit_qa.UNIT_REPORT_NAME,
            candidate_root=candidate_dir,
            task_id=bundle.task_id,
            source_head=bundle.source_head,
            expected_inventory_sha256=bundle.candidate_sha256,
        )
    except Exception as exc:
        raise ReleaseGateError("Stage4 unit proof cannot be reverified") from exc
    claimed = getattr(bundle, "unit_evidence", None)
    qa_receipt = getattr(bundle, "qa_receipt", {})
    controller_receipt = getattr(bundle, "controller_receipt", {})
    digest = verified["evidence_sha256"]
    if (
        not isinstance(claimed, Mapping)
        or claimed.get("evidence_sha256") != digest
        or claimed.get("report_sha256") != verified["report_sha256"]
        or claimed.get("report_receipt_sha256") != verified["report_receipt_sha256"]
        or claimed.get("context_sha256") != verified["context_sha256"]
        or qa_receipt.get("qa_contract_version") != version
        or not isinstance(qa_receipt.get("unit_evidence"), Mapping)
        or qa_receipt["unit_evidence"].get("evidence_sha256") != digest
        or controller_receipt.get("unit_evidence_sha256") != digest
    ):
        raise ReleaseGateError("Stage4 unit proof is not bound to QA and controller receipts")
    return verified


def _validate_persisted_evidence(
    state: Mapping[str, Any],
    bundle: Any,
) -> dict[str, Any]:
    evidence = state.get("evidence")
    if not isinstance(evidence, dict) or (
        evidence.get("qa_review_verified") is not True
        or evidence.get("whole_task_complete") is not False
        or evidence.get("reviewer_independent") is not True
        or evidence.get("simulated") is not False
        or evidence.get("acceptance_mode") != "independent"
        or evidence.get("candidate_sha256") != bundle.candidate_sha256
        or evidence.get("task_id") != bundle.task_id
    ):
        raise ReleaseGateError("Persisted evidence contains forged or non-independent acceptance")
    if tuple(evidence.get("commands", ())) != tuple(bundle.expected_commands):
        raise ReleaseGateError("Persisted QA command set changed")
    return evidence


def _expected_published_files(
    run_root: Path,
    journal_state: Mapping[str, Any],
    journal_events: Sequence[Mapping[str, Any]],
) -> tuple[str, dict[str, str], dict[str, str], dict[str, str]]:
    handoff = delivery._strict_json(delivery._contained_file(run_root, "finalhandoff.json"))
    state = delivery._strict_json(delivery._contained_file(run_root, "state.json"))
    task_id = handoff.get("task_id")
    if (
        not isinstance(task_id, str)
        or state.get("task_id") != task_id
        or handoff.get("task_status") != "AWAITING_MAIN_ACCEPTANCE"
        or state.get("state") != "AWAITING_MAIN_ACCEPTANCE"
    ):
        raise ReleaseGateError("Stage 3 handoff is not awaiting explicit MAIN-ACK")
    builds = handoff.get("builds")
    if type(builds) is not int or not 1 <= builds <= 8:
        raise ReleaseGateError("Stage 3 build number is malformed")
    source_dir = delivery._contained_directory(run_root, "source")
    candidate_dir = delivery._contained_directory(run_root, f"candidates/build-{builds}")
    source_files = qa.source_inventory(source_dir)
    candidate_files = qa.source_inventory(candidate_dir)
    source_head = handoff.get("source_head")
    if not isinstance(source_head, str) or not _SHA40_RE.fullmatch(source_head):
        raise ReleaseGateError("Stage 3 source HEAD is malformed")
    source_digest = qa.inventory_digest(source_head, source_files)
    candidate_digest = qa.inventory_digest(source_head, candidate_files)
    if (
        handoff.get("candidate_sha256") != candidate_digest
        or state.get("candidate_sha256") != candidate_digest
        or state.get("source_snapshot_sha256") != source_digest
    ):
        raise ReleaseGateError("Stage 3 source/candidate inventory digest drifted")
    delivery._validated_handoff_changes(source_files, candidate_files, handoff.get("files"))
    if not delivery._publisher_events_match(journal_state, journal_events, task_id=task_id):
        raise ReleaseGateError("Document receipts lack exact durable publisher events")
    expected: dict[str, str] | None = None
    for base in (candidate_files, source_files):
        candidate = delivery._document_published_files(base, journal_state, task_id=task_id)
        if candidate is not None:
            expected = candidate
            break
    if expected is None:
        raise ReleaseGateError("DELIVERY_READY has no exact published document overlay")
    delivery._validate_main_ack_fields(
        run_root,
        task_id=task_id,
        handoff_sha256=delivery._sha_file(run_root / "finalhandoff.json"),
        source_head=source_head,
        candidate_sha256=candidate_digest,
    )
    return task_id, source_files, candidate_files, expected


def _knowledge_history(
    home: str | Path,
    alias: str,
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    try:
        with KnowledgeReader.open(home, alias, project_id=project_id) as reader:
            return reader.get_release_receipts(task_id, limit=20)
    except Exception as exc:
        raise ReleaseGateError("Knowledge DB read-only receipt verification failed") from exc


def _validate_knowledge_receipt(gate: Mapping[str, Any], history: Mapping[str, Any]) -> None:
    bundle = gate["bundle"]
    index = gate["index_receipt"]
    evidence = gate["evidence"]
    event_id = index.get("index_event_id")
    if (
        not isinstance(event_id, str)
        or not _UUID_RE.fullmatch(event_id)
        or gate["journal_state"].get("index_receipt") != index
        or gate["journal_state"].get("status") != "DELIVERY_READY"
        or gate["journal_state"].get("ready") is not True
    ):
        raise ReleaseGateError("Index receipt is not the exact durable DELIVERY_READY receipt")
    if (
        index.get("candidate_sha256") is not None
        and index.get("candidate_sha256") != bundle.candidate_sha256
    ):
        raise ReleaseGateError("Index receipt candidate binding changed")
    if index.get("source_head") != bundle.source_head:
        raise ReleaseGateError("Index receipt source HEAD changed")
    if index.get("working_content_commit") is not None:
        raise ReleaseGateError("Pre-merge knowledge receipt unexpectedly claims a merge")
    tests = index.get("tests")
    if not isinstance(tests, list) or len(tests) != len(gate["commands"]):
        raise ReleaseGateError("Index receipt does not cover every prescribed QA command")
    index_docs = index.get("documents")
    docs_receipt = gate["journal_state"].get("documents_receipt", {}).get("documents", {})
    if not isinstance(index_docs, list) or len(index_docs) != len(docs_receipt):
        raise ReleaseGateError("Index receipt does not cover every published document")
    indexed = {
        row.get("path"): row.get("raw_sha256")
        for row in index_docs
        if isinstance(row, dict)
    }
    if indexed != {
        path: receipt.get("after_sha256")
        for path, receipt in docs_receipt.items()
        if isinstance(receipt, dict)
    }:
        raise ReleaseGateError("Index document hashes do not match publisher receipts")
    if (
        not isinstance(history, Mapping)
        or history.get("project_id") != gate.get("project_id")
        or history.get("task_id") != bundle.task_id
        or history.get("content_trust") != "quoted_data_not_instructions"
    ):
        raise ReleaseGateError("Knowledge history is not bound to the release project/task")
    items = history.get("items")
    if (
        not isinstance(items, list)
        or len(items) != 1
        or not isinstance(items[0], Mapping)
        or items[0].get("task_id") != bundle.task_id
    ):
        raise ReleaseGateError("Knowledge history must contain exactly the bound task")
    task_item = items[0]
    events = task_item.get("events")
    if not isinstance(events, list) or not any(
        item.get("id") == event_id and item.get("event_type") == "index_synced"
        for item in events
        if isinstance(item, dict)
    ):
        raise ReleaseGateError("Index event ID is absent from the read-only knowledge history")
    history_tests = task_item.get("tests")
    if not isinstance(history_tests, list):
        raise ReleaseGateError("Knowledge test receipts are unavailable")
    by_command = {}
    # Receipt readers order newest first. Older preserved failures must not
    # overwrite the current delivery proof merely because they occur later.
    for item in history_tests:
        if isinstance(item, dict) and isinstance(item.get("command"), str):
            by_command.setdefault(item["command"], item)
    for command in gate["commands"]:
        item = by_command.get(command)
        if (
            item is None
            or not isinstance(item.get("id"), str)
            or item.get("status") != "passed"
            or item.get("output_sha") != evidence.get("qa_sha256")
        ):
            raise ReleaseGateError("Knowledge receipt does not bind the exact QA evidence")
    changes = task_item.get("changes")
    if not isinstance(changes, list):
        raise ReleaseGateError("Knowledge document change receipts are unavailable")
    by_path = {}
    for item in changes:
        if isinstance(item, dict) and isinstance(item.get("file_path"), str):
            by_path.setdefault(item["file_path"], item)
    for path, after_sha in indexed.items():
        item = by_path.get(path.casefold())
        if item is None or item.get("source_raw_sha") != after_sha:
            raise ReleaseGateError("Knowledge document receipt drifted from the publisher bytes")


def _git_visible_source_inventory(repo: Path) -> dict[str, str]:
    """Hash only the Stage 3 Git-visible source view, never ignored private trees."""
    paths = BASE.source_paths(repo)
    repo_text = os.path.normcase(os.path.abspath(str(repo)))
    inventory: dict[str, str] = {}
    for value in paths:
        try:
            relative = BASE.safe_relative(value)
            selected = repo / Path(relative)
            _reject_reparse_chain(selected.absolute())
            resolved = selected.resolve(strict=True)
        except Exception as exc:
            raise ReleaseGateError("Git-visible source path is unsafe or unavailable") from exc
        resolved_text = os.path.normcase(os.path.abspath(str(resolved)))
        try:
            contained = os.path.commonpath((repo_text, resolved_text)) == repo_text
        except ValueError:
            contained = False
        if not contained or selected.is_symlink() or not selected.is_file():
            raise ReleaseGateError("Git-visible source path is not a contained regular file")
        inventory[relative] = _sha256_file(selected)
    return inventory


def validate_local_gate(
    *,
    run_root: str | Path,
    repo_root: str | Path,
    knowledge_home: str | Path,
    knowledge_db: str,
    project_id: str,
    task_id: str | None = None,
    journal: Any | None = None,
    bundle_loader: Callable[..., Any] = delivery.load_handoff_bundle,
    history_loader: Callable[..., Mapping[str, Any]] = _knowledge_history,
) -> LocalGate:
    """Reload and recompute Stage 3 acceptance instead of trusting booleans."""
    root = Path(run_root).resolve(strict=True)
    repo = Path(repo_root).resolve(strict=True)
    if task_id is None:
        handoff = delivery._strict_json(delivery._contained_file(root, "finalhandoff.json"))
        task_id = handoff.get("task_id")
    if not isinstance(task_id, str) or not _UUID_RE.fullmatch(task_id):
        raise ReleaseGateError("Stage 3 task ID is malformed")
    state, events = _journal_state(task_id, journal)
    if (
        state.get("status") != "DELIVERY_READY"
        or state.get("ready") is not True
        or state.get("task_id") != task_id
    ):
        raise ReleaseGateError("Release gate requires the exact DELIVERY_READY journal state")
    handoff_task, source_files, candidate_files, expected_files = _expected_published_files(
        root, state, events
    )
    if handoff_task != task_id:
        raise ReleaseGateError("Stage 3 task identity changed")
    runner = CommandRunner()
    top = runner.run(["git", "rev-parse", "--show-toplevel"], cwd=repo, timeout=15)
    head = runner.run(["git", "rev-parse", "HEAD"], cwd=repo, timeout=15)
    if top.exit_code != 0 or head.exit_code != 0:
        raise ReleaseGateError("Target repository identity cannot be verified")
    try:
        top_path = Path(top.stdout.decode("utf-8").strip()).resolve(strict=True)
        head_text = head.stdout.decode("ascii").strip()
    except (UnicodeError, OSError) as exc:
        raise ReleaseGateError("Target repository output is invalid") from exc
    if top_path != repo:
        raise ReleaseGateError("Selected path is not the exact Git worktree root")
    current_files = _git_visible_source_inventory(repo)
    if current_files != expected_files:
        raise ReleaseGateError("Current repository drift is not the exact published delivery bytes")
    handoff = delivery._strict_json(delivery._contained_file(root, "finalhandoff.json"))
    if head_text != handoff.get("source_head"):
        raise ReleaseGateError("Current repository HEAD drifted from the Stage 3 source")
    bundle = bundle_loader(
        run_root=root,
        repo_root=repo,
        repo_reader=lambda _root: (head_text, handoff.get("source_baseline_sha256", ""), candidate_files),
        require_main_ack=True,
    )
    if (
        bundle.task_id != task_id
        or bundle.source_head != handoff.get("source_head")
        or bundle.candidate_sha256 != handoff.get("candidate_sha256")
        or bundle.source_files != source_files
        or bundle.candidate_files != candidate_files
    ):
        raise ReleaseGateError("Strict Stage 3 bundle does not bind recomputed inventories")
    _validate_worker_receipts(bundle)
    _validate_stage4_unit_proof(bundle)
    evidence = _validate_persisted_evidence(state, bundle)
    expected_commands = tuple(bundle.expected_commands)
    if tuple(evidence.get("commands", ())) != expected_commands:
        raise ReleaseGateError("Persisted QA command set changed")
    documents = state.get("documents_receipt")
    index = state.get("index_receipt")
    if not isinstance(documents, dict) or not isinstance(index, dict):
        raise ReleaseGateError("Document and knowledge receipts are required")
    gate = LocalGate(
        bundle=bundle,
        journal_state=state,
        journal_events=tuple(events),
        current_files=current_files,
        candidate_files=candidate_files,
        main_ack_sha256=_sha256_file(root / "main-ack.json"),
        docs_receipt_sha256=_digest(documents),
        index_receipt_sha256=_digest(index),
        evidence=evidence,
    )
    history = history_loader(knowledge_home, knowledge_db, project_id, task_id)
    _validate_knowledge_receipt(
        {
            "bundle": bundle,
            "project_id": project_id,
            "journal_state": state,
            "evidence": evidence,
            "commands": expected_commands,
            "index_receipt": index,
        },
        history,
    )
    return gate


def _validate_authorization_against_gate(
    authorization: ReleaseAuthorization,
    gate: LocalGate,
) -> None:
    bundle = gate.bundle
    version = unit_qa.contract_version(tuple(getattr(bundle, "expected_commands", unit_qa.STAGE4_QA_COMMANDS)))
    if authorization.repository == "dabinglei2-hash/LifeOS-DevTeam" and version != 3:
        raise ReleaseAuthorizationError("DevTeam requires the split v3 QA contract")
    if version == 3 and (getattr(bundle, "unit_evidence", None) or {}).get("repository") != authorization.repository:
        raise ReleaseAuthorizationError("Split QA proof belongs to another repository")
    if (
        authorization.task_id != bundle.task_id
        or authorization.source_head != bundle.source_head
        or authorization.candidate_sha256 != bundle.candidate_sha256
        or authorization.repository not in APPROVED_REPOSITORIES
        or authorization.base_branch != DEFAULT_BASE_BRANCH
    ):
        raise ReleaseAuthorizationError("Authorization does not bind the validated Stage 3 delivery")
    candidates = {item.path: item for item in authorization.candidate_files}
    authorized_changes = {item.path: item for item in authorization.changes}
    required_candidates = {item["path"]: item for item in bundle.changed_files}
    documents = gate.journal_state.get("documents_receipt", {}).get("documents", {})
    for path, item in required_candidates.items():
        if item.get("kind") == "deleted":
            deletion = next(
                (entry for entry in authorization.deletions if entry.path == path),
                None,
            )
            if (
                deletion is None
                or deletion.source_before_sha256 != item.get("old_sha256")
            ):
                raise ReleaseAuthorizationError(
                    "Authorization omits or changes a Stage 3 candidate deletion"
                )
            continue
        change = authorized_changes.get(path)
        expected_after = item.get("new_sha256")
        if path in documents:
            receipt = documents[path]
            if (not delivery._document_receipt_matches(bundle.task_id, receipt)
                    or receipt.get("document") != path
                    or receipt.get("before_sha256") != expected_after):
                raise ReleaseAuthorizationError("Published candidate document lacks an exact before/after receipt")
            expected_after = receipt["after_sha256"]
        if change is None or (
            change.source_before_sha256 != item.get("old_sha256")
            or change.after_sha256 != expected_after
        ):
            raise ReleaseAuthorizationError(
                "Authorization omits or changes a Stage 3 candidate file"
            )
    delivery_paths = set(documents)
    extra = set(candidates) - {
        path
        for path, item in required_candidates.items()
        if item.get("kind") != "deleted"
    }
    if not extra <= delivery_paths:
        raise ReleaseAuthorizationError("Authorization adds undeclared candidate paths")
    for path in extra:
        if candidates[path].after_sha256 != documents[path].get("after_sha256"):
            raise ReleaseAuthorizationError("Delivered document hash is not bound by authorization")
    for change in authorization.changes:
        if gate.current_files.get(change.path) != change.after_sha256:
            raise ReleaseAuthorizationError("Current approved source bytes drifted")
    for deletion in authorization.deletions:
        if deletion.path in gate.current_files:
            raise ReleaseAuthorizationError("Authorized deletion still exists in the source view")


class ReleaseController:
    """Durable release state machine with injected Git/GitHub test seams."""

    def __init__(
        self,
        *,
        repo_root: str | Path,
        run_root: str | Path,
        store: ReleaseStore | None = None,
        transport: GitHubTransport | None = None,
        runner: CommandRunner | None = None,
        knowledge_history_loader: Callable[..., Mapping[str, Any]] = _knowledge_history,
        sleeper: Callable[[float], None] = time.sleep,
        poll_attempts: int = 12,
        poll_interval: float = 5.0,
        git_executable: str = "git",
        gh_executable: str = "gh",
        journal_namespace: str | None = None,
        repository: str = CANONICAL_REPOSITORY,
    ) -> None:
        if repository not in APPROVED_REPOSITORIES:
            raise ReleaseAuthorizationError("Controller repository must be explicitly approved")
        self.repository = repository
        self.repo_root = Path(repo_root).resolve(strict=True)
        self.run_root = Path(run_root).resolve(strict=True)
        self.store = store or ReleaseStore().initialize()
        self.runner = runner or CommandRunner(maximum_output=MAX_SOURCE_BYTES)
        self.transport = transport or GhCliTransport(self.runner, executable=gh_executable)
        self.github = GitHubClient(self.transport, sleeper=sleeper)
        self.knowledge_history_loader = knowledge_history_loader
        self.sleeper = sleeper
        if not 1 <= poll_attempts <= MAX_POLL_ATTEMPTS:
            raise ReleaseError("Poll attempts are outside their bound")
        if not 0 <= poll_interval <= 30:
            raise ReleaseError("Poll interval is outside its bound")
        self.poll_attempts = poll_attempts
        self.poll_interval = poll_interval
        self.git = git_executable
        self.journal = (
            FileJournal.open(acceptance_namespace=journal_namespace)
            if journal_namespace is not None
            else None
        )

    def _git(
        self,
        *args: str,
        cwd: str | Path | None = None,
        timeout: float = 30,
        allow_code_one: bool = False,
    ) -> CommandResult:
        result = self.runner.run(
            [self.git, *args],
            cwd=cwd or self.repo_root,
            timeout=timeout,
        )
        if result.exit_code != 0 and not (allow_code_one and result.exit_code == 1):
            raise ReleaseBlocked("Git command failed", reason=f"git_{args[0] if args else 'unknown'}_failed")
        return result

    def _git_text(self, *args: str, cwd: str | Path | None = None, allow_code_one: bool = False) -> str:
        result = self._git(*args, cwd=cwd, allow_code_one=allow_code_one)
        try:
            return result.stdout.decode("utf-8").strip()
        except UnicodeError as exc:
            raise ReleaseBlocked("Git returned invalid text") from exc

    def _verify_repository(self) -> None:
        top = self._git_text("rev-parse", "--show-toplevel")
        try:
            actual = Path(top).resolve(strict=True)
        except OSError as exc:
            raise ReleaseBlocked("Git top-level directory is unavailable") from exc
        if actual != self.repo_root:
            raise ReleaseBlocked("Git top-level does not equal the authorized repository")

    def _remote_url_lines(self, *, push: bool) -> list[str]:
        command = [
            self.git,
            "remote",
            "get-url",
            *(["--push"] if push else []),
            "--all",
            "origin",
        ]
        result = self.runner.run(command, cwd=self.repo_root, timeout=15)
        if result.exit_code != 0 or result.timed_out or result.bounded:
            raise ReleaseBlocked("Git origin route validation failed")
        try:
            text = result.stdout.decode("utf-8")
        except UnicodeError as exc:
            raise ReleaseBlocked("Git origin route validation failed") from exc
        lines = text.splitlines()
        if (
            len(lines) != 1
            or not lines[0]
            or lines[0] != lines[0].strip()
            or any(ord(character) < 0x20 for character in lines[0])
        ):
            raise ReleaseBlocked("Git origin route validation failed")
        return lines

    def _validate_origin_routes(self) -> None:
        """Validate the exact effective fetch and push routes before every use."""
        if any(
            os.environ.get(name)
            for name in (
                "GIT_DIR",
                "GIT_WORK_TREE",
            )
        ):
            raise ReleaseBlocked("Git origin route validation failed")
        rewrites = self.runner.run(
            [self.git, "config", "--get-regexp", r"^url\..*\.insteadof$"],
            cwd=self.repo_root,
            timeout=15,
        )
        if rewrites.timed_out or rewrites.bounded:
            raise ReleaseBlocked("Git origin route validation failed")
        if rewrites.exit_code == 0 and rewrites.stdout.strip():
            raise ReleaseBlocked("Git origin URL rewriting is not allowed")
        if rewrites.exit_code not in {0, 1}:
            raise ReleaseBlocked("Git origin route validation failed")
        fetch_urls = self._remote_url_lines(push=False)
        push_urls = self._remote_url_lines(push=True)
        for url in (*fetch_urls, *push_urls):
            selected_repository = getattr(self, "repository", CANONICAL_REPOSITORY)
            if url in {f"https://github.com/{selected_repository}.git", f"https://github.com/{selected_repository}"}:
                continue
            if selected_repository == CANONICAL_REPOSITORY and url in LEGACY_ORIGIN_URLS:
                self.github.resolve_repository()
                continue
            raise ReleaseBlocked("Git origin route is not the reviewed repository")

    @staticmethod
    def _network_failure_total(state: Mapping[str, Any]) -> int:
        rows = state.get("network_failures", [])
        if not isinstance(rows, list):
            raise ReleaseBoundaryError("Release network failure history is malformed")
        value = state.get("network_failure_total", len(rows))
        if type(value) is not int or value < 0:
            raise ReleaseBoundaryError("Release network failure counter is invalid")
        return value

    def _record_network_failure(
        self,
        state: dict[str, Any],
        failure: Mapping[str, Any],
    ) -> int:
        rows = state.setdefault("network_failures", [])
        if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
            raise ReleaseBoundaryError("Release network failure history is malformed")
        total = self._network_failure_total(state) + 1
        if total > MAX_NETWORK_FAILURE_TOTAL:
            raise ReleaseBlocked(
                "Persistent release network retry budget is exhausted",
                reason="NETWORK_FAILED",
            )
        rows.append(dict(failure))
        if len(rows) > MAX_STORED_NETWORK_FAILURES:
            del rows[:-MAX_STORED_NETWORK_FAILURES]
        state["network_failure_total"] = total
        return total

    def _state(self, authorization: ReleaseAuthorization) -> dict[str, Any]:
        try:
            state = self.store.load(authorization.task_id)
        except ReleaseStateNotFound:
            directory = self.store.task_dir(authorization.task_id, create=True)
            artifacts = [
                item.name
                for item in directory.iterdir()
                if item.name != ".lock"
            ]
            if artifacts:
                raise ReleaseBoundaryError(
                    "Release state is missing while prior task history or artifacts remain"
                )
            state = {
                "task_id": authorization.task_id,
                "status": "INITIALIZED",
                "ready": False,
                "authorization_sha256": authorization.authorization_sha256,
                "source_head": authorization.source_head,
                "candidate_sha256": authorization.candidate_sha256,
                "repository": authorization.repository,
                "base_branch": authorization.base_branch,
                "base_sha": authorization.base_sha,
                "branch": authorization.branch,
                "created_at": datetime.now(UTC).isoformat(),
                "network_failures": [],
                "network_failure_total": 0,
                "attempt": 0,
            }
            state = self.store.save(authorization.task_id, state)
        for key, expected in (
            ("task_id", authorization.task_id),
            ("authorization_sha256", authorization.authorization_sha256),
            ("source_head", authorization.source_head),
            ("candidate_sha256", authorization.candidate_sha256),
            ("repository", authorization.repository),
            ("branch", authorization.branch),
        ):
            if state.get(key) != expected:
                raise ReleaseConflict(f"Release state has a different {key}")
        return state

    def _save(self, state: dict[str, Any], status: str, **fields: Any) -> dict[str, Any]:
        state["status"] = status
        state["ready"] = status == "MERGED"
        state.update(fields)
        return self.store.save(state["task_id"], state)

    @staticmethod
    def _bind_repository_resolution(
        repository: Mapping[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        settings = {
            "allow_merge_commit": repository.get("allow_merge_commit") is True,
            "allow_rebase_merge": repository.get("allow_rebase_merge") is True,
            "allow_squash_merge": repository.get("allow_squash_merge") is True,
        }
        merge_method = next(
            (
                candidate
                for candidate, allowed in (
                    ("MERGE", settings["allow_merge_commit"]),
                    ("REBASE", settings["allow_rebase_merge"]),
                    ("SQUASH", settings["allow_squash_merge"]),
                )
                if allowed
            ),
            None,
        )
        resolution = {
            "full_name": repository.get("full_name"),
            "private": repository.get("private") is True,
            "visibility": repository.get("visibility"),
            "merge_methods": settings,
            "merge_method": merge_method,
        }
        prior = state.get("repository_resolution")
        if prior is not None:
            if (
                not isinstance(prior, Mapping)
                or prior.get("full_name") != resolution["full_name"]
                or prior.get("private") != resolution["private"]
                or (
                    prior.get("merge_method") is not None
                    and prior.get("merge_method") != merge_method
                )
                or (
                    isinstance(prior.get("merge_methods"), Mapping)
                    and prior.get("merge_methods") != settings
                )
            ):
                raise ReleaseConflict("Repository release settings changed on resume")
            if prior.get("merge_method") is None:
                resolution["merge_method_initialized"] = True
        return resolution

    def _capture(self, authorization: ReleaseAuthorization, state: dict[str, Any]) -> Path:
        directory = self.store.task_dir(authorization.task_id)
        staging = directory / "staging"
        if staging.exists() and (not staging.is_dir() or _is_reparse(staging) or staging.is_symlink()):
            raise ReleaseBoundaryError("Private staging path is unsafe")
        staging.mkdir(exist_ok=True)
        expected = {item.path: item.after_sha256 for item in authorization.changes}
        for path, artifact in self._walk_regular(staging):
            if artifact not in expected or _sha256_file(staging / path) != expected[artifact]:
                raise ReleaseConflict("Private staging contains unexpected or changed bytes")
        for change in authorization.changes:
            source = self.repo_root / change.path
            try:
                _reject_reparse_chain(source)
            except Exception as exc:
                raise ReleaseBoundaryError("Approved source traversal is unsafe") from exc
            if not source.is_file() or source.is_symlink():
                raise ReleaseAuthorizationError("Approved source file is missing or not regular")
            data = source.read_bytes()
            if len(data) > MAX_SOURCE_BYTES or _sha256(data) != change.after_sha256:
                raise ReleaseAuthorizationError("Approved source bytes changed before staging")
            destination = staging / change.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                _reject_reparse_chain(destination.parent.absolute())
            except Exception as exc:
                raise ReleaseBoundaryError("Private staging parent became a reparse path") from exc
            self.store._atomic_write(destination, data)
            if change.mode == "100755":
                try:
                    destination.chmod(0o755)
                except OSError:
                    pass
        manifest = {
            "task_id": authorization.task_id,
            "authorization_sha256": authorization.authorization_sha256,
            "files": [
                {**item.as_dict(), "staged_sha256": _sha256_file(staging / item.path)}
                for item in authorization.changes
            ],
            "deletions": [item.as_dict() for item in authorization.deletions],
        }
        self.store._atomic_write(
            directory / "manifest.json",
            (_canonical(manifest) + "\n").encode("utf-8"),
        )
        return staging

    @staticmethod
    def _walk_regular(root: Path) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for current, directories, files in os.walk(root, topdown=True):
            base = Path(current)
            for name in directories:
                child = base / name
                if _is_reparse(child) or child.is_symlink():
                    raise ReleaseBoundaryError("Private state traversal contains a reparse directory")
            for name in files:
                child = base / name
                if _is_reparse(child) or child.is_symlink() or not child.is_file():
                    raise ReleaseBoundaryError("Private state traversal contains an unsafe file")
                rows.append((child.relative_to(root).as_posix(), child.relative_to(root).as_posix()))
        return rows

    def _fetch_base(self, authorization: ReleaseAuthorization, state: dict[str, Any]) -> None:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            self._validate_origin_routes()
            result = self.runner.run(
                [
                    self.git,
                    "fetch",
                    "--no-tags",
                    "origin",
                    f"{authorization.base_branch}:refs/remotes/origin/{authorization.base_branch}",
                ],
                cwd=self.repo_root,
                timeout=60,
            )
            if result.exit_code == 0 and not result.timed_out and not result.bounded:
                remote = self._git_text(
                    "rev-parse", f"refs/remotes/origin/{authorization.base_branch}"
                )
                if remote != authorization.base_sha:
                    raise ReleaseBlocked("Remote base branch drifted from controller authorization")
                return
            last_error = ReleaseBlocked("Remote base fetch failed", reason="NETWORK_FAILED")
            self._record_network_failure(
                state,
                {"operation": "fetch_base", "attempt": attempt, "status": "NETWORK_FAILED"}
            )
            self._save(state, "NETWORK_RETRY")
            if attempt < 3:
                self.sleeper(0.2 * attempt)
        raise last_error or ReleaseBlocked("Remote base fetch failed", reason="NETWORK_FAILED")

    def _worktree_path(self, authorization: ReleaseAuthorization) -> Path:
        return self.store.task_dir(authorization.task_id) / "worktree"

    def _valid_commit(self, commit: str, authorization: ReleaseAuthorization) -> None:
        self._verify_base_manifest(authorization.base_sha, authorization)
        if not _SHA40_RE.fullmatch(commit):
            raise ReleaseConflict("Existing task commit SHA is malformed")
        parent = self._git_text("rev-list", "--parents", "-n", "1", commit).split()
        if len(parent) != 2 or parent[1] != authorization.base_sha:
            raise ReleaseConflict("Existing task commit does not have the exact remote base parent")
        message = self._git_text("show", "-s", "--format=%B", commit)
        if authorization.task_id not in message:
            raise ReleaseConflict("Existing task commit is not bound to this TASK_ID")
        diff = self._diff_manifest(authorization.base_sha, commit)
        expected = {
            item.path: "added" if item.git_before_sha256 is None else "modified"
            for item in authorization.changes
        }
        expected.update({item.path: "deleted" for item in authorization.deletions})
        if diff != expected:
            raise ReleaseConflict("Existing task commit diff does not match authorization")
        self._verify_commit_bytes(commit, authorization)

    def _verify_base_manifest(
        self,
        base: str,
        authorization: ReleaseAuthorization,
    ) -> None:
        """Verify Git-before hashes against the exact remote base tree."""
        for change in authorization.changes:
            entry = self._base_tree_entry(base, change.path)
            if change.git_before_sha256 is None:
                if entry is not None:
                    raise ReleaseAuthorizationError("Authorized new file already exists at the base")
                continue
            if entry is None or entry[1] != change.git_before_sha256:
                raise ReleaseAuthorizationError("Base file bytes do not match authorization")
        for deletion in authorization.deletions:
            entry = self._base_tree_entry(base, deletion.path)
            if entry is None or entry[1] != deletion.git_before_sha256:
                raise ReleaseAuthorizationError("Deletion base bytes do not match authorization")

    def _base_tree_entry(self, base: str, path: str) -> tuple[str, str] | None:
        """Return one validated regular blob, or exact absence from successful ls-tree."""
        tree = self.runner.run(
            [
                self.git,
                "ls-tree",
                "-z",
                "--full-tree",
                base,
                "--",
                f":(literal){path}",
            ],
            cwd=self.repo_root,
            timeout=30,
        )
        if tree.exit_code != 0 or tree.timed_out or tree.bounded:
            raise ReleaseAuthorizationError("Base tree lookup did not complete exactly")
        if not tree.stdout:
            return None
        if not tree.stdout.endswith(b"\0") or tree.stdout.count(b"\0") != 1:
            raise ReleaseAuthorizationError("Base tree lookup returned malformed or multiple records")
        try:
            metadata, encoded_path = tree.stdout[:-1].split(b"\t", 1)
            mode_raw, kind_raw, object_raw = metadata.split(b" ", 2)
            mode = mode_raw.decode("ascii")
            kind = kind_raw.decode("ascii")
            object_id = object_raw.decode("ascii")
            actual_path = encoded_path.decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise ReleaseAuthorizationError("Base tree lookup returned a malformed record") from exc
        if (
            actual_path != path
            or kind != "blob"
            or mode not in {"100644", "100755"}
            or not _SHA40_RE.fullmatch(object_id)
        ):
            raise ReleaseAuthorizationError("Base tree lookup returned an unsupported object")
        blob = self.runner.run(
            [self.git, "cat-file", "blob", object_id],
            cwd=self.repo_root,
            timeout=30,
        )
        if (
            blob.exit_code != 0
            or blob.timed_out
            or blob.bounded
            or len(blob.stdout) > MAX_SOURCE_BYTES
        ):
            raise ReleaseAuthorizationError("Base blob lookup did not complete exactly")
        return mode, _sha256(blob.stdout)

    def _diff_manifest(self, base: str, commit: str) -> dict[str, str]:
        raw = self._git("diff", "--name-status", "--no-renames", base, commit)
        try:
            lines = raw.stdout.decode("utf-8").splitlines()
        except UnicodeError as exc:
            raise ReleaseConflict("Git diff manifest is invalid") from exc
        result: dict[str, str] = {}
        for line in lines:
            parts = line.split("\t")
            if len(parts) != 2:
                raise ReleaseConflict("Git diff manifest contains an unexpected record")
            code, path = parts
            kind = {"A": "added", "M": "modified", "D": "deleted"}.get(code)
            if kind is None:
                raise ReleaseConflict("Git diff manifest contains an unsupported status")
            result[_safe_relative(path, "diff path")] = kind
        return result

    def _verify_commit_bytes(self, commit: str, authorization: ReleaseAuthorization) -> None:
        for change in authorization.changes:
            result = self.runner.run(
                [self.git, "cat-file", "blob", f"{commit}:{change.path}"],
                cwd=self.repo_root,
                timeout=30,
            )
            if (
                result.exit_code != 0
                or result.timed_out
                or result.bounded
                or _sha256(result.stdout) != change.after_sha256
            ):
                raise ReleaseConflict("Committed blob bytes do not match authorization")
        tree = self._git("ls-tree", "-r", "-z", commit)
        for record in tree.stdout.split(b"\0"):
            if not record:
                continue
            try:
                meta, path = record.split(b"\t", 1)
                mode = meta.split(b" ", 1)[0].decode("ascii")
                decoded = path.decode("utf-8")
            except (ValueError, UnicodeError) as exc:
                raise ReleaseConflict("Git tree output is malformed") from exc
            if decoded in authorization.all_paths and mode == "120000":
                raise ReleaseConflict("Outgoing commit contains a symlink")
            if decoded in {item.path for item in authorization.changes} and mode not in {"100644", "100755"}:
                raise ReleaseConflict("Outgoing commit mode is not a regular file")

    def _assert_no_active_hooks(self, cwd: Path) -> None:
        result = self.runner.run(
            [self.git, "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
            cwd=cwd, timeout=15,
        )
        if result.exit_code != 0 or result.timed_out or result.bounded:
            raise ReleaseBlocked("Effective Git hooks could not be verified")
        try:
            value = result.stdout.decode("utf-8").strip()
            if not value or "\n" in value or "\r" in value:
                raise ValueError("ambiguous hooks path")
            hooks = Path(value)
            if not hooks.is_absolute():
                raise ValueError("hooks path is not absolute")
            _reject_reparse_chain(hooks)
            if not hooks.exists():
                return
            if not hooks.is_dir():
                raise ValueError("hooks path is not a directory")
            for path in hooks.iterdir():
                _reject_reparse_chain(path)
                if path.is_file() and not path.name.endswith(".sample"):
                    raise ReleaseBlocked("Active Git hooks require separate review; none were bypassed or executed")
                if not path.is_file():
                    raise ValueError("hooks contain a non-regular entry")
        except ReleaseBlocked:
            raise
        except (ValueError, OSError) as exc:
            raise ReleaseBlocked("Effective Git hooks are unsafe or ambiguous") from exc

    def _stage_and_commit(
        self,
        authorization: ReleaseAuthorization,
        state: dict[str, Any],
    ) -> str:
        self._assert_no_active_hooks(self.repo_root)
        self._verify_base_manifest(authorization.base_sha, authorization)
        existing_commit = state.get("commit_sha")
        if existing_commit:
            self._valid_commit(existing_commit, authorization)
            return existing_commit
        branch_ref = f"refs/heads/{authorization.branch}"
        branch = self._git("rev-parse", "--verify", "--quiet", branch_ref, allow_code_one=True)
        if branch.exit_code == 0:
            commit = self._git_text("rev-parse", branch_ref)
            self._valid_commit(commit, authorization)
            self._save(state, "COMMITTED", commit_sha=commit)
            return commit
        worktree = self._worktree_path(authorization)
        if worktree.exists():
            raise ReleaseConflict("Private release worktree exists without the expected task branch")
        staging = self._capture(authorization, state)
        self._save(state, "COMMIT_INTENT", commit_intent={"base_sha": authorization.base_sha, "branch": authorization.branch})
        created = self.runner.run(
            [self.git, "worktree", "add", "--detach", str(worktree), authorization.base_sha],
            cwd=self.repo_root,
            timeout=60,
        )
        if created.exit_code != 0 or created.timed_out or created.bounded:
            raise ReleaseBlocked("Private Git worktree could not be created")
        switched = self.runner.run(
            [self.git, "switch", "--create", authorization.branch, "--no-track", authorization.base_sha],
            cwd=worktree,
            timeout=30,
        )
        if switched.exit_code != 0:
            raise ReleaseBlocked("Task branch could not be created without overwriting")
        self._assert_no_active_hooks(worktree)
        for deletion in authorization.deletions:
            result = self.runner.run([self.git, "rm", "--", deletion.path], cwd=worktree, timeout=30)
            if result.exit_code != 0:
                raise ReleaseBlocked("Authorized deletion could not be staged")
        for change in authorization.changes:
            destination = worktree / change.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                _reject_reparse_chain(destination.parent.absolute())
            except Exception as exc:
                raise ReleaseBoundaryError("Task worktree parent became a reparse path") from exc
            self.store._atomic_write(destination, (staging / change.path).read_bytes())
            if change.mode == "100755":
                try:
                    destination.chmod(0o755)
                except OSError:
                    pass
            staged = self.runner.run([self.git, "-c", "core.autocrlf=false", "add", "--", change.path], cwd=worktree, timeout=30)
            if staged.exit_code != 0:
                raise ReleaseBlocked("Approved path could not be staged individually")
            if change.mode == "100755":
                self.runner.run(
                    [self.git, "update-index", "--chmod=+x", "--", change.path],
                    cwd=worktree,
                    timeout=30,
                )
        index = self.runner.run(
            [self.git, "ls-files", "--stage", "-z", "--", *authorization.all_paths],
            cwd=worktree,
            timeout=30,
        )
        if index.exit_code != 0:
            raise ReleaseBlocked("Git index manifest cannot be read")
        indexed: dict[str, str] = {}
        for record in index.stdout.split(b"\0"):
            if not record:
                continue
            try:
                meta, path = record.split(b"\t", 1)
                mode, object_id, stage_number = meta.decode("ascii").split(" ")
                decoded = path.decode("utf-8")
            except (ValueError, UnicodeError) as exc:
                raise ReleaseConflict("Git index output is malformed") from exc
            if stage_number != "0" or mode == "120000":
                raise ReleaseConflict("Git index contains a conflict or symlink")
            indexed[decoded] = f"{mode}:{object_id}"
        expected_paths = {item.path for item in authorization.changes}
        if set(indexed) != expected_paths:
            raise ReleaseConflict("Staged index is not the exact authorized manifest")
        for change in authorization.changes:
            mode = indexed[change.path].split(":", 1)[0]
            if mode != change.mode:
                raise ReleaseConflict("Staged file mode does not match authorization")
            blob = self.runner.run(
                [self.git, "cat-file", "blob", f":{change.path}"],
                cwd=worktree,
                timeout=30,
            )
            if blob.exit_code != 0 or _sha256(blob.stdout) != change.after_sha256:
                raise ReleaseConflict("Staged bytes do not match authorization")
        status = self.runner.run(
            [self.git, "status", "--porcelain=v2", "-z", "--untracked-files=all"],
            cwd=worktree,
            timeout=30,
        )
        for record in status.stdout.split(b"\0"):
            if not record or record.startswith(b"# "):
                continue
            if record.startswith((b"? ", b"! ", b"u ")):
                raise ReleaseConflict("Private task worktree has untracked or unmerged content")
            if record.startswith((b"1 ", b"2 ")):
                try:
                    xy = record.split(b" ", 2)[1].decode("ascii")
                except (IndexError, UnicodeError) as exc:
                    raise ReleaseConflict("Private task worktree status is malformed") from exc
                if len(xy) != 2 or xy[1] != ".":
                    raise ReleaseConflict("Private task worktree has unstaged content")
                continue
            raise ReleaseConflict("Private task worktree status is unsupported")
        committed = self.runner.run(
            [
                self.git,
                "-c",
                "user.name=LADS Automation",
                "-c",
                "user.email=lads-automation@localhost.invalid",
                "commit",
                "-m",
                f"LADS {authorization.task_id}: {authorization.summary[:120]}",
            ],
            cwd=worktree,
            timeout=60,
        )
        if committed.exit_code != 0:
            raise ReleaseBlocked("Release commit failed in the isolated worktree")
        commit = self._git_text("rev-parse", "HEAD", cwd=worktree)
        self._valid_commit(commit, authorization)
        return self._save(state, "COMMITTED", commit_sha=commit)["commit_sha"]

    def _push_once(self, authorization: ReleaseAuthorization, state: dict[str, Any]) -> None:
        commit = str(state.get("commit_sha") or "")
        self._valid_commit(commit, authorization)
        remote_ref = f"refs/heads/{authorization.branch}"
        self._validate_origin_routes()
        try:
            remote = self._git("ls-remote", "--heads", "origin", remote_ref)
        except ReleaseBlocked:
            self._record_network_failure(
                state,
                {"operation": "ls_remote", "status": "NETWORK_FAILED"},
            )
            self._save(state, "NETWORK_RETRY")
            raise ReleaseBlocked("Task branch lookup failed", reason="NETWORK_FAILED")
        try:
            lines = remote.stdout.decode("ascii", errors="strict").splitlines() if remote.stdout.strip() else []
        except UnicodeError as exc:
            self._record_network_failure(
                state,
                {"operation": "ls_remote", "status": "MALFORMED"},
            )
            self._save(state, "NETWORK_RETRY")
            raise ReleaseBlocked("Task branch lookup was malformed", reason="NETWORK_FAILED") from exc
        if len(lines) > 1:
            raise ReleaseConflict("Task branch lookup returned multiple records")
        if lines:
            fields = lines[0].split()
            if len(fields) != 2 or fields[0] != commit or fields[1] != remote_ref:
                raise ReleaseConflict("Existing remote task branch is not this exact commit")
            self._save(state, "PUSHED", pushed_commit=commit)
            return
        self._save(
            state,
            "PUSH_INTENT",
            push_intent={"commit_sha": commit, "branch": authorization.branch, "force": False},
        )
        self._validate_origin_routes()
        pushed = self.runner.run(
            [self.git, "push", "--porcelain", "origin", f"{remote_ref}:{remote_ref}"],
            cwd=self.repo_root,
            timeout=90,
        )
        if pushed.exit_code != 0 or pushed.timed_out or pushed.bounded:
            # Ambiguous mutation: reconcile once and never blind-retry.
            self._validate_origin_routes()
            try:
                reconciled = self._git("ls-remote", "--heads", "origin", remote_ref)
            except ReleaseBlocked:
                reconciled = None
            if reconciled is not None and reconciled.stdout.strip():
                try:
                    reconciled_lines = reconciled.stdout.decode(
                        "ascii",
                        errors="strict",
                    ).splitlines()
                except UnicodeError:
                    reconciled_lines = []
                fields = reconciled_lines[0].split() if len(reconciled_lines) == 1 else []
                if len(fields) == 2 and fields[0] == commit and fields[1] == remote_ref:
                    self._save(state, "PUSHED", pushed_commit=commit)
                    return
            self._record_network_failure(
                state,
                {"operation": "push_reconcile", "status": "NETWORK_FAILED"},
            )
            self._save(state, "PUSH_PENDING", push_error="GitHub task-branch push did not reconcile")
            raise ReleaseBlocked("Task-branch push is ambiguous or failed", reason="PUSH_PENDING")
        self._save(state, "PUSHED", pushed_commit=commit)

    def _pr_list(self, authorization: ReleaseAuthorization) -> list[PullRequest]:
        route = (
            f"/repos/{authorization.repository}/pulls?state=all&head="
            f"{quote(CANONICAL_OWNER, safe='')}:{quote(authorization.branch, safe='')}"
            f"&base={quote(authorization.base_branch, safe='')}&per_page=100"
        )
        value = self.github.get(route)
        if not isinstance(value, list):
            raise GitHubTransportError("Pull-request lookup response is malformed")
        matches = [
            PullRequest.from_api(item)
            for item in value
            if isinstance(item, dict)
            and isinstance(item.get("head"), dict)
            and item["head"].get("ref") == authorization.branch
            and isinstance(item.get("base"), dict)
            and item["base"].get("ref") == authorization.base_branch
        ]
        return matches

    def _pr_body(self, authorization: ReleaseAuthorization, gate: LocalGate) -> str:
        tests = "\n".join(f"- `{command}`" for command in gate.bundle.expected_commands)
        paths = "\n".join(f"- `{path}`" for path in authorization.all_paths)
        worker_lines = "\n".join(
            f"- {item['role']}: {item['status']} via {item['provider']} / {item['model']}"
            for item in gate.bundle.worker_stage_receipts
        )
        body = (
            f"## TASK_ID\n\n{authorization.task_id}\n\n"
            f"## Demand\n\n{authorization.demand}\n\n"
            f"## Summary\n\n{authorization.summary}\n\n"
            f"## Exact paths\n\n{paths}\n\n"
            f"## Tests\n\n{tests}\n\n"
            "## Independent DeepSeek review\n\n"
            "The following are controller-recorded independent CLI worker results, "
            "not a GitHub human review:\n\n"
            f"{worker_lines}\n\n"
            f"Final Reviewer status: {gate.bundle.reviewer_receipt['status']}.\n\n"
            "## Controller acceptance\n\n"
            "GPT/controller owns this release action. The `LADS / controller` commit "
            "status is evidence-bound machine acceptance and is not a human review.\n\n"
            "## Safety and docs/index state\n\n"
            f"- Stage 3 state: `{gate.journal_state.get('status')}`\n"
            f"- Documents receipt: `{gate.docs_receipt_sha256}`\n"
            f"- Index receipt: `{gate.index_receipt_sha256}`\n"
            "- Git operations use an isolated worktree and exact per-path staging.\n\n"
            f"## Risk\n\n{authorization.reason}\n\n"
            f"Declared flags: {', '.join(authorization.risk_flags) or 'none'}\n"
        )
        _validate_public_text(body, "PR body")
        return body

    def _ensure_pr(
        self,
        authorization: ReleaseAuthorization,
        gate: LocalGate,
        state: dict[str, Any],
    ) -> PullRequest:
        matches = self._pr_list(authorization)
        if len(matches) > 1:
            raise ReleaseConflict("More than one PR uses the exact task head/base")
        if matches:
            pr = matches[0]
            if pr.head_sha != state.get("commit_sha"):
                raise ReleaseConflict("Existing PR head is not the committed manifest")
            if not pr.merged and (pr.state != "open" or pr.draft):
                raise ReleaseConflict("Existing exact PR is closed or draft")
            if not pr.merged:
                self._save(state, "PR_OPEN", pr_number=pr.number, pr_url=pr.url, pr_node_id=pr.node_id)
            return pr
        self._save(state, "PR_INTENT", pr_intent={"head": authorization.branch, "base": authorization.base_branch})
        body = self._pr_body(authorization, gate)
        title = f"LADS {authorization.task_id[:8]}: {authorization.summary[:100]}"
        _validate_public_text(title, "PR title")
        try:
            created = self.github.mutate(
                "POST",
                f"/repos/{authorization.repository}/pulls",
                {
                    "title": title,
                    "head": authorization.branch,
                    "base": authorization.base_branch,
                    "body": body,
                    "draft": False,
                },
            )
        except GitHubTransportError:
            reconciled = self._pr_list(authorization)
            if len(reconciled) != 1:
                self._save(state, "PR_PENDING", pr_error="PR create did not reconcile")
                raise ReleaseBlocked("PR create is ambiguous or failed", reason="PR_PENDING")
            created = {
                "number": reconciled[0].number,
                "node_id": reconciled[0].node_id,
                "html_url": reconciled[0].url,
                "state": reconciled[0].state,
                "draft": reconciled[0].draft,
                "mergeable": reconciled[0].mergeable,
                "head": {"sha": reconciled[0].head_sha, "ref": reconciled[0].head_ref},
                "base": {"ref": reconciled[0].base_ref},
                "merged": reconciled[0].merged,
                "merge_commit_sha": reconciled[0].merge_commit_sha,
            }
        if not isinstance(created, dict):
            raise GitHubTransportError("Created PR response is malformed")
        pr = PullRequest.from_api(created)
        if (
            not pr.number
            or not pr.node_id
            or pr.head_ref != authorization.branch
            or pr.base_ref != authorization.base_branch
            or pr.head_sha != state["commit_sha"]
        ):
            raise ReleaseConflict("Created PR does not bind the exact head/base/commit")
        return pr

    def _branch_protection(self, authorization: ReleaseAuthorization) -> dict[str, Any]:
        route = f"/repos/{authorization.repository}/branches/{quote(authorization.base_branch, safe='')}/protection"
        try:
            value = self.github.get(route)
        except GitHubTransportError as exc:
            ruleset = self._ruleset_protection(authorization)
            if ruleset is not None:
                return ruleset
            raise ReleaseBlocked(
                "Required branch protection or ruleset is unavailable",
                state="AWAITING_APPROVAL",
                reason="PROTECTION_REQUIRED",
            ) from exc
        if not isinstance(value, dict):
            raise ReleaseBlocked("Branch protection response is malformed", state="AWAITING_APPROVAL")
        enforce = value.get("enforce_admins")
        checks = value.get("required_status_checks")
        if (
            not isinstance(enforce, dict)
            or enforce.get("enabled") is not True
            or not isinstance(checks, dict)
            or checks.get("strict") is not True
        ):
            ruleset = self._ruleset_protection(authorization)
            if ruleset is not None:
                return ruleset
            raise ReleaseBlocked(
                "Branch protection is absent or does not enforce administrators and current heads",
                state="AWAITING_APPROVAL",
                reason="PROTECTION_REFUSED",
            )
        contexts: set[str] = set()
        raw_contexts = checks.get("contexts")
        if isinstance(raw_contexts, list):
            contexts.update(item for item in raw_contexts if isinstance(item, str))
        raw_checks = checks.get("checks")
        if isinstance(raw_checks, list):
            contexts.update(
                item["context"]
                for item in raw_checks
                if isinstance(item, dict) and isinstance(item.get("context"), str)
            )
        required = {item.context for item in REQUIRED_CHECKS}
        if not required <= contexts or len(contexts) > MAX_PROTECTED_CHECKS:
            ruleset = self._ruleset_protection(authorization)
            if ruleset is not None:
                return ruleset
            raise ReleaseBlocked(
                "Required CI/docs/security checks are not all protected",
                state="AWAITING_APPROVAL",
                reason="REQUIRED_CHECKS_NOT_PROTECTED",
            )
        for name in ("allow_force_pushes", "allow_deletions"):
            setting = value.get(name)
            if not isinstance(setting, dict) or setting.get("enabled") is not False:
                ruleset = self._ruleset_protection(authorization)
                if ruleset is not None:
                    return ruleset
                raise ReleaseBlocked(
                    "Protection permits force pushes or branch deletion",
                    state="AWAITING_APPROVAL",
                    reason="PROTECTION_REFUSED",
                )
        return value

    def _ruleset_protection(
        self,
        authorization: ReleaseAuthorization,
    ) -> dict[str, Any] | None:
        """Accept an active branch ruleset as native protection without mutation."""
        try:
            listing = self.github.get(
                f"/repos/{authorization.repository}/rulesets"
                "?includes_parents=true&targets=branch&per_page=100"
            )
        except GitHubTransportError:
            return None
        if not isinstance(listing, list) or len(listing) > 100:
            return None
        required = {item.context for item in REQUIRED_CHECKS}
        observed_checks: set[str] = set()
        deletion_blocked = False
        non_fast_forward = False
        current_head_required = False
        for summary in listing:
            if (
                not isinstance(summary, dict)
                or summary.get("target") != "branch"
                or summary.get("enforcement") != "active"
            ):
                continue
            identifier = summary.get("id")
            if type(identifier) is not int or identifier <= 0:
                continue
            try:
                detail = self.github.get(
                    f"/repos/{authorization.repository}/rulesets/{identifier}"
                )
            except GitHubTransportError:
                continue
            if not isinstance(detail, dict) or detail.get("enforcement") != "active":
                continue
            if detail.get("bypass_actors") not in (None, []):
                continue
            conditions = detail.get("conditions")
            refs = conditions.get("ref_name") if isinstance(conditions, dict) else None
            includes = refs.get("include") if isinstance(refs, dict) else None
            if not isinstance(includes, list) or not any(
                value in {
                    "~ALL",
                    "~DEFAULT_BRANCH",
                    f"refs/heads/{authorization.base_branch}",
                }
                for value in includes
                if isinstance(value, str)
            ):
                continue
            rules = detail.get("rules")
            if not isinstance(rules, list):
                continue
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                kind = rule.get("type")
                if kind == "deletion":
                    deletion_blocked = True
                elif kind == "non_fast_forward":
                    non_fast_forward = True
                elif kind == "required_status_checks":
                    parameters = rule.get("parameters")
                    if not isinstance(parameters, dict):
                        continue
                    if parameters.get("strict_required_status_checks_policy") is True:
                        current_head_required = True
                    checks = parameters.get("required_status_checks")
                    if isinstance(checks, list):
                        observed_checks.update(
                            item["context"]
                            for item in checks
                            if isinstance(item, dict) and isinstance(item.get("context"), str)
                        )
        if (
            required <= observed_checks
            and deletion_blocked
            and non_fast_forward
            and current_head_required
        ):
            return {
                "source": "ruleset",
                "required_checks": sorted(observed_checks),
                "deletion_blocked": True,
                "non_fast_forward": True,
            }
        return None

    def _pr_context(self, authorization: ReleaseAuthorization, number: int) -> dict[str, Any]:
        query = (
            "query($owner:String!,$name:String!,$number:Int!){"
            "repository(owner:$owner,name:$name){pullRequest(number:$number){"
            "id number headRefOid baseRefName state isDraft merged "
            "reviewDecision reviewThreads(first:100){totalCount pageInfo{hasNextPage}"
            "nodes{isResolved isOutdated}}"
            "autoMergeRequest{enabledAt mergeMethod}}}}"
        )
        result = self.github.transport.graphql(
            query,
            {"owner": CANONICAL_OWNER, "name": authorization.repository.split("/", 1)[1], "number": number},
        )
        if result.get("errors"):
            raise GitHubTransportError("Pull-request review context query failed")
        try:
            return result["data"]["repository"]["pullRequest"]
        except (KeyError, TypeError) as exc:
            raise GitHubTransportError("Pull-request review context is malformed") from exc

    def _check_snapshot(
        self,
        authorization: ReleaseAuthorization,
        sha: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        runs_value = self.github.get(
            f"/repos/{authorization.repository}/commits/{sha}/check-runs?filter=latest&per_page=100"
        )
        runs = runs_value.get("check_runs") if isinstance(runs_value, dict) else None
        if not isinstance(runs, list):
            raise GitHubTransportError("GitHub check-run response is malformed")
        required = {item.context for item in REQUIRED_WORKFLOW_CHECKS}
        observations: dict[str, list[dict[str, Any]]] = {
            context: [] for context in required
        }
        for run in runs:
            if not isinstance(run, dict) or not isinstance(run.get("name"), str):
                continue
            name = run["name"]
            if name not in required:
                continue
            app = run.get("app")
            suite = run.get("check_suite")
            run_id = run.get("id")
            if (
                not isinstance(app, Mapping)
                or app.get("slug") != "github-actions"
                or run.get("head_sha") != sha
                or type(run_id) is not int
                or run_id <= 0
            ):
                continue
            suite_id = suite.get("id") if isinstance(suite, Mapping) else None
            if type(suite_id) is not int or suite_id <= 0:
                continue
            observations[name].append(
                {
                    "name": name,
                    "id": run_id,
                    "check_suite_id": suite_id,
                    "status": run.get("status"),
                    "conclusion": run.get("conclusion"),
                    "source": "check_run",
                    "app_slug": "github-actions",
                }
            )
        missing = sorted(context for context, records in observations.items() if not records)
        if missing:
            return (
                "WAITING_CI",
                [
                    {
                        "context": context,
                        "missing": True,
                        "observations": [],
                    }
                    for context in missing
                ],
            )
        summary: list[dict[str, Any]] = []
        has_pending = False
        failed = False
        for context in sorted(required):
            records = observations[context]
            if len(records) != 1:
                failed = True
                summary.append({"context": context, "observations": records})
                continue
            latest = records[0]
            status = latest["status"]
            conclusion = latest["conclusion"]
            if status != "completed" or conclusion is None:
                has_pending = True
            elif conclusion != "success":
                failed = True
            summary.append({"context": context, "observations": records})
        if failed:
            return "CI_FAILED", summary
        if has_pending:
            return "WAITING_CI", summary
        summary.clear()
        for context in sorted(required):
            check = observations[context][0]
            workflow = next(
                item for item in REQUIRED_WORKFLOW_CHECKS if item.context == context
            )
            workflow_path = REQUIRED_WORKFLOW_PATHS[context]
            workflow_file = workflow_path.rsplit("/", 1)[-1]
            workflow_value = self.github.get(
                f"/repos/{authorization.repository}/actions/workflows/"
                f"{quote(workflow_file, safe='')}"
            )
            workflow_id = (
                workflow_value.get("id")
                if isinstance(workflow_value, dict)
                else None
            )
            if (
                type(workflow_id) is not int
                or workflow_id <= 0
                or workflow_value.get("name") != workflow.workflow
                or workflow_value.get("path") != workflow_path
            ):
                failed = True
                summary.append(
                    {
                        "context": context,
                        "observations": observations[context],
                        "provenance_error": "workflow_identity",
                    }
                )
                continue
            runs_route = (
                f"/repos/{authorization.repository}/actions/workflows/{workflow_id}/runs"
                f"?head_sha={quote(sha, safe='')}"
                f"&check_suite_id={check['check_suite_id']}&per_page=100"
            )
            workflow_runs_value = self.github.get(runs_route)
            workflow_runs = (
                workflow_runs_value.get("workflow_runs")
                if isinstance(workflow_runs_value, dict)
                else None
            )
            total_count = (
                workflow_runs_value.get("total_count")
                if isinstance(workflow_runs_value, dict)
                else None
            )
            if (
                type(total_count) is not int
                or total_count < 0
                or not isinstance(workflow_runs, list)
                or total_count != len(workflow_runs)
            ):
                failed = True
                summary.append(
                    {
                        "context": context,
                        "observations": observations[context],
                        "provenance_error": "workflow_runs",
                        "workflow_path": workflow_path,
                        "workflow_id": workflow_id,
                    }
                )
                continue
            if total_count == 0:
                has_pending = True
                summary.append(
                    {
                        "context": context,
                        "observations": observations[context],
                        "workflow_path": workflow_path,
                        "workflow_id": workflow_id,
                        "provenance_missing": True,
                    }
                )
                continue
            if total_count != 1:
                failed = True
                summary.append(
                    {
                        "context": context,
                        "observations": observations[context],
                        "workflow_path": workflow_path,
                        "workflow_id": workflow_id,
                        "provenance_error": "ambiguous_workflow_run",
                    }
                )
                continue
            workflow_run = workflow_runs[0]
            workflow_run_id = (
                workflow_run.get("id")
                if isinstance(workflow_run, dict)
                else None
            )
            if (
                type(workflow_run_id) is not int
                or workflow_run_id <= 0
                or workflow_run.get("workflow_id") != workflow_id
                or workflow_run.get("head_sha") != sha
                or workflow_run.get("check_suite_id") != check["check_suite_id"]
                or workflow_run.get("path") != workflow_path
                or workflow_run.get("status") != "completed"
                or workflow_run.get("conclusion") != "success"
            ):
                failed = True
                summary.append(
                    {
                        "context": context,
                        "observations": observations[context],
                        "workflow_path": workflow_path,
                        "workflow_id": workflow_id,
                        "provenance_error": "workflow_run_binding",
                    }
                )
                continue
            check.update(
                {
                    "workflow_path": workflow_path,
                    "workflow_id": workflow_id,
                    "workflow_run_id": workflow_run_id,
                }
            )
            summary.append({"context": context, "observations": [check]})
        if failed:
            return "CI_FAILED", summary
        if has_pending:
            return "WAITING_CI", summary
        return "SUCCESS", summary

    def _controller_status_snapshot(
        self,
        authorization: ReleaseAuthorization,
        sha: str,
        *,
        expected: Mapping[str, Any] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        value = self.github.get(f"/repos/{authorization.repository}/commits/{sha}/status")
        statuses = value.get("statuses") if isinstance(value, dict) else None
        if not isinstance(statuses, list):
            raise GitHubTransportError("GitHub commit-status response is malformed")
        records: list[dict[str, Any]] = []
        for item in statuses:
            if not isinstance(item, Mapping) or item.get("context") != CONTROLLER_CHECK.context:
                continue
            state = item.get("state")
            if not isinstance(state, str):
                return "CI_FAILED", records
            records.append(
                {
                    "context": CONTROLLER_CHECK.context,
                    "state": state,
                    "description": item.get("description"),
                    "target_url": item.get("target_url"),
                }
            )
        successes = [item for item in records if item["state"] == "success"]
        if expected is not None:
            if successes:
                exact = (
                    len(successes) == 1
                    and records[0] is successes[0]
                    and records[0].get("description") == expected.get("description")
                    and records[0].get("target_url") == expected.get("target_url")
                )
                return ("SUCCESS" if exact else "CI_FAILED"), records
            if any(item["state"] not in {"pending"} for item in records):
                return "CI_FAILED", records
            return "WAITING_CI", records
        if successes or any(item["state"] not in {"pending"} for item in records):
            return "CI_FAILED", records
        return "WAITING_CI", records

    @staticmethod
    def _controller_status_expectation(
        authorization: ReleaseAuthorization,
        gate: LocalGate,
        pr: PullRequest,
        state: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        digest = _digest(
            {
                "task_id": authorization.task_id,
                "commit_sha": state["commit_sha"],
                "candidate_sha256": authorization.candidate_sha256,
                "qa_sha256": gate.evidence.get("qa_sha256"),
                "reviewer_sha256": gate.evidence.get("reviewer_sha256"),
                "docs_receipt_sha256": gate.docs_receipt_sha256,
                "index_receipt_sha256": gate.index_receipt_sha256,
            }
        )
        return digest, {
            "context": CONTROLLER_CHECK.context,
            "state": "success",
            "description": "LADS machine acceptance " + digest[:20],
            "target_url": pr.url,
        }

    def _verify_open_pr(
        self,
        authorization: ReleaseAuthorization,
        state: Mapping[str, Any],
        pr: PullRequest,
    ) -> PullRequest:
        if (
            pr.number != state.get("pr_number")
            or pr.node_id != state.get("pr_node_id")
            or pr.head_sha != state.get("commit_sha")
            or pr.head_ref != authorization.branch
            or pr.base_ref != authorization.base_branch
        ):
            raise ReleaseConflict("PR identity no longer matches the release state")
        if pr.merged:
            raise ReleaseConflict("Open release operation encountered a merged PR")
        if pr.state != "open" or pr.draft or pr.mergeable is False:
            raise ReleaseBlocked(
                "PR is draft, closed, or has a merge conflict",
                state="BLOCKED",
            )
        return pr

    def _review_context(
        self,
        authorization: ReleaseAuthorization,
        state: Mapping[str, Any],
        pr: PullRequest,
    ) -> dict[str, Any]:
        self._verify_open_pr(authorization, state, pr)
        context = self._pr_context(authorization, pr.number)
        if (
            context.get("id") != pr.node_id
            or context.get("number") != pr.number
            or context.get("headRefOid") != pr.head_sha
            or context.get("baseRefName") != pr.base_ref
            or context.get("state") != "OPEN"
            or context.get("isDraft") is not False
            or context.get("merged") is not False
        ):
            raise ReleaseConflict("PR review context does not bind the exact pinned PR/head")
        if context.get("reviewDecision") == "CHANGES_REQUESTED":
            raise ReleaseBlocked("PR has a blocking CHANGES_REQUESTED review", state="BLOCKED")
        review_threads = context.get("reviewThreads")
        if not isinstance(review_threads, dict):
            raise ReleaseBlocked("PR review-thread state is unavailable", state="BLOCKED")
        page_info = review_threads.get("pageInfo")
        if not isinstance(page_info, dict) or page_info.get("hasNextPage") is True:
            raise ReleaseBlocked(
                "PR has more than the bounded 100 review threads",
                state="BLOCKED",
            )
        threads = review_threads.get("nodes")
        if not isinstance(threads, list):
            raise ReleaseBlocked("PR review threads are malformed", state="BLOCKED")
        if any(
            isinstance(item, dict) and item.get("isResolved") is False and item.get("isOutdated") is not True
            for item in threads
        ):
            raise ReleaseBlocked("PR has unresolved review threads", state="BLOCKED")
        return context

    def _verify_prerequisites(
        self,
        authorization: ReleaseAuthorization,
        state: dict[str, Any],
    ) -> tuple[PullRequest, dict[str, Any]]:
        number = state.get("pr_number")
        if type(number) is not int or number <= 0:
            raise ReleaseConflict("Release state lacks the exact PR number")
        pr = self._get_pr(authorization, number)
        context = self._review_context(authorization, state, pr)
        self._branch_protection(authorization)
        return pr, context

    def _wait_for_workflows(
        self,
        authorization: ReleaseAuthorization,
        state: dict[str, Any],
        pr: PullRequest,
        *,
        require_controller_pending: bool = True,
    ) -> PullRequest:
        latest = pr
        for attempt in range(1, self.poll_attempts + 1):
            latest, _context = self._verify_prerequisites(authorization, state)
            workflow_state, workflows = self._check_snapshot(
                authorization,
                str(state["commit_sha"]),
            )
            if require_controller_pending:
                controller_state, controller = self._controller_status_snapshot(
                    authorization,
                    str(state["commit_sha"]),
                )
            else:
                controller_state, controller = "WAITING_CI", []
            self._save(
                state,
                "CI_FAILED" if workflow_state == "CI_FAILED" or controller_state == "CI_FAILED" else "WAITING_CI",
                check_snapshot=workflows,
                controller_status_snapshot=controller,
                workflow_wait_attempt=attempt,
            )
            if workflow_state == "CI_FAILED":
                raise ReleaseBlocked("A required GitHub workflow check failed", state="CI_FAILED")
            if controller_state == "CI_FAILED":
                self._save(
                    state,
                    "AWAITING_APPROVAL",
                    substatus="CONTROLLER_STATUS_CONFLICT",
                )
                raise ReleaseBlocked(
                    "Controller status has an unexpected success or terminal state",
                    state="AWAITING_APPROVAL",
                    reason="CONTROLLER_STATUS_CONFLICT",
                )
            if workflow_state == "SUCCESS":
                return latest
            if attempt < self.poll_attempts:
                self.sleeper(self.poll_interval)
        self._save(state, "WAITING_CI", workflow_wait_attempt=self.poll_attempts)
        raise ReleaseBlocked("Bounded workflow-check wait ended before success", state="WAITING_CI")

    @staticmethod
    def _auto_merge_evidence(
        pr: PullRequest,
        merge_method: str,
        auto: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "pull_request_id": pr.node_id,
            "pull_request_number": pr.number,
            "head_sha": pr.head_sha,
            "base_ref": pr.base_ref,
            "merge_method": merge_method,
            "enabled_at": auto.get("enabledAt"),
        }

    def _verify_auto_merge_snapshot(
        self,
        latest: PullRequest,
        context: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> None:
        evidence = state.get("auto_merge_evidence")
        auto = context.get("autoMergeRequest")
        if (
            not isinstance(evidence, Mapping)
            or not isinstance(auto, Mapping)
            or evidence.get("pull_request_id") != latest.node_id
            or evidence.get("pull_request_number") != latest.number
            or evidence.get("head_sha") != latest.head_sha
            or evidence.get("base_ref") != latest.base_ref
            or evidence.get("merge_method") != auto.get("mergeMethod")
            or evidence.get("enabled_at") != auto.get("enabledAt")
            or not isinstance(evidence.get("enabled_at"), str)
            or not evidence["enabled_at"]
        ):
            raise ReleaseBlocked(
                "Native auto-merge evidence is absent or changed",
                state="AWAITING_APPROVAL",
                reason="AUTO_MERGE_EVIDENCE_CONFLICT",
            )

    def _verify_auto_merge_context(
        self,
        authorization: ReleaseAuthorization,
        state: dict[str, Any],
    ) -> PullRequest:
        latest, context = self._verify_prerequisites(authorization, state)
        self._verify_auto_merge_snapshot(latest, context, state)
        return latest

    def _create_controller_status(
        self,
        authorization: ReleaseAuthorization,
        gate: LocalGate,
        pr: PullRequest,
        state: dict[str, Any],
    ) -> None:
        digest, expected = self._controller_status_expectation(
            authorization,
            gate,
            pr,
            state,
        )
        intent = {
            "context": CONTROLLER_CHECK.context,
            "state": "success",
            "evidence_digest": digest,
            "description": expected["description"],
            "target_url": expected["target_url"],
        }
        current_intent = state.get("controller_status_intent")
        if current_intent is not None and current_intent != intent:
            raise ReleaseConflict("Controller status intent changed")
        current_state, current = self._controller_status_snapshot(
            authorization,
            str(state["commit_sha"]),
            expected=expected,
        )
        if current_state == "SUCCESS":
            if (
                current_intent != intent
                or state.get("controller_status_attempted") is not True
            ):
                raise ReleaseBlocked(
                    "Controller success appeared without a durable LADS status intent",
                    state="AWAITING_APPROVAL",
                    reason="CONTROLLER_STATUS_CONFLICT",
                )
            self._save(
                state,
                "CONTROLLER_STATUS_READY",
                controller_evidence_digest=digest,
                controller_status_snapshot=current,
            )
            return
        if current_state == "CI_FAILED":
            raise ReleaseBlocked(
                "Controller status read-back does not match the bound success",
                state="AWAITING_APPROVAL",
                reason="CONTROLLER_STATUS_CONFLICT",
            )
        if state.get("controller_status_attempted") is True:
            raise ReleaseBlocked(
                "Controller status creation previously became ambiguous",
                state="CONTROLLER_STATUS_PENDING",
                reason="CONTROLLER_STATUS_AMBIGUOUS",
            )
        self._save(
            state,
            "CONTROLLER_STATUS_INTENT",
            controller_status_intent=intent,
            controller_status_attempted=True,
        )
        try:
            self.github.mutate(
                "POST",
                f"/repos/{authorization.repository}/statuses/{state['commit_sha']}",
                {
                    "state": expected["state"],
                    "context": expected["context"],
                    "description": expected["description"],
                    "target_url": pr.url,
                },
            )
        except GitHubTransportError:
            pass
        readback_state, readback = self._controller_status_snapshot(
            authorization,
            str(state["commit_sha"]),
            expected=expected,
        )
        if readback_state != "SUCCESS":
            raise ReleaseBlocked(
                "Controller status creation is ambiguous",
                state="CONTROLLER_STATUS_PENDING",
                reason="CONTROLLER_STATUS_AMBIGUOUS",
            )
        self._save(
            state,
            "CONTROLLER_STATUS_READY",
            controller_evidence_digest=digest,
            controller_status_snapshot=readback,
        )

    def _enable_auto_merge(
        self,
        authorization: ReleaseAuthorization,
        state: dict[str, Any],
        pr: PullRequest,
    ) -> None:
        if not pr.node_id:
            raise ReleaseConflict("PR node ID is missing for native auto-merge")
        resolution = state.get("repository_resolution")
        merge_method = (
            resolution.get("merge_method")
            if isinstance(resolution, Mapping)
            else None
        )
        if merge_method not in {"MERGE", "REBASE", "SQUASH"}:
            self._save(
                state,
                "AWAITING_APPROVAL",
                substatus="AUTO_MERGE_UNSUPPORTED",
                auto_merge_error="repository has no supported native merge method",
            )
            raise ReleaseBlocked(
                "Repository does not allow a supported native merge method",
                state="AWAITING_APPROVAL",
                reason="AUTO_MERGE_UNSUPPORTED",
            )
        if pr.number != state.get("pr_number") or pr.node_id != state.get("pr_node_id"):
            raise ReleaseConflict("Native auto-merge PR identity changed")
        intent = {"pull_request_id": pr.node_id, "merge_method": merge_method}
        current_intent = state.get("auto_merge_intent")
        if current_intent is not None and current_intent != intent:
            raise ReleaseConflict("Native auto-merge intent changed on resume")
        latest, context = self._verify_prerequisites(authorization, state)
        existing_auto = context.get("autoMergeRequest")
        if isinstance(existing_auto, Mapping):
            if existing_auto.get("mergeMethod") != merge_method:
                raise ReleaseConflict("Existing PR auto-merge method changed")
            if (
                current_intent != intent
                or state.get("auto_merge_registration_attempted") is not True
            ):
                raise ReleaseBlocked(
                    "A native auto-merge request lacks the durable LADS registration intent",
                    state="AWAITING_APPROVAL",
                    reason="AUTO_MERGE_EVIDENCE_CONFLICT",
                )
            evidence = self._auto_merge_evidence(latest, merge_method, existing_auto)
            if not isinstance(evidence["enabled_at"], str) or not evidence["enabled_at"]:
                raise ReleaseBlocked(
                    "Native auto-merge read-back lacks an enabled timestamp",
                    state="AWAITING_APPROVAL",
                    reason="AUTO_MERGE_EVIDENCE_CONFLICT",
                )
            self._save(
                state,
                "AUTO_MERGE_ENABLED",
                auto_merge_enabled=True,
                auto_merge_method=merge_method,
                auto_merge_evidence=evidence,
            )
            return
        if state.get("auto_merge_registration_attempted") is True:
            raise ReleaseBlocked(
                "Native auto-merge registration is absent after a prior attempt",
                state="AWAITING_APPROVAL",
                reason="AUTO_MERGE_EVIDENCE_CONFLICT",
            )
        mutation = (
            "mutation($pullRequestId:ID!,$mergeMethod:PullRequestMergeMethod!){"
            "enablePullRequestAutoMerge(input:{pullRequestId:$pullRequestId,"
            "mergeMethod:$mergeMethod}){pullRequest{autoMergeRequest{enabledAt mergeMethod}}}}"
        )
        self._save(
            state,
            "AUTO_MERGE_INTENT",
            auto_merge_intent=intent,
            auto_merge_registration_attempted=True,
        )
        try:
            result = self.github.transport.graphql(
                mutation,
                {"pullRequestId": pr.node_id, "mergeMethod": merge_method},
            )
        except GitHubTransportError as exc:
            latest = self._get_pr(authorization, pr.number)
            if latest.merged:
                raise ReleaseBlocked(
                    "PR merged without a durable pre-merge native auto-merge read-back",
                    state="AWAITING_APPROVAL",
                    reason="AUTO_MERGE_EVIDENCE_CONFLICT",
                ) from exc
            latest, context = self._verify_prerequisites(authorization, state)
            reconciled = context.get("autoMergeRequest")
            if isinstance(reconciled, Mapping):
                if reconciled.get("mergeMethod") != merge_method:
                    raise ReleaseConflict("Reconciled PR auto-merge method changed") from exc
                evidence = self._auto_merge_evidence(pr, merge_method, reconciled)
                if not isinstance(evidence["enabled_at"], str) or not evidence["enabled_at"]:
                    raise ReleaseBlocked(
                        "Reconciled native auto-merge lacks an enabled timestamp",
                        state="AWAITING_APPROVAL",
                        reason="AUTO_MERGE_EVIDENCE_CONFLICT",
                    ) from exc
                self._save(
                    state,
                    "AUTO_MERGE_ENABLED",
                    auto_merge_enabled=True,
                    auto_merge_method=merge_method,
                    auto_merge_evidence=evidence,
                )
                return
            self._save(
                state,
                "AWAITING_APPROVAL",
                substatus="AUTO_MERGE_UNSUPPORTED",
                auto_merge_error="native auto-merge was rejected",
            )
            raise ReleaseBlocked(
                "Native auto-merge is unsupported or rejected",
                state="AWAITING_APPROVAL",
                reason="AUTO_MERGE_UNSUPPORTED",
            ) from exc
        errors = result.get("errors")
        if errors:
            latest = self._get_pr(authorization, pr.number)
            if latest.merged:
                raise ReleaseBlocked(
                    "PR merged without a durable pre-merge native auto-merge read-back",
                    state="AWAITING_APPROVAL",
                    reason="AUTO_MERGE_EVIDENCE_CONFLICT",
                )
            latest, context = self._verify_prerequisites(authorization, state)
            reconciled = context.get("autoMergeRequest")
            if isinstance(reconciled, Mapping):
                if reconciled.get("mergeMethod") != merge_method:
                    raise ReleaseConflict("Reconciled PR auto-merge method changed")
                evidence = self._auto_merge_evidence(pr, merge_method, reconciled)
                if not isinstance(evidence["enabled_at"], str) or not evidence["enabled_at"]:
                    raise ReleaseBlocked(
                        "Reconciled native auto-merge lacks an enabled timestamp",
                        state="AWAITING_APPROVAL",
                        reason="AUTO_MERGE_EVIDENCE_CONFLICT",
                    )
                self._save(
                    state,
                    "AUTO_MERGE_ENABLED",
                    auto_merge_enabled=True,
                    auto_merge_method=merge_method,
                    auto_merge_evidence=evidence,
                )
                return
            self._save(
                state,
                "AWAITING_APPROVAL",
                substatus="AUTO_MERGE_UNSUPPORTED",
                auto_merge_error="native auto-merge returned GraphQL errors",
            )
            raise ReleaseBlocked(
                "Native auto-merge is unsupported or rejected",
                state="AWAITING_APPROVAL",
                reason="AUTO_MERGE_UNSUPPORTED",
            )
        auto = (
            result.get("data", {})
            .get("enablePullRequestAutoMerge", {})
            .get("pullRequest", {})
            .get("autoMergeRequest")
        )
        if (
            not isinstance(auto, Mapping)
            or auto.get("mergeMethod") != merge_method
            or not isinstance(auto.get("enabledAt"), str)
            or not auto["enabledAt"]
        ):
            raise ReleaseBlocked(
                "Native auto-merge did not produce an enabled request",
                state="AWAITING_APPROVAL",
                reason="AUTO_MERGE_UNSUPPORTED",
            )
        evidence = self._auto_merge_evidence(pr, merge_method, auto)
        latest, latest_context = self._verify_prerequisites(authorization, state)
        readback = latest_context.get("autoMergeRequest")
        if (
            latest.number != pr.number
            or latest.node_id != pr.node_id
            or latest.head_sha != pr.head_sha
            or not isinstance(readback, Mapping)
            or readback.get("mergeMethod") != merge_method
            or readback.get("enabledAt") != evidence["enabled_at"]
        ):
            raise ReleaseBlocked(
                "Native auto-merge registration did not read back exactly",
                state="AWAITING_APPROVAL",
                reason="AUTO_MERGE_EVIDENCE_CONFLICT",
            )
        self._save(
            state,
            "AUTO_MERGE_ENABLED",
            auto_merge_enabled=True,
            auto_merge_method=merge_method,
            auto_merge_evidence=evidence,
        )

    def _get_pr(self, authorization: ReleaseAuthorization, number: int) -> PullRequest:
        value = self.github.get(f"/repos/{authorization.repository}/pulls/{number}")
        if not isinstance(value, dict):
            raise GitHubTransportError("Pull-request response is malformed")
        return PullRequest.from_api(value)

    def _fetch_merged_base(self, authorization: ReleaseAuthorization) -> str:
        self._validate_origin_routes()
        result = self.runner.run(
            [self.git, "fetch", "--no-tags", "origin", authorization.base_branch],
            cwd=self.repo_root,
            timeout=60,
        )
        if result.exit_code != 0 or result.timed_out or result.bounded:
            raise ReleaseBlocked("Post-merge base fetch failed", state="MERGED_INDEX_PENDING")
        return self._git_text("rev-parse", "FETCH_HEAD")

    def _verify_merge(
        self,
        authorization: ReleaseAuthorization,
        pr: PullRequest,
    ) -> str:
        if (
            not pr.merged
            or not isinstance(pr.merge_commit_sha, str)
            or not _SHA40_RE.fullmatch(pr.merge_commit_sha)
        ):
            raise ReleaseBlocked("PR is not genuinely merged", reason="MERGE_NOT_CONFIRMED")
        remote_base = self._fetch_merged_base(authorization)
        present = self._git("cat-file", "-e", f"{pr.merge_commit_sha}^{{commit}}")
        if present.exit_code != 0:
            raise ReleaseBlocked("Merge commit is absent after fetch", reason="MERGE_NOT_CONFIRMED")
        ancestor = self._git(
            "merge-base",
            "--is-ancestor",
            pr.merge_commit_sha,
            remote_base,
            allow_code_one=True,
        )
        if ancestor.exit_code != 0:
            raise ReleaseBlocked("Merged commit is not contained by the base branch", reason="MERGE_NOT_CONFIRMED")
        return pr.merge_commit_sha

    def _postmerge_knowledge(
        self,
        authorization: ReleaseAuthorization,
        gate: LocalGate,
        state: dict[str, Any],
        *,
        project_id: str,
        knowledge_store: KnowledgeStore,
        merge_commit_sha: str,
        pr_url: str,
    ) -> dict[str, Any]:
        event_id = str(uuid.uuid5(uuid.UUID(authorization.task_id), "github-release-merged"))
        payload = {
            "release_state": "MERGED",
            "task_id": authorization.task_id,
            "source_head": authorization.source_head,
            "candidate_sha256": authorization.candidate_sha256,
            "commit_sha": state["commit_sha"],
            "merge_commit_sha": merge_commit_sha,
            "pr_url": pr_url,
            "evidence_sha256": _digest(gate.evidence),
            "documents_receipt_sha256": gate.docs_receipt_sha256,
            "index_receipt_sha256": gate.index_receipt_sha256,
        }
        try:
            with knowledge_store.batch():
                history = KnowledgeReader.from_store(knowledge_store, project_id).get_task_history(
                    authorization.task_id, limit=20
                )
                current = history.get("items", [{}])[0] if history.get("items") else {}
                current_state = current.get("state")
                if current_state == "planned":
                    knowledge_store.transition_task(
                        project_id,
                        authorization.task_id,
                        "active",
                        event_id=str(uuid.uuid5(uuid.UUID(authorization.task_id), "release-active")),
                        event_payload={"reason": "authorized GitHub release"},
                    )
                    current_state = "active"
                if current_state in {"active", "testing", "review"}:
                    knowledge_store.transition_task(
                        project_id,
                        authorization.task_id,
                        "completed",
                        event_id=str(uuid.uuid5(uuid.UUID(authorization.task_id), "release-completed")),
                        event_payload={"reason": "verified GitHub merge"},
                    )
                elif current_state not in {"completed", None}:
                    raise ReleaseConflict("Knowledge task is not in a releasable state")
                knowledge_store.update_task(
                    project_id,
                    authorization.task_id,
                    commit_sha=merge_commit_sha,
                    pr_ref=pr_url,
                    test_result="passed",
                    review_result="APPROVED",
                )
                event = knowledge_store.record_event(
                    project_id,
                    authorization.task_id,
                    "release_state_changed",
                    payload,
                    event_id=event_id,
                )
        except Exception as exc:
            state["index_attempts"] = int(state.get("index_attempts", 0)) + 1
            self._save(
                state,
                "MERGED_INDEX_PENDING",
                merge_commit_sha=merge_commit_sha,
                pr_url=pr_url,
                postmerge_error="knowledge merger receipt pending",
            )
            raise ReleaseBlocked(
                "Git merge is verified but knowledge receipt remains pending",
                state="MERGED_INDEX_PENDING",
            ) from exc
        receipt = {
            "index_event_id": event_id,
            "index_event_idempotent": event.get("idempotent", False),
            "merge_commit_sha": merge_commit_sha,
            "pr_url": pr_url,
            "state": "MERGED",
        }
        return self._save(
            state,
            "MERGED",
            merge_commit_sha=merge_commit_sha,
            pr_url=pr_url,
            merged_receipt=receipt,
        )

    def _publish_open_pr(
        self,
        authorization: ReleaseAuthorization,
        gate: LocalGate,
        state: dict[str, Any],
        pr: PullRequest,
    ) -> PullRequest:
        pr = self._verify_prerequisites(authorization, state)[0]
        if state.get("status") == "CONTROLLER_STATUS_READY":
            pr = self._verify_auto_merge_context(authorization, state)
        else:
            if not isinstance(state.get("auto_merge_evidence"), Mapping):
                pr = self._wait_for_workflows(authorization, state, pr)
                self._enable_auto_merge(authorization, state, pr)
            pr = self._verify_auto_merge_context(authorization, state)
            pr = self._wait_for_workflows(
                authorization,
                state,
                pr,
                require_controller_pending=(
                    state.get("controller_status_attempted") is not True
                ),
            )
            self._create_controller_status(authorization, gate, pr, state)
        return self._poll_merge(authorization, gate, state, pr)

    def _poll_merge(
        self,
        authorization: ReleaseAuthorization,
        gate: LocalGate,
        state: dict[str, Any],
        pr: PullRequest,
    ) -> PullRequest:
        latest = pr
        expected_digest, expected_status = self._controller_status_expectation(
            authorization,
            gate,
            pr,
            state,
        )
        for attempt in range(1, self.poll_attempts + 1):
            latest = self._get_pr(authorization, pr.number)
            if latest.merged:
                self._verify_native_merge_evidence(
                    authorization,
                    gate,
                    state,
                    latest,
                )
                return latest
            if latest.state != "open" or latest.mergeable is False:
                raise ReleaseBlocked("PR closed without a verified merge or has conflicts")
            latest, context = self._verify_prerequisites(authorization, state)
            self._verify_auto_merge_snapshot(latest, context, state)
            workflow_state, workflows = self._check_snapshot(
                authorization,
                str(state["commit_sha"]),
            )
            controller_state, controller = self._controller_status_snapshot(
                authorization,
                str(state["commit_sha"]),
                expected=expected_status,
            )
            self._save(
                state,
                "CI_FAILED"
                if workflow_state == "CI_FAILED" or controller_state == "CI_FAILED"
                else "WAITING_CI",
                check_snapshot=workflows,
                controller_status_snapshot=controller,
                poll_attempt=attempt,
            )
            if workflow_state == "CI_FAILED":
                raise ReleaseBlocked("A required GitHub workflow check failed", state="CI_FAILED")
            if controller_state == "CI_FAILED":
                raise ReleaseBlocked(
                    "The bound controller success status is absent or changed",
                    state="AWAITING_APPROVAL",
                    reason="CONTROLLER_STATUS_CONFLICT",
                )
            if workflow_state == "SUCCESS" and controller_state == "SUCCESS":
                self._save(
                    state,
                    "WAITING_CI",
                    controller_evidence_digest=expected_digest,
                    controller_status_snapshot=controller,
                )
            if attempt < self.poll_attempts:
                self.sleeper(self.poll_interval)
        self._save(state, "WAITING_CI", poll_attempt=attempt)
        raise ReleaseBlocked("Bounded auto-merge polling ended before a merge", state="WAITING_CI")

    def _verify_native_merge_evidence(
        self,
        authorization: ReleaseAuthorization,
        gate: LocalGate,
        state: Mapping[str, Any],
        pr: PullRequest,
    ) -> None:
        evidence = state.get("auto_merge_evidence")
        if (
            pr.number != state.get("pr_number")
            or pr.node_id != state.get("pr_node_id")
            or pr.head_sha != state.get("commit_sha")
            or pr.head_ref != authorization.branch
            or pr.base_ref != authorization.base_branch
            or state.get("auto_merge_enabled") is not True
            or state.get("controller_status_attempted") is not True
            or not isinstance(evidence, Mapping)
            or evidence.get("pull_request_id") != pr.node_id
            or evidence.get("pull_request_number") != pr.number
            or evidence.get("head_sha") != pr.head_sha
            or evidence.get("base_ref") != pr.base_ref
            or evidence.get("merge_method") != state.get("auto_merge_method")
            or not isinstance(evidence.get("enabled_at"), str)
            or not evidence["enabled_at"]
        ):
            raise ReleaseBlocked(
                "Merged PR lacks durable pre-merge native auto-merge evidence",
                state="AWAITING_APPROVAL",
                reason="AUTO_MERGE_EVIDENCE_CONFLICT",
            )
        digest, expected = self._controller_status_expectation(
            authorization,
            gate,
            pr,
            state,
        )
        controller_state, _controller = self._controller_status_snapshot(
            authorization,
            str(state["commit_sha"]),
            expected=expected,
        )
        if (
            controller_state != "SUCCESS"
            or state.get("controller_evidence_digest") != digest
        ):
            raise ReleaseBlocked(
                "Merged PR lacks the exact bound controller success",
                state="AWAITING_APPROVAL",
                reason="CONTROLLER_STATUS_CONFLICT",
            )

    def publish(
        self,
        authorization_path: str | Path,
        *,
        knowledge_home: str | Path,
        knowledge_db: str,
        project_id: str,
        knowledge_store: KnowledgeStore | None = None,
    ) -> dict[str, Any]:
        authorization = load_authorization(authorization_path)
        if authorization.repository != getattr(self, "repository", CANONICAL_REPOSITORY):
            raise ReleaseAuthorizationError("Authorization and controller refer to different projects")
        with self.store.lock(authorization.task_id):
            state = self._state(authorization)
            if state.get("status") == "MERGED" and state.get("merged_receipt"):
                return state
            if state.get("status") == "MERGED_INDEX_PENDING":
                if not isinstance(knowledge_store, KnowledgeStore):
                    raise ReleaseBlocked(
                        "Post-merge knowledge retry requires the existing store binding",
                        state="MERGED_INDEX_PENDING",
                    )
                gate = validate_local_gate(
                    run_root=self.run_root,
                    repo_root=self.repo_root,
                    knowledge_home=knowledge_home,
                    knowledge_db=knowledge_db,
                    project_id=project_id,
                    task_id=authorization.task_id,
                    history_loader=self.knowledge_history_loader,
                    journal=self.journal,
                )
                if state.get("index_attempts", 0) >= 3:
                    raise ReleaseBlocked("Post-merge knowledge retry limit reached", state="MERGED_INDEX_PENDING")
                return self._postmerge_knowledge(
                    authorization,
                    gate,
                    state,
                    project_id=project_id,
                    knowledge_store=knowledge_store,
                    merge_commit_sha=state["merge_commit_sha"],
                    pr_url=state["pr_url"],
                )
            self._verify_repository()
            gate = validate_local_gate(
                run_root=self.run_root,
                repo_root=self.repo_root,
                knowledge_home=knowledge_home,
                knowledge_db=knowledge_db,
                project_id=project_id,
                task_id=authorization.task_id,
                history_loader=self.knowledge_history_loader,
                journal=self.journal,
            )
            _validate_authorization_against_gate(authorization, gate)
            selected_repository = getattr(self, "repository", CANONICAL_REPOSITORY)
            repository = self.github.resolve_repository() if selected_repository == CANONICAL_REPOSITORY else self.github.resolve_repository(selected_repository)
            state["repository_resolution"] = self._bind_repository_resolution(
                repository,
                state,
            )
            audit = audit_changes(self.repo_root, authorization)
            state["audit"] = audit.as_dict()
            critical, undeclared = _audit_review_gaps(
                audit,
                authorization.risk_flags,
            )
            if critical:
                self._save(
                    state,
                    "AWAITING_APPROVAL",
                    critical_audit_findings=list(critical),
                )
                raise ReleaseBlocked(
                    "Critical audit findings require code remediation and cannot be waived",
                    state="AWAITING_APPROVAL",
                )
            if undeclared:
                self._save(
                    state,
                    "AWAITING_APPROVAL",
                    undeclared_risk_flags=list(undeclared),
                )
                raise ReleaseBlocked(
                    "High-risk change lacks an exact controller authorization flag",
                    state="AWAITING_APPROVAL",
                )
            self._save(state, "LOCAL_GATE_VERIFIED", stage3_gate={
                "task_id": gate.task_id,
                "source_head": gate.bundle.source_head,
                "candidate_sha256": gate.bundle.candidate_sha256,
                "main_ack_sha256": gate.main_ack_sha256,
                "docs_receipt_sha256": gate.docs_receipt_sha256,
                "index_receipt_sha256": gate.index_receipt_sha256,
                "commands": list(gate.bundle.expected_commands),
                "worker_roles": [item["role"] for item in gate.bundle.worker_stage_receipts],
            })
            self._fetch_base(authorization, state)
            self._capture(authorization, state)
            commit = self._stage_and_commit(authorization, state)
            state["commit_sha"] = commit
            self._push_once(authorization, state)
            pr = self._ensure_pr(authorization, gate, state)
            if pr.state == "open" and not pr.merged:
                self._save(
                    state,
                    "PR_OPEN",
                    pr_number=pr.number,
                    pr_url=pr.url,
                    pr_node_id=pr.node_id,
                )
            if pr.merged:
                self._verify_native_merge_evidence(
                    authorization,
                    gate,
                    state,
                    pr,
                )
                merge_sha = self._verify_merge(authorization, pr)
                if not isinstance(knowledge_store, KnowledgeStore):
                    self._save(
                        state,
                        "MERGED_INDEX_PENDING",
                        merge_commit_sha=merge_sha,
                        pr_url=pr.url,
                    )
                    return state
                return self._postmerge_knowledge(
                    authorization,
                    gate,
                    state,
                    project_id=project_id,
                    knowledge_store=knowledge_store,
                    merge_commit_sha=merge_sha,
                    pr_url=pr.url,
                )
            merged = self._publish_open_pr(
                authorization,
                gate,
                state,
                pr,
            )
            merge_sha = self._verify_merge(authorization, merged)
            if not isinstance(knowledge_store, KnowledgeStore):
                self._save(
                    state,
                    "MERGED_INDEX_PENDING",
                    merge_commit_sha=merge_sha,
                    pr_url=merged.url,
                )
                return state
            return self._postmerge_knowledge(
                authorization,
                gate,
                state,
                project_id=project_id,
                knowledge_store=knowledge_store,
                merge_commit_sha=merge_sha,
                pr_url=merged.url,
            )


def read_state(store: ReleaseStore, task_id: str) -> dict[str, Any]:
    """Return one private release state without performing network or Git work."""
    return store.load(task_id)


__all__ = [
    "AUTHORIZATION_SCHEMA",
    "CANONICAL_REPOSITORY",
    "ChangeAudit",
    "CommandResult",
    "CommandRunner",
    "FileChange",
    "GitHubClient",
    "GitHubTransientError",
    "GitHubTransport",
    "GhCliTransport",
    "LocalGate",
    "PullRequest",
    "REQUIRED_CHECKS",
    "ReleaseAuthorization",
    "ReleaseAuthorizationError",
    "ReleaseBlocked",
    "ReleaseBoundaryError",
    "ReleaseConflict",
    "ReleaseController",
    "ReleaseError",
    "ReleaseGateError",
    "ReleaseStore",
    "ReleaseStateNotFound",
    "audit_changes",
    "default_release_root",
    "load_authorization",
    "parse_authorization",
    "read_state",
    "validate_local_gate",
]
