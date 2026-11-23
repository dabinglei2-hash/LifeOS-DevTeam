"""Credential-free host outbox. stdlib only, optional; never reads prompts or output."""
from __future__ import annotations

import json
import os
import stat
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(__file__).resolve().parents[2] / "LADS-Workspace" / "control-center-data"
_local = threading.local()


@contextmanager
def reporting(enabled=True):
    """Explicit host scope; never enabled merely by importing or running tests."""
    previous = getattr(_local, "enabled", False)
    _local.enabled = enabled
    try:
        yield
    finally:
        _local.enabled = previous


def _safe(path):
    for node in (path, *path.parents):
        if node.exists() and (node.is_symlink() or getattr(node.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            return False
    return True


def send(*, task_id, role, event_type, project="devteam", run_id=None, pid=None, created=None, model=None, payload=None, _enabled=None):
    if not (_enabled if _enabled is not None else getattr(_local, "enabled", False)):
        return False
    if not _safe(HOME) or not (HOME / "telemetry.enabled.json").is_file():
        return False
    try:
        task_id = str(uuid.UUID(task_id))
        run_id = str(uuid.UUID(run_id or task_id))
        if role not in {"gpt-controller", "scanner", "reviewer", "builder", "qa-tester"} or project not in {"lifeos", "devteam"}:
            return False
        event_id = str(uuid.uuid4())
        folder = HOME / "spool"
        if not _safe(folder):
            return False
        folder.mkdir(exist_ok=True)
        envelope = {"event_id": event_id, "task_id": task_id, "run_id": run_id, "agent_id": role,
                    "project": project, "event_type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "lads-controller-host-v2" if role == "gpt-controller" else "lads-worker-host-v2",
                    "provider": "openai" if role == "gpt-controller" else "deepseek", "model_name": model,
                    "process_id": pid, "process_created": created, "payload": payload or {}}
        temporary = folder / (event_id + ".tmp")
        temporary.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
        temporary.replace(folder / (event_id + ".json"))
        return True
    except (OSError, ValueError, TypeError):
        # No raw exception/arguments: this path must never log a prompt or a key.
        try:
            with (HOME / "telemetry-write-errors.log").open("a", encoding="utf-8") as log:
                log.write(datetime.now(timezone.utc).isoformat() + " OUTBOX_WRITE_FAILED\n")
        except OSError:
            pass  # Telemetry failure cannot change the existing permission/publication gates.
        return False


class WorkerMonitor:
    def __init__(self, task_id, role, model, project="devteam"):
        self.task_id, self.role, self.model, self.project = task_id, role, model, project
        self.enabled = getattr(_local, "enabled", False)
        self.run_id, self.pid, self.created = str(uuid.uuid4()), None, None

    def emit(self, kind, payload=None):
        return send(task_id=self.task_id, role=self.role, model=self.model, project=self.project,
                    run_id=self.run_id, event_type=kind, pid=self.pid, created=self.created, payload=payload, _enabled=self.enabled)

    def observe(self, kind, process, details):
        self.pid = process.pid
        if kind == "started":
            # A platform process creation identity, not a guessed PID match.
            try:
                if os.name == "nt":
                    import ctypes
                    from ctypes import wintypes
                    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
                    times = [wintypes.FILETIME() for _ in range(4)]
                    if kernel.GetProcessTimes(wintypes.HANDLE(int(process._handle)), *[ctypes.byref(t) for t in times]):
                        self.created = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
                else:
                    self.created = Path(f"/proc/{process.pid}/stat").read_text().split()[21]
            except (OSError, AttributeError):
                self.created = None
            self.emit("AGENT_STARTED")
            self.emit("AGENT_TASK_STARTED")
        elif kind == "heartbeat":
            self.emit("AGENT_HEARTBEAT")
        elif kind == "finished":
            self.emit("AGENT_TASK_COMPLETED" if details.get("exit_code") == 0 and not details.get("timed_out") else "AGENT_TASK_FAILED", details)
        elif kind == "error":
            self.emit("AGENT_ERROR", {"error_code": "PROCESS_BOUNDARY_ERROR"})

    def __enter__(self):
        self.previous = getattr(_local, "observer", None)
        _local.observer = self.observe
        return self

    def __exit__(self, kind, value, traceback):
        if kind:
            self.emit("AGENT_ERROR", {"error_code": "WORKER_VALIDATION_FAILED"})
        _local.observer = self.previous


def observer():
    return getattr(_local, "observer", None)
