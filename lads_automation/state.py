"""Private, per-task journals for the LADS delivery gate.

The journal is engineering metadata, never product or personal data. File state
is stored only in the fixed LifeOS-LADS private namespace. State and event files
are replaced individually; no cross-file or cross-system atomicity is claimed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from lads_knowledge.database import (
    _ensure_git_exclusion,
    _is_reparse,
    _reject_reparse_chain,
)
from lads_knowledge.safety import utc_now_iso

MAX_STATE_BYTES = 1024 * 1024
MAX_EVENTS_BYTES = 4 * 1024 * 1024
MAX_EVENT_PAYLOAD_BYTES = 64 * 1024
MAX_LOCK_SECONDS = 10.0
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_ACCEPTANCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MARKER_FORMAT = "lads-automation-home-v1"
_APPLICATION_ID = "LifeOS-LADS-automation"


class JournalError(RuntimeError):
    """Base class for safe journal failures."""


class JournalBoundaryError(JournalError):
    """The selected directory or path is outside the owned private boundary."""


class JournalNotFound(JournalError):
    """A task or journal file does not exist."""


class JournalConflict(JournalError):
    """An identifier or append operation conflicts with durable state."""


class JournalLockTimeout(JournalError):
    """A per-task lock could not be acquired within the bounded timeout."""


class JournalSessionAmbiguous(JournalError):
    """A chat session resolves to more than one unfinished task."""


class JournalSessionCorrupt(JournalError):
    """A session scan encountered unreadable task state and failed closed."""

    def __init__(self, count: int) -> None:
        self.count = max(0, min(int(count), 10000))
        super().__init__(
            "Session binding could not be verified because one or more task "
            "journals are unreadable"
        )

    def warning(self) -> dict[str, Any]:
        """Return bounded, nonsecret corruption metadata for hook consumers."""
        return {
            "code": "journal_task_corrupt",
            "unreadable_task_count": self.count,
            "message": (
                "LADS task binding is unavailable because the journal contains "
                "unreadable task state; no task was selected."
            ),
        }


_TERMINAL_TASK_STATES = frozenset(
    {
        "DELIVERY_READY",
        "BLOCKED",
        "QA_FAILED",
        "REVIEW_FAILED",
    }
)


def _task_uuid(task_id: str) -> str:
    value = str(task_id).strip().lower()
    if not _UUID_RE.fullmatch(value):
        raise JournalError("Task ID must be a canonical UUID")
    if value != str(task_id):
        raise JournalError("Task ID must use lowercase canonical UUID form")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _without_idempotent(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "idempotent"}


def _strict_json(data: bytes, label: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise JournalConflict(f"{label} contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                JournalError(f"{label} contains non-JSON numeric constants")
            ),
        )
    except (UnicodeError, ValueError) as exc:
        raise JournalError(f"{label} is not valid UTF-8 JSON") from exc


def default_automation_root() -> Path:
    """Return the fixed private automation root, never the project tree."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local).resolve(strict=False) / "LifeOS-LADS" / "automation"
    return Path.home().resolve(strict=False) / ".local" / "share" / "lifeos-lads" / "automation"


def acceptance_root(namespace: str) -> Path:
    """Return an explicit synthetic-test namespace under LifeOS-LADS/acceptance."""
    if not isinstance(namespace, str) or not _ACCEPTANCE_RE.fullmatch(namespace):
        raise JournalBoundaryError("Acceptance namespace is not a bounded safe component")
    return default_automation_root().parent / "acceptance" / namespace


def _validate_root(root: Path, *, acceptance_namespace: str | None) -> Path:
    selected = acceptance_root(acceptance_namespace) if acceptance_namespace is not None else default_automation_root()
    if root is not None and root != selected:
        raise JournalBoundaryError("Journal root must be the fixed private path or explicit acceptance path")
    if not selected.is_absolute():
        raise JournalBoundaryError("Journal root must be absolute")
    _reject_reparse_chain(selected)
    resolved = selected.resolve(strict=False)
    if resolved != selected:
        raise JournalBoundaryError("Journal root must not traverse links or relative components")
    return resolved


