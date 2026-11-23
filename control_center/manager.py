"""Windows lifecycle manager for the local-only LADS Control Center."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

SOURCE_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = SOURCE_ROOT.parent / "LADS-Workspace" / "control-center-data"
SERVICE_ID = "lads-control-center"
STATE_SCHEMA = "lads-control-center-state-v1"
FRONTEND_FILES = (
    "package.json",
    "package-lock.json",
    "index.html",
    "tsconfig.json",
    "vite.config.ts",
    "src/main.tsx",
    "src/App.tsx",
    "src/styles.css",
)
SENSITIVE_ENV = re.compile(
    r"(?i)(?:^|_)(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|ACCESS_?KEY|KEY|AUTH|CREDENTIAL)S?(?:$|_)"
)


class ManagerError(RuntimeError):
    """An explicit, safe lifecycle failure."""


class _FileTime(ctypes.Structure):
    _fields_ = (
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    )


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    marker = getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & marker)


def _reject_reparse_chain(path: Path) -> None:
    selected = path.absolute()
    for current in (selected, *selected.parents):
        if current.exists() and _is_reparse(current):
            raise ManagerError("Managed path contains a link or reparse point")


def _sanitized_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not SENSITIVE_ENV.search(key)
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _bounded_run(
    arguments: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(arguments),
            cwd=cwd,
            env=dict(env or _sanitized_env()),
            capture_output=True,
            timeout=timeout,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ManagerError("Bounded lifecycle command could not complete") from exc
    if len(result.stdout) + len(result.stderr) > 256_000:
        raise ManagerError("Lifecycle command output exceeded its bound")
    return result


def _shanghai_iso(epoch: float | None = None) -> str:
    from datetime import timedelta, timezone

    selected = (
        datetime.fromtimestamp(epoch, timezone.utc)
        if epoch is not None
        else datetime.now(timezone.utc)
    )
    return selected.astimezone(timezone(timedelta(hours=8), "Asia/Shanghai")).isoformat(
        timespec="seconds"
    )


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    _reject_reparse_chain(path.parent)
    payload = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_text(path: Path, value: str) -> None:
    _reject_reparse_chain(path.parent)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json(path: Path, *, maximum: int = 64 * 1024) -> dict[str, Any]:
    if _is_reparse(path) or not path.is_file() or path.stat().st_size > maximum:
        raise ManagerError("Lifecycle JSON is missing or unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ManagerError("Lifecycle JSON is malformed") from exc
    if not isinstance(value, dict):
        raise ManagerError("Lifecycle JSON must be an object")
    return value


def _process_creation_epoch(pid: int) -> float | None:
    if os.name != "nt":
        raise ManagerError("Control Center lifecycle is supported only on Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
    )
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    created = _FileTime()
    exited = _FileTime()
    kernel = _FileTime()
    user = _FileTime()
    try:
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
    finally:
        kernel32.CloseHandle(handle)
    ticks = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
    return (ticks - 116444736000000000) / 10_000_000


def _terminate_exact(pid: int, expected_creation: float) -> bool:
    current = _process_creation_epoch(pid)
    if current is None or abs(current - expected_creation) > 0.02:
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(0x0001 | 0x00100000 | 0x1000, False, pid)
    if not handle:
        return False
    try:
        if not kernel32.TerminateProcess(handle, 1):
            return False
        kernel32.WaitForSingleObject(handle, 5000)
        return True
    finally:
        kernel32.CloseHandle(handle)


def _parent_process_id(pid: int) -> int | None:
    if os.name != "nt" or type(pid) is not int or pid <= 0:
        return None
    executable = shutil.which("powershell")
    if not executable:
        return None
    # Query one already-observed process, never command lines or credentials.
    command = f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').ParentProcessId"
    try:
        result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, timeout=8, text=True, env=_sanitized_env(), creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    return int(value) if result.returncode == 0 and value.isdigit() else None


def _owned_server_identity(health, instance_id, launcher_pid, launcher_created):
    if not isinstance(health, dict) or health.get("service") != SERVICE_ID or health.get("schema") != "stage1" or health.get("read_only") is not True or health.get("instance_id") != instance_id:
        return None
    pid = health.get("pid")
    if type(pid) is not int or pid <= 0:
        return None
    if pid != launcher_pid and _parent_process_id(pid) != launcher_pid:
        return None
    created = _process_creation_epoch(pid)
    if created is None or created < launcher_created - 0.01:
        return None
    return pid, created


class ControlCenterManager:
    def __init__(
        self,
        *,
        source_root: Path = SOURCE_ROOT,
        data_root: Path = DATA_ROOT,
        wait_seconds: float = 20.0,
    ) -> None:
        self.source_root = source_root.resolve(strict=True)
        data_input = data_root.absolute()
        if data_input.is_relative_to(self.source_root):
            raise ManagerError("Control Center runtime must be outside the source worktree")
        self.data_root = data_input.resolve(strict=False)
        self.venv = self.data_root / "venv"
        self.venv_python = self.venv / "Scripts" / "python.exe"
        self.frontend = self.data_root / "frontend"
        self.dist = self.frontend / "dist"
        self.logs = self.data_root / "logs"
        self.stdout_log = self.logs / "server.stdout.log"
        self.stderr_log = self.logs / "server.stderr.log"
        self.state_path = self.data_root / "server.state.json"
        self.pid_path = self.data_root / "server.pid"
        self.started_path = self.data_root / "server.started-at"
        self.wait_seconds = float(wait_seconds)
        if not 3 <= self.wait_seconds <= 60:
            raise ManagerError("Lifecycle wait bound is invalid")

    def _ensure_dirs(self) -> None:
        for path in (self.data_root, self.frontend, self.logs):
            _reject_reparse_chain(path)
            path.mkdir(parents=True, exist_ok=True)
            if _is_reparse(path) or not path.is_dir():
                raise ManagerError("Control Center runtime directory is unsafe")

    def _state(self) -> dict[str, Any] | None:
        if not self.state_path.exists():
            return None
        state = _read_json(self.state_path)
        required = {
            "schema",
            "service",
            "stage",
            "pid",
            "process_created_at",
            "started_at",
            "instance_id",
            "port",
            "url",
        }
        if set(state) != required:
            raise ManagerError("Control Center state schema is malformed")
        if (
            state.get("schema") != STATE_SCHEMA
            or state.get("service") != SERVICE_ID
            or state.get("stage") != 1
            or type(state.get("pid")) is not int
            or state["pid"] <= 0
            or type(state.get("process_created_at")) not in {int, float}
            or not isinstance(state.get("instance_id"), str)
            or re.fullmatch(
                r"[0-9a-f-]{36}", state.get("instance_id", "")
            )
            is None
            or type(state.get("port")) is not int
            or not 1 <= state["port"] <= 65535
            or not isinstance(state.get("started_at"), str)
            or state.get("url") != f"http://127.0.0.1:{state['port']}"
        ):
            raise ManagerError("Control Center process state is malformed")
        if (
            not self.pid_path.is_file()
            or _is_reparse(self.pid_path)
            or self.pid_path.read_text(encoding="utf-8").strip() != str(state["pid"])
            or not self.started_path.is_file()
            or _is_reparse(self.started_path)
            or self.started_path.read_text(encoding="utf-8").strip() != state["started_at"]
        ):
            raise ManagerError("Control Center PID or start-time receipt is malformed")
        return state

    def _state_matches_process(self, state: Mapping[str, Any]) -> bool:
        created = _process_creation_epoch(int(state["pid"]))
        return created is not None and abs(created - float(state["process_created_at"])) <= 0.02

    def _write_state(self, state: Mapping[str, Any]) -> None:
        _atomic_json(self.state_path, state)
        _atomic_text(self.pid_path, f"{state['pid']}\n")
        _atomic_text(self.started_path, f"{state['started_at']}\n")

    def _clear_state(self) -> None:
        for path in (self.state_path, self.pid_path, self.started_path):
            if path.exists() and _is_reparse(path):
                raise ManagerError("Lifecycle state file is a link or reparse point")
            path.unlink(missing_ok=True)

    def _health(self, port: int) -> dict[str, Any] | None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/health",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                if response.status != 200:
                    return None
                raw = response.read(16_385)
        except (OSError, urllib.error.URLError, ValueError):
            return None
        if len(raw) > 16_384:
            return None
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _health_matches(
        self,
        health: Mapping[str, Any] | None,
        *,
        instance_id: str,
        pid: int,
    ) -> bool:
        return bool(
            health
            and health.get("service") == SERVICE_ID
            and health.get("read_only") is True
            and health.get("schema") == "stage1"
            and health.get("instance_id") == instance_id
            and health.get("pid") == pid
        )

    def _require_runtime_python(self, *, install_dependencies: bool) -> None:
        if os.name != "nt":
            raise ManagerError("Control Center lifecycle is supported only on Windows")
        if not self.venv_python.is_file() or _is_reparse(self.venv_python):
            raise ManagerError("Control Center venv is missing; use -InstallDependencies")
        try:
            same = Path(sys.executable).resolve() == self.venv_python.resolve()
        except OSError:
            same = False
        if not same:
            raise ManagerError("Control Center manager must run with the external venv Python")
        if sys.version_info < (3, 11):
            raise ManagerError("Control Center requires Python 3.11 or newer")
        if install_dependencies:
            installed = _bounded_run(
                [
                    str(self.venv_python),
                    "-B",
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "-r",
                    str(self.source_root / "requirements-control-center.txt"),
                ],
                cwd=self.source_root,
                timeout=300,
            )
            if installed.returncode != 0:
                raise ManagerError("Control Center Python dependency installation failed")
        imported = _bounded_run(
            [
                str(self.venv_python),
                "-B",
                "-c",
                "import fastapi, uvicorn",
            ],
            cwd=self.source_root,
            timeout=20,
        )
        if imported.returncode != 0:
            raise ManagerError("Control Center Python dependencies are missing")

    def _node_tools(self) -> tuple[str, str]:
        node = shutil.which("node")
        npm = shutil.which("npm")
        if node is None or npm is None:
            raise ManagerError("Node.js and npm are required")
        version = _bounded_run([node, "--version"], cwd=self.source_root, timeout=10)
        match = re.fullmatch(r"v([0-9]+)\.([0-9]+)\.([0-9]+)\s*", version.stdout)
        if version.returncode != 0 or match is None:
            raise ManagerError("Node.js version could not be verified")
        parsed = tuple(int(item) for item in match.groups())
        if parsed < (22, 12, 0):
            raise ManagerError("Node.js 22.12 or newer is required")
        return node, npm

    def _verify_frontend_baseline(self) -> None:
        frontend = self.source_root / "control_center" / "frontend"
        for relative in FRONTEND_FILES:
            source = frontend / relative
            if not source.is_file() or _is_reparse(source):
                raise ManagerError("Prepared frontend source is incomplete or unsafe")
        package = _read_json(frontend / "package.json")
        lock = _read_json(frontend / "package-lock.json", maximum=2 * 1024 * 1024)
        root = lock.get("packages", {}).get("")
        if (
            lock.get("lockfileVersion") != 3
            or lock.get("name") != package.get("name")
            or lock.get("version") != package.get("version")
            or not isinstance(root, dict)
            or root.get("dependencies") != package.get("dependencies")
            or root.get("devDependencies") != package.get("devDependencies")
        ):
            raise ManagerError("Frontend package lock does not match the prepared baseline")
        for name, version in {
            **package.get("dependencies", {}),
            **package.get("devDependencies", {}),
        }.items():
            if lock.get("packages", {}).get(f"node_modules/{name}", {}).get("version") != version:
                raise ManagerError("Frontend dependency versions differ from the prepared lock")

    def _source_fingerprint(self) -> str:
        digest = hashlib.sha256()
        frontend = self.source_root / "control_center" / "frontend"
        for relative in FRONTEND_FILES:
            source = frontend / relative
            raw = source.read_bytes()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(raw)
            digest.update(b"\0")
        return digest.hexdigest()

    def _sync_frontend(self) -> str:
        source_frontend = self.source_root / "control_center" / "frontend"
        for forbidden in (source_frontend / "node_modules", source_frontend / "dist"):
            if forbidden.exists():
                raise ManagerError("Frontend build artifacts must not exist in the source worktree")
        for relative in FRONTEND_FILES:
            source = source_frontend / relative
            target = self.frontend / relative
            _reject_reparse_chain(target.parent)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and _is_reparse(target):
                raise ManagerError("External frontend target is a link or reparse point")
            shutil.copy2(source, target)
        return self._source_fingerprint()

    def _prepare_frontend(self, *, install_dependencies: bool) -> tuple[str, str]:
        self._verify_frontend_baseline()
        _node, npm = self._node_tools()
        fingerprint = self._sync_frontend()
        node_modules = self.frontend / "node_modules"
        if install_dependencies:
            installed = _bounded_run(
                [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                cwd=self.frontend,
                timeout=300,
            )
            if installed.returncode != 0:
                raise ManagerError("npm ci failed; no dependency script was run")
        if not node_modules.is_dir():
            raise ManagerError("Frontend dependencies are not installed; use -InstallDependencies")

        receipt_path = self.frontend / ".build-receipt.json"
        previous = _read_json(receipt_path) if receipt_path.exists() else {}
        needs_build = not (self.dist / "index.html").is_file() or previous.get("source_sha256") != fingerprint
        if needs_build:
            built = _bounded_run(
                [npm, "run", "build"],
                cwd=self.frontend,
                timeout=300,
            )
            if built.returncode != 0 or not (self.dist / "index.html").is_file():
                raise ManagerError("Frontend build failed outside the source worktree")
            _atomic_json(
                receipt_path,
                {
                    "schema": "lads-control-center-build-v1",
                    "source_sha256": fingerprint,
                    "built_at": _shanghai_iso(),
                },
            )
        return npm, fingerprint

    def _port_available(self, port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if exclusive is not None:
                sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False
        finally:
            sock.close()

    def _probe_running(self, state: Mapping[str, Any]) -> dict[str, Any]:
        if not self._state_matches_process(state):
            raise ManagerError("Recorded PID ownership or start time no longer matches")
        health = self._health(int(state["port"]))
        if not self._health_matches(
            health,
            instance_id=str(state["instance_id"]),
            pid=int(state["pid"]),
        ):
            raise ManagerError("Health identity does not match owned process state")
        return {
            "status": "RUNNING",
            "service": SERVICE_ID,
            "url": state["url"],
            "pid": state["pid"],
            "instance_id": state["instance_id"],
            "started_at": state["started_at"],
        }

    def start(
        self,
        *,
        port: int = 8765,
        no_browser: bool = False,
        install_dependencies: bool = False,
    ) -> dict[str, Any]:
        if type(port) is not int or not 1 <= port <= 65535:
            raise ManagerError("Port must be between 1 and 65535")
        self._ensure_dirs()
        existing = self._state()
        if existing is not None:
            if existing["port"] != port:
                raise ManagerError("Control Center state owns a different local port")
            health = self._health(port)
            if self._state_matches_process(existing) and self._health_matches(
                health,
                instance_id=existing["instance_id"],
                pid=existing["pid"],
            ):
                result = {
                    "status": "ALREADY_RUNNING",
                    "service": SERVICE_ID,
                    "url": existing["url"],
                    "pid": existing["pid"],
                    "instance_id": existing["instance_id"],
                }
                if not no_browser:
                    webbrowser.open(existing["url"] + "/")
                return result
            raise ManagerError("Lifecycle state is stale; refusing to reuse or overwrite it")
        if self.pid_path.exists() or self.started_path.exists():
            raise ManagerError("Orphaned lifecycle markers exist; explicit inspection is required")

        self._require_runtime_python(install_dependencies=install_dependencies)
        _npm, _fingerprint = self._prepare_frontend(
            install_dependencies=install_dependencies
        )
        if not self._port_available(port):
            raise ManagerError("Local port is occupied; unrelated services are never reused")

        instance_id = str(uuid.uuid4())
        url = f"http://127.0.0.1:{port}"
        command = [
            str(self.venv_python),
            "-B",
            "-m",
            "control_center.server",
            "--port",
            str(port),
            "--instance-id",
            instance_id,
            "--frontend-dist",
            str(self.dist),
        ]
        env = _sanitized_env()
        env["PYTHONPATH"] = str(self.source_root)
        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        with self.stdout_log.open("ab") as stdout, self.stderr_log.open("ab") as stderr:
            process = subprocess.Popen(
                command,
                cwd=self.source_root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                creationflags=creation_flags,
            )
        created = _process_creation_epoch(process.pid)
        if created is None:
            process.kill()
            raise ManagerError("Started process ownership could not be recorded")
        state = {
            "schema": STATE_SCHEMA,
            "service": SERVICE_ID,
            "stage": 1,
            "pid": process.pid,
            "process_created_at": created,
            "started_at": _shanghai_iso(created),
            "instance_id": instance_id,
            "port": port,
            "url": url,
        }
        try:
            self._write_state(state)
        except Exception:
            _terminate_exact(process.pid, created)
            raise
        deadline = time.monotonic() + self.wait_seconds
        try:
            while time.monotonic() < deadline:
                if _process_creation_epoch(process.pid) is None:
                    raise ManagerError("Control Center exited before health verification")
                health = self._health(port)
                identity = _owned_server_identity(health, instance_id, process.pid, created)
                if identity is not None:
                    server_pid, server_created = identity
                    state.update(pid=server_pid, process_created_at=server_created, started_at=_shanghai_iso(server_created))
                    self._write_state(state)
                    if not no_browser:
                        webbrowser.open(url + "/")
                    return {
                        "status": "STARTED",
                        "service": SERVICE_ID,
                        "url": url,
                        "pid": state["pid"],
                        "instance_id": instance_id,
                        "started_at": state["started_at"],
                    }
                time.sleep(0.25)
            raise ManagerError("Control Center did not produce matching health in time")
        except Exception:
            if state["pid"] != process.pid:
                _terminate_exact(int(state["pid"]), float(state["process_created_at"]))
            if _process_creation_epoch(process.pid) is not None:
                _terminate_exact(process.pid, created)
            self._clear_state()
            raise

    def check(self, *, port: int = 8765) -> dict[str, Any]:
        state = self._state()
        if state is None:
            raise ManagerError("No owned Control Center state is present")
        if state["port"] != port:
            raise ManagerError("Requested port differs from owned Control Center state")
        return self._probe_running(state)

    def stop(self, *, port: int = 8765) -> dict[str, Any]:
        state = self._state()
        if state is None:
            if self.pid_path.exists() or self.started_path.exists():
                raise ManagerError("Orphaned ownership markers require inspection")
            return {"status": "NO_OWNED_STATE", "message": "No owned process was changed"}
        if state["port"] != port:
            raise ManagerError("Requested port differs from owned Control Center state")
        running = self._probe_running(state)
        if not _terminate_exact(int(state["pid"]), float(state["process_created_at"])):
            raise ManagerError("Exact owned process could not be terminated")
        self._clear_state()
        return {
            "status": "STOPPED",
            "service": SERVICE_ID,
            "pid": running["pid"],
            "instance_id": running["instance_id"],
        }

    def bootstrap(self) -> dict[str, Any]:
        self._ensure_dirs()
        if sys.version_info < (3, 11):
            raise ManagerError("Control Center requires Python 3.11 or newer")
        if not self.venv_python.is_file():
            created = _bounded_run(
                [sys.executable, "-B", "-m", "venv", str(self.venv)],
                cwd=self.source_root,
                timeout=120,
            )
            if created.returncode != 0 or not self.venv_python.is_file():
                raise ManagerError("External Python venv could not be created")
        installed = _bounded_run(
            [
                str(self.venv_python),
                "-B",
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-r",
                str(self.source_root / "requirements-control-center.txt"),
            ],
            cwd=self.source_root,
            timeout=300,
        )
        if installed.returncode != 0:
            raise ManagerError("Control Center Python dependency installation failed")
        imported = _bounded_run(
            [str(self.venv_python), "-B", "-c", "import fastapi, uvicorn"],
            cwd=self.source_root,
            timeout=20,
        )
        if imported.returncode != 0:
            raise ManagerError("Control Center runtime imports failed after installation")
        return {
            "status": "BOOTSTRAP_READY",
            "service": SERVICE_ID,
            "python": str(self.venv_python),
        }


def _port(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "stop", "check", "bootstrap"))
    parser.add_argument("--port", type=_port, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--install-dependencies", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manager = ControlCenterManager()
        if args.command == "bootstrap":
            result = manager.bootstrap()
        elif args.command == "start":
            result = manager.start(
                port=args.port,
                no_browser=args.no_browser,
                install_dependencies=args.install_dependencies,
            )
        elif args.command == "stop":
            result = manager.stop(port=args.port)
        else:
            result = manager.check(port=args.port)
    except ManagerError as exc:
        print(
            json.dumps(
                {
                    "status": "ERROR",
                    "service": SERVICE_ID,
                    "code": "CONTROL_CENTER_LIFECYCLE",
                    "message": str(exc),
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