def _initialize_root(root: Path) -> None:
    if root.exists():
        if not root.is_dir() or _is_reparse(root):
            raise JournalBoundaryError("Automation root is not a safe directory")
        if any(root.iterdir()) and not (root / ".lads-automation.json").exists():
            raise JournalBoundaryError("Nonempty automation root is not marked as LADS-owned")
    else:
        root.mkdir(parents=True, exist_ok=False)
    try:
        root.chmod(0o700)
    except OSError:
        pass

    marker = root / ".lads-automation.json"
    marker_payload = {"application_id": _APPLICATION_ID, "format": _MARKER_FORMAT}
    if marker.exists():
        if _is_reparse(marker) or marker.is_symlink() or not marker.is_file():
            raise JournalBoundaryError("Automation ownership marker is unsafe")
        try:
            current = _strict_json(marker.read_bytes(), "automation marker")
        except JournalError as exc:
            raise JournalBoundaryError("Automation ownership marker is unreadable") from exc
        if current != marker_payload:
            raise JournalBoundaryError("Automation root belongs to another owner or format")
    else:
        try:
            with marker.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(_canonical(marker_payload) + "\n")
        except FileExistsError as exc:
            raise JournalBoundaryError("Automation marker appeared concurrently") from exc
        try:
            marker.chmod(0o600)
        except OSError:
            pass

    ignore = root / ".gitignore"
    if not ignore.exists():
        with ignore.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write("*\n")
    if _is_reparse(ignore) or ignore.is_symlink() or ignore.read_bytes() != b"*\n":
        raise JournalBoundaryError("Automation root requires its owned deny-all Git exclusion")
    _ensure_git_exclusion(root, allow_create=False)

    tasks = root / "tasks"
    if tasks.exists():
        if not tasks.is_dir() or _is_reparse(tasks) or tasks.is_symlink():
            raise JournalBoundaryError("Automation tasks directory is unsafe")
    else:
        tasks.mkdir()


def _validate_owned_root(root: Path) -> None:
    if not root.exists() or not root.is_dir():
        raise JournalBoundaryError("Automation root is not initialized")
    _reject_reparse_chain(root)
    marker = root / ".lads-automation.json"
    ignore = root / ".gitignore"
    tasks = root / "tasks"
    for item in (marker, ignore, tasks):
        if not item.exists() or _is_reparse(item) or item.is_symlink():
            raise JournalBoundaryError("Automation root is missing safe owned metadata")
    if _strict_json(marker.read_bytes(), "automation marker") != {
        "application_id": _APPLICATION_ID,
        "format": _MARKER_FORMAT,
    }:
        raise JournalBoundaryError("Automation ownership marker mismatch")
    if ignore.read_bytes() != b"*\n":
        raise JournalBoundaryError("Automation Git exclusion mismatch")
    if not tasks.is_dir():
        raise JournalBoundaryError("Automation tasks path is not a directory")
    _ensure_git_exclusion(root, allow_create=False)


def _read_json_file(path: Path, maximum: int, label: str) -> dict[str, Any]:
    if not path.exists() or _is_reparse(path) or path.is_symlink() or not path.is_file():
        raise JournalNotFound(f"{label} does not exist as a regular file")
    if path.stat().st_size > maximum:
        raise JournalError(f"{label} exceeds its size limit")
    data = path.read_bytes()
    parsed = _strict_json(data, label)
    if not isinstance(parsed, dict):
        raise JournalError(f"{label} must contain a JSON object")
    return parsed


def _atomic_json(path: Path, payload: dict[str, Any], maximum: int) -> None:
    data = (_canonical(payload) + "\n").encode("utf-8")
    if len(data) > maximum:
        raise JournalError("Journal payload exceeds its size limit")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".lads-tmp-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


class _FileTaskLock:
    def __init__(self, path: Path, timeout: float) -> None:
        self.path = path
        self.timeout = timeout
        self._handle: Any = None
        self._windows = os.name == "nt"

    def _try_lock(self) -> bool:
        assert self._handle is not None
        if self._windows:
            import msvcrt

            self._handle.seek(0)
            try:
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            return True
        import fcntl

        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def __enter__(self) -> None:
        self.path.parent.mkdir(exist_ok=True)
        self._handle = self.path.open("a+b")
        if self._handle.tell() == 0:
            self._handle.write(b"\0")
            self._handle.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            if self._try_lock():
                return
            if time.monotonic() >= deadline:
                self._handle.close()
                self._handle = None
                raise JournalLockTimeout("Task journal lock timed out")
            time.sleep(0.025)

    def __exit__(self, *_args: Any) -> None:
        assert self._handle is not None
        try:
            if self._windows:
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


class FileJournal:
    """Durable per-task JSON journal in the fixed private automation namespace."""

    def __init__(
        self,
        *,
        root: str | os.PathLike[str] | None = None,
        acceptance_namespace: str | None = None,
        lock_timeout: float = 2.0,
    ) -> None:
        selected = Path(root) if root is not None else None
        self.root = _validate_root(selected, acceptance_namespace=acceptance_namespace)  # type: ignore[arg-type]
        if not 0 < float(lock_timeout) <= MAX_LOCK_SECONDS:
            raise JournalError("Lock timeout must be bounded and positive")
        self.lock_timeout = float(lock_timeout)

    @classmethod
    def create(
        cls,
        *,
        root: str | os.PathLike[str] | None = None,
        acceptance_namespace: str | None = None,
        lock_timeout: float = 2.0,
    ) -> "FileJournal":
        journal = cls(root=root, acceptance_namespace=acceptance_namespace, lock_timeout=lock_timeout)
        _initialize_root(journal.root)
        return journal

    @classmethod
    def open(
        cls,
        *,
        root: str | os.PathLike[str] | None = None,
        acceptance_namespace: str | None = None,
        lock_timeout: float = 2.0,
    ) -> "FileJournal":
        journal = cls(root=root, acceptance_namespace=acceptance_namespace, lock_timeout=lock_timeout)
        _validate_owned_root(journal.root)
        return journal

    @property
    def tasks_root(self) -> Path:
        return self.root / "tasks"

    def _task_dir(self, task_id: str, *, create: bool) -> Path:
        identity = _task_uuid(task_id)
        selected = self.tasks_root / identity
        if create:
            selected.mkdir(exist_ok=True)
        if selected.exists():
            _reject_reparse_chain(selected)
            if not selected.is_dir():
                raise JournalBoundaryError("Task journal path is not a directory")
        elif not create:
            raise JournalNotFound("Task journal is not present")
        return selected

    def _lock(self, directory: Path) -> _FileTaskLock:
        return _FileTaskLock(directory / ".lock", self.lock_timeout)

    def create_task(self, state: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(state, dict):
            raise JournalError("Task state must be an object")
        task_id = _task_uuid(state.get("task_id", ""))
        stored = copy.deepcopy(state)
        stored["task_id"] = task_id
        directory = self._task_dir(task_id, create=True)
        with self._lock(directory):
            path = directory / "state.json"
            if path.exists():
                current = self.get_task(task_id)
                if _canonical(current) != _canonical(stored):
                    raise JournalConflict("Task ID already has different initial state")
                return copy.deepcopy(current)
            _atomic_json(path, stored, MAX_STATE_BYTES)
            events_path = directory / "events.json"
            if not events_path.exists():
                _atomic_json(events_path, {"version": 1, "events": []}, MAX_EVENTS_BYTES)
            return copy.deepcopy(stored)

    def get_task(self, task_id: str) -> dict[str, Any]:
        directory = self._task_dir(task_id, create=False)
        return _read_json_file(directory / "state.json", MAX_STATE_BYTES, "Task state")

    def save_task(self, state: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(state, dict):
            raise JournalError("Task state must be an object")
        task_id = _task_uuid(state.get("task_id", ""))
        directory = self._task_dir(task_id, create=False)
        with self._lock(directory):
            current = _read_json_file(directory / "state.json", MAX_STATE_BYTES, "Task state")
            if current.get("task_id") != task_id:
                raise JournalConflict("Task state identity changed")
            stored = copy.deepcopy(state)
            _atomic_json(directory / "state.json", stored, MAX_STATE_BYTES)
            return copy.deepcopy(stored)

    def is_registered(self, task_id: str) -> bool:
        try:
            self.get_task(task_id)
        except (JournalError, OSError):
            return False
        return True

    def resolve_session(self, session_id: str) -> str | None:
        if not isinstance(session_id, str) or not session_id or len(session_id) > 200:
            return None
        matches: list[str] = []
        unreadable = 0
        for directory in sorted(self.tasks_root.iterdir(), key=lambda item: item.name):
            if not directory.is_dir() or _is_reparse(directory) or not _UUID_RE.fullmatch(directory.name):
                continue
            try:
                state = self.get_task(directory.name)
            except (JournalError, OSError):
                unreadable += 1
                continue
            if (
                state.get("session_id") == session_id
                and state.get("ready") is not True
                and state.get("status") not in _TERMINAL_TASK_STATES
            ):
                matches.append(directory.name)
        if unreadable:
            raise JournalSessionCorrupt(unreadable)
        if len(matches) > 1:
            raise JournalSessionAmbiguous(
                "Session binding is ambiguous: more than one unfinished task is registered"
            )
        return matches[0] if matches else None

    def append_event(
        self,
        task_id: str,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        identity = _task_uuid(event_id)
        clean_type = str(event_type).strip()
        if not clean_type or len(clean_type) > 100:
            raise JournalError("Event type is empty or too long")
        if not isinstance(payload, dict):
            raise JournalError("Event payload must be an object")
        try:
            payload_json = _canonical(payload)
        except (TypeError, ValueError) as exc:
            raise JournalError("Event payload is not canonical JSON data") from exc
        if len(payload_json.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
            raise JournalError("Event payload exceeds its size limit")
        directory = self._task_dir(task_id, create=False)
        with self._lock(directory):
            task = _read_json_file(directory / "state.json", MAX_STATE_BYTES, "Task state")
            if task.get("task_id") != _task_uuid(task_id):
                raise JournalConflict("Task state identity changed")
            events_path = directory / "events.json"
            events = _read_json_file(events_path, MAX_EVENTS_BYTES, "Task events")
            rows = events.get("events")
            if not isinstance(rows, list):
                raise JournalError("Task events payload is malformed")
            for row in rows:
                if row.get("id") != identity:
                    continue
                if (
                    row.get("task_id") != _task_uuid(task_id)
                    or row.get("event_type") != clean_type
                    or row.get("payload") != payload
                ):
                    raise JournalConflict("Event ID already has a different payload")
                return {**copy.deepcopy(row), "idempotent": True}
            record = {
                "id": identity,
                "task_id": _task_uuid(task_id),
                "event_type": clean_type,
                "payload": copy.deepcopy(payload),
                "payload_sha256": hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                "occurred_at": occurred_at or utc_now_iso(),
            }
            updated_events = {**events, "events": [*rows, record]}
            _atomic_json(events_path, updated_events, MAX_EVENTS_BYTES)
            event_ids = task.get("event_ids", [])
            if not isinstance(event_ids, list):
                raise JournalError("Task event index is malformed")
            if identity not in event_ids:
                task["event_ids"] = [*event_ids, identity]
                _atomic_json(directory / "state.json", task, MAX_STATE_BYTES)
            return {**copy.deepcopy(record), "idempotent": False}

    def list_events(self, task_id: str, *, event_type: str | None = None) -> list[dict[str, Any]]:
        directory = self._task_dir(task_id, create=False)
        events = _read_json_file(directory / "events.json", MAX_EVENTS_BYTES, "Task events")
        rows = events.get("events")
        if not isinstance(rows, list):
            raise JournalError("Task events payload is malformed")
        if event_type is None:
            return copy.deepcopy(rows)
        return copy.deepcopy([row for row in rows if row.get("event_type") == event_type])


class MemoryJournal:
    """In-memory journal with the same identity and append semantics as FileJournal."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}

    def create_task(self, state: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(state, dict):
            raise JournalError("Task state must be an object")
        task_id = _task_uuid(state.get("task_id", ""))
        stored = copy.deepcopy(state)
        stored["task_id"] = task_id
        with self._lock:
            if task_id in self._tasks:
                if _canonical(self._tasks[task_id]) != _canonical(stored):
                    raise JournalConflict("Task ID already has different initial state")
                return copy.deepcopy(self._tasks[task_id])
            self._tasks[task_id] = stored
            self._events[task_id] = []
            return copy.deepcopy(stored)

    def get_task(self, task_id: str) -> dict[str, Any]:
        identity = _task_uuid(task_id)
        with self._lock:
            if identity not in self._tasks:
                raise JournalNotFound("Task journal is not present")
            return copy.deepcopy(self._tasks[identity])

    def save_task(self, state: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(state, dict):
            raise JournalError("Task state must be an object")
        task_id = _task_uuid(state.get("task_id", ""))
        stored = copy.deepcopy(state)
        with self._lock:
            if task_id not in self._tasks:
                raise JournalNotFound("Task journal is not present")
            self._tasks[task_id] = stored
            return copy.deepcopy(stored)

    def is_registered(self, task_id: str) -> bool:
        try:
            self.get_task(task_id)
        except JournalError:
            return False
        return True

    def resolve_session(self, session_id: str) -> str | None:
        if not isinstance(session_id, str) or not session_id or len(session_id) > 200:
            return None
        unreadable = 0
        with self._lock:
            matches: list[str] = []
            for task_id, state in self._tasks.items():
                try:
                    matched = (
                        state.get("session_id") == session_id
                        and state.get("ready") is not True
                        and state.get("status") not in _TERMINAL_TASK_STATES
                    )
                except (AttributeError, TypeError):
                    unreadable += 1
                    continue
                if matched:
                    matches.append(task_id)
            matches.sort()
        if unreadable:
            raise JournalSessionCorrupt(unreadable)
        if len(matches) > 1:
            raise JournalSessionAmbiguous(
                "Session binding is ambiguous: more than one unfinished task is registered"
            )
        return matches[0] if matches else None

    def append_event(
        self,
        task_id: str,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        identity = _task_uuid(event_id)
        canonical_task = _task_uuid(task_id)
        clean_type = str(event_type).strip()
        if not clean_type or len(clean_type) > 100:
            raise JournalError("Event type is empty or too long")
        if not isinstance(payload, dict):
            raise JournalError("Event payload must be an object")
        try:
            payload_json = _canonical(payload)
        except (TypeError, ValueError) as exc:
            raise JournalError("Event payload is not canonical JSON data") from exc
        if len(payload_json.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
            raise JournalError("Event payload exceeds its size limit")
        with self._lock:
            if canonical_task not in self._tasks:
                raise JournalNotFound("Task journal is not present")
            for row in self._events[canonical_task]:
                if row["id"] == identity:
                    if row["event_type"] != clean_type or row["payload"] != payload:
                        raise JournalConflict("Event ID already has a different payload")
                    return {**copy.deepcopy(row), "idempotent": True}
            record = {
                "id": identity,
                "task_id": canonical_task,
                "event_type": clean_type,
                "payload": copy.deepcopy(payload),
                "payload_sha256": hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                "occurred_at": occurred_at or utc_now_iso(),
            }
            self._events[canonical_task].append(record)
            event_ids = self._tasks[canonical_task].setdefault("event_ids", [])
            if identity not in event_ids:
                event_ids.append(identity)
            return {**copy.deepcopy(record), "idempotent": False}

    def list_events(self, task_id: str, *, event_type: str | None = None) -> list[dict[str, Any]]:
        identity = _task_uuid(task_id)
        with self._lock:
            if identity not in self._tasks:
                raise JournalNotFound("Task journal is not present")
            rows = copy.deepcopy(self._events[identity])
        if event_type is None:
            return rows
        return [row for row in rows if row["event_type"] == event_type]
