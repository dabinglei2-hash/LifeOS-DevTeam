"""Credential-free Codex app-server executor for the pinned Stage 3 suites.

The executor deliberately exposes no thread, turn, model, shell, or arbitrary
command API. Each invocation starts a private app-server process, performs the
JSON-RPC ``initialize`` handshake, and sends exactly one ``command/exec``.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

MAX_JSON_LINE = 2 * 1024 * 1024
MAX_PROCESS_OUTPUT = 8 * 1024 * 1024
_SAFE_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,100}$")
_SENSITIVE_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH)", re.I)
_URL_CREDENTIAL = re.compile(r"(?i)(https?://)[^/@\s]+@")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_SHELL_META = re.compile(r"[\r\n;&|><`$]")
_RUNTIME_ENV_NAMES = frozenset(
    {
        "ALLUSERSPROFILE",
        "APPDATA",
        "CODEX_HOME",
        "CODEX_SANDBOX_NETWORK_DISABLED",
        "CODEX_VERSION",
        "COMSPEC",
        "COMMONPROGRAMFILES",
        "DRIVERDATA",
        "HOMEDRIVE",
        "HOMEPATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PSModulePath",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
)
_RUNTIME_TOOLS = frozenset({"codex", "node", "python"})


class RpcExecutionError(RuntimeError):
    """A bounded, nonsecret app-server execution failure."""
    def __init__(self, message: str, *, raw_detail: Any = None) -> None:
        super().__init__(message)
        self.safe_detail = _safe_error_text(raw_detail if raw_detail is not None else message)


def _safe_error_text(value: Any) -> str:
    text = str(value)
    text = _URL_CREDENTIAL.sub(r"\1[REDACTED]@", text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = re.sub(r"\b(?:sk-(?:proj-)?|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{12,}", "[REDACTED]", text)
    text = re.sub(r"(?i)\b(api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*['\"]?[^\s'\"]{12,}", r"\1=[REDACTED]", text)
    return text


def bounded_error_text(value: Any, limit: int = 800) -> str:
    text = _safe_error_text(value).replace("\r", " ").replace("\n", " ")
    maximum = max(1, min(int(limit), 2000))
    if len(text) <= maximum:
        return text
    marker = " ...[truncated; terminal tail preserved]... "
    if maximum <= len(marker) + 20:
        return text[-maximum:]
    prefix = min(180, (maximum-len(marker))//3)
    return text[:prefix]+marker+text[-(maximum-len(marker)-prefix):]


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    marker = getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & marker)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
    """Terminate the app-server process and prove bounded process-tree cleanup."""
    failures: list[str] = []
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class Entry(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateToolhelp32Snapshot.argtypes = [
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            kernel32.Process32FirstW.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(Entry),
            ]
            kernel32.Process32FirstW.restype = wintypes.BOOL
            kernel32.Process32NextW.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(Entry),
            ]
            kernel32.Process32NextW.restype = wintypes.BOOL
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel32.TerminateProcess.restype = wintypes.BOOL
            kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
            relations: dict[int, int] = {}
            invalid_handle = ctypes.c_void_p(-1).value
            if not snapshot or int(snapshot) == invalid_handle:
                failures.append(
                    "CreateToolhelp32Snapshot failed with code "
                    + str(ctypes.get_last_error())
                )
            else:
                try:
                    entry = Entry()
                    entry.dwSize = ctypes.sizeof(Entry)
                    ctypes.set_last_error(0)
                    active = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
                    while active:
                        relations[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                        active = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
                    if ctypes.get_last_error() not in {0, 18}:
                        failures.append(
                            "Process32 enumeration failed with code "
                            + str(ctypes.get_last_error())
                        )
                finally:
                    if not kernel32.CloseHandle(snapshot):
                        failures.append(
                            "CloseHandle(snapshot) failed with code "
                            + str(ctypes.get_last_error())
                        )
            pending = {process.pid}
            targets: set[int] = set()
            while pending:
                current = pending.pop()
                targets.add(current)
                pending.update(
                    pid for pid, parent in relations.items()
                    if parent == current and pid not in targets
                )
            for pid in sorted(targets, reverse=True):
                handle = kernel32.OpenProcess(0x0001 | 0x00100000, False, pid)
                if not handle:
                    error = ctypes.get_last_error()
                    if error != 87:
                        failures.append(f"OpenProcess({pid}) failed with code {error}")
                    continue
                try:
                    stopped = kernel32.WaitForSingleObject(handle, 0) == 0
                    if not stopped and not kernel32.TerminateProcess(handle, 1):
                        error = ctypes.get_last_error()
                        stopped = kernel32.WaitForSingleObject(handle, 5000) == 0
                        if not stopped:
                            failures.append(f"TerminateProcess({pid}) failed with code {error}")
                    elif not stopped and kernel32.WaitForSingleObject(handle, 5000) != 0:
                        failures.append(
                            f"Process({pid}) did not signal exit after termination"
                        )
                finally:
                    if not kernel32.CloseHandle(handle):
                        failures.append(
                            f"CloseHandle({pid}) failed with code "
                            + str(ctypes.get_last_error())
                        )
        except Exception as exc:
            failures.append(
                "Windows process-tree termination setup failed: "
                + bounded_error_text(exc)
            )
            try:
                process.kill()
            except OSError as kill_exc:
                failures.append("Root process kill failed: " + bounded_error_text(kill_exc))
    else:
        if process.poll() is None:
            try:
                process.kill()
            except OSError as exc:
                failures.append("Process kill failed: " + bounded_error_text(exc))
    if process.poll() is None:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired as exc:
            raise RpcExecutionError(
                "app-server process tree did not stop after tree termination"
            ) from exc
        except OSError as exc:
            raise RpcExecutionError(
                "app-server process cleanup could not be verified: "
                + bounded_error_text(exc)
            ) from exc
        if process.poll() is None:
            raise RpcExecutionError("app-server process is still running after cleanup")
    if failures:
        raise RpcExecutionError(
            "app-server process-tree cleanup was incomplete: "
            + "; ".join(failures[:8])
        )


class _JsonLineProcess:
    def __init__(self, argv: list[str], cwd: Path, environment: Mapping[str, str]) -> None:
        self.process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=dict(environment),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            bufsize=0,
        )
        self.messages: queue.Queue[dict[str, Any] | RpcExecutionError] = queue.Queue()
        self.stderr = bytearray()
        self.overflow = False
        self._threads = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        while True:
            line = self.process.stdout.readline(MAX_JSON_LINE + 1)
            if not line:
                return
            if len(line) > MAX_JSON_LINE:
                self.overflow = True
                self.messages.put(RpcExecutionError("app-server JSON line exceeded its bound"))
                return
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeError, ValueError):
                self.messages.put(RpcExecutionError("app-server emitted non-JSON stdout"))
                return
            if not isinstance(value, dict):
                self.messages.put(RpcExecutionError("app-server emitted a non-object message"))
                return
            self.messages.put(value)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        while True:
            chunk = self.process.stderr.read(65536)
            if not chunk:
                return
            room = MAX_PROCESS_OUTPUT - len(self.stderr)
            if room > 0:
                self.stderr.extend(chunk[:room])
            if len(chunk) > room:
                self.overflow = True

    def send(self, value: Mapping[str, Any]) -> None:
        if self.process.stdin is None:
            raise RpcExecutionError("app-server stdin is unavailable")
        raw = (
            json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        try:
            self.process.stdin.write(raw)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RpcExecutionError(
                "app-server stdin closed: " + bounded_error_text(exc)
            ) from exc

    def receive_id(self, request_id: str, deadline: float) -> dict[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("app-server response timed out")
            try:
                item = self.messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError("app-server response timed out") from exc
            if isinstance(item, RpcExecutionError):
                raise item
            if item.get("id") != request_id:
                continue
            error = item.get("error")
            if error is not None:
                if isinstance(error, dict):
                    detail = error.get("message") or error.get("code") or "unknown"
                else:
                    detail = error
                if isinstance(detail, str) and re.search(r"\bcommand timed out\b", detail, re.I):
                    raise TimeoutError("Sandbox command timed out")
                raise RpcExecutionError(
                    "app-server RPC error: " + bounded_error_text(detail),
                    raw_detail=detail,
                )
            result = item.get("result")
            if not isinstance(result, dict):
                raise RpcExecutionError("app-server RPC result is not an object")
            return result

    def close(self) -> None:
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        _terminate_tree(self.process)
        for thread in self._threads:
            thread.join(timeout=10)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


class CodexJsonRpcExecutor:
    """Serial executor that permits only initialize and command/exec RPCs."""

    def __init__(
        self,
        *,
        work_root: str | Path,
        allowed_commands: Iterable[str],
        codex_executable: str | None = None,
        executable_resolver: Any = shutil.which,
        process_factory: Any = _JsonLineProcess,
        maximum_timeout: float = 300.0,
        runtime_environment: Mapping[str, str] | None = None,
        runtime_executables: Mapping[str, str] | None = None,
    ) -> None:
        self.work_root = Path(work_root).resolve(strict=True)
        if not self.work_root.is_dir() or _is_reparse(self.work_root):
            raise RpcExecutionError("Executor work root must be a regular directory")
        if self.work_root == self.work_root.parent:
            raise RpcExecutionError("Executor work root cannot be a filesystem root")
        commands = tuple(str(item) for item in allowed_commands)
        if not commands or len(commands) > 32 or len(set(commands)) != len(commands):
            raise RpcExecutionError("Executor command allowlist is malformed")
        self.allowed_commands = frozenset(commands)
        self.runtime_environment = self._sanitized_runtime_environment(
            runtime_environment
        )
        configured = dict(runtime_executables or {})
        if set(configured) - _RUNTIME_TOOLS:
            raise RpcExecutionError("Runtime executable map contains an unsupported tool")
        if codex_executable is not None:
            if "codex" in configured:
                raise RpcExecutionError("codex executable was configured twice")
            configured["codex"] = codex_executable
        self.executable_resolver = executable_resolver
        self._configured_runtime_executables = configured
        self._runtime_executables: dict[str, str] = {}
        for tool in ("codex", "python", "node"):
            if tool in configured or tool == "codex":
                self._runtime_executables[tool] = self._resolve_runtime_executable(tool)
        if not self._runtime_executables.get("codex"):
            raise RpcExecutionError("codex executable is unavailable")
        self.command_argv: dict[str, tuple[str, ...]] = {}
        for command in commands:
            self.command_argv[command] = self._command_argv(command)
        self.runtime_metadata_value = self._runtime_metadata()
        if not 1 <= float(maximum_timeout) <= 300:
            raise RpcExecutionError("Executor timeout bound is invalid")
        self.maximum_timeout = float(maximum_timeout)
        self.process_factory = process_factory
        self._lock = threading.Lock()
        self._processes: set[Any] = set()

    @staticmethod
    def _sanitized_runtime_environment(
        overrides: Mapping[str, str] | None,
    ) -> dict[str, str]:
        source: Mapping[str, str] = os.environ if overrides is None else overrides
        if not isinstance(source, Mapping):
            raise RpcExecutionError("Runtime environment must be a mapping")
        if overrides is not None and set(source) - _RUNTIME_ENV_NAMES:
            raise RpcExecutionError(
                "Runtime environment metadata contains an unsupported name"
            )
        result: dict[str, str] = {}
        for name in sorted(_RUNTIME_ENV_NAMES):
            if name not in source:
                continue
            value = source[name]
            if (
                not isinstance(value, str)
                or len(value) > 32767
                or "\x00" in value
                or _SENSITIVE_NAME.search(name)
            ):
                raise RpcExecutionError("Runtime environment metadata is unsafe")
            result[name] = value
        if not result.get("PATH"):
            raise RpcExecutionError("Runtime environment must preserve an original PATH")
        return result

    def _resolve_runtime_executable(self, tool: str) -> str:
        if tool not in _RUNTIME_TOOLS:
            raise RpcExecutionError("Runtime tool is not approved")
        configured = self._configured_runtime_executables.get(tool)
        if configured is None:
            configured = self.executable_resolver(
                tool,
                path=self.runtime_environment.get("PATH"),
            )
        if not isinstance(configured, str) or not configured:
            raise RpcExecutionError(f"Trusted runtime executable is unavailable: {tool}")
        selected = Path(configured)
        if not selected.is_absolute():
            raise RpcExecutionError(f"Trusted runtime executable is not absolute: {tool}")
        try:
            resolved = selected.resolve(strict=True)
        except OSError as exc:
            raise RpcExecutionError(
                f"Trusted runtime executable cannot be resolved: {tool}"
            ) from exc
        if not resolved.is_file() or _is_reparse(resolved):
            raise RpcExecutionError(f"Trusted runtime executable is not a regular file: {tool}")
        if resolved.suffix.casefold() not in {".exe", ".com", ""}:
            raise RpcExecutionError(f"Trusted runtime executable type is unsupported: {tool}")
        return str(resolved)

    def _command_argv(self, command: str) -> tuple[str, ...]:
        if (
            not isinstance(command, str)
            or not command
            or len(command) > 1000
            or _SHELL_META.search(command)
        ):
            raise RpcExecutionError("Executor command is not a safe fixed argv form")
        try:
            parts = shlex.split(command, posix=False)
        except ValueError as exc:
            raise RpcExecutionError("Executor command could not be mapped to argv") from exc
        if not 2 <= len(parts) <= 64 or any("\x00" in part for part in parts):
            raise RpcExecutionError("Executor command argv is malformed")
        tool = parts[0]
        if tool not in {"python", "node"}:
            raise RpcExecutionError("Executor command uses an unapproved runtime")
        executable = self._runtime_executables.get(tool)
        if not executable:
            executable = self._resolve_runtime_executable(tool)
            self._runtime_executables[tool] = executable
        return (executable, *parts[1:])

    def _runtime_metadata(self) -> dict[str, Any]:
        executables: list[dict[str, Any]] = []
        for tool, path in sorted(self._runtime_executables.items()):
            selected = Path(path)
            digest = hashlib.sha256()
            with selected.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            executables.append(
                {
                    "tool": tool,
                    "sha256": digest.hexdigest(),
                    "size": selected.stat().st_size,
                }
            )
        return {
            "policy": "sanitized-original-runtime-metadata-v1",
            "environment_names": sorted(self.runtime_environment),
            "codex_home_preserved": bool(self.runtime_environment.get("CODEX_HOME")),
            "executables": executables,
        }

    def _safe_path(self, value: str | Path, label: str, *, directory: bool) -> Path:
        selected = Path(value)
        if not selected.is_absolute():
            raise RpcExecutionError(f"{label} must be absolute")
        resolved = selected.resolve(strict=True)
        if not _within(resolved, self.work_root):
            raise RpcExecutionError(f"{label} escapes the owned acceptance work root")
        if _is_reparse(resolved):
            raise RpcExecutionError(f"{label} is a reparse/symlink")
        if directory and not resolved.is_dir():
            raise RpcExecutionError(f"{label} is not a directory")
        return resolved

    @staticmethod
    def _command_environment(overrides: Mapping[str, Any]) -> dict[str, str | None]:
        if not isinstance(overrides, Mapping):
            raise RpcExecutionError("Executor environment must be a mapping")
        clean: dict[str, str | None] = {}
        for name, value in overrides.items():
            if (
                not isinstance(name, str)
                or name not in _RUNTIME_ENV_NAMES | {
                    "LADS_ARTIFACT_DIR",
                    "LIFEOS_BASE_URL",
                    "LADS_PRODUCT_QA_ROOT",
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                    "NO_PROXY",
                }
                or _SENSITIVE_NAME.search(name)
            ):
                raise RpcExecutionError("Executor environment override is unsafe")
            if value is None:
                clean[name] = None
                continue
            if (
                not isinstance(value, str)
                or len(value) > 4096
                or "\x00" in value
            ):
                raise RpcExecutionError("Executor environment override is unsafe")
            clean[name] = value
        return clean

    def _command_params(
        self,
        *,
        command: str,
        cwd: Path,
        timeout: float,
        network_access: bool,
        writable_roots: Iterable[str | Path],
        environment: Mapping[str, str],
    ) -> dict[str, Any]:
        if command not in self.allowed_commands:
            raise RpcExecutionError("Executor command is outside the pinned allowlist")
        if network_access is not False:
            raise RpcExecutionError("Stage 3 command execution requires network_access=false")
        roots = [
            str(self._safe_path(root, "writable root", directory=True))
            for root in writable_roots
        ]
        if not roots or len(set(roots)) != len(roots):
            raise RpcExecutionError("Writable roots are missing or duplicated")
        argv = self.command_argv.get(command)
        if argv is None:
            argv = self._command_argv(command)
            self.command_argv[command] = argv
        return {
            "command": list(argv),
            "cwd": str(cwd),
            "timeoutMs": int(timeout * 1000),
            "sandboxPolicy": {
                "type": "workspaceWrite",
                "writableRoots": roots,
                "networkAccess": False,
                "excludeTmpdirEnvVar": True,
                "excludeSlashTmp": True,
            },
            "env": self._command_environment(environment),
        }

    @staticmethod
    def _receipt(result: Mapping[str, Any], stdout: str, stderr: str) -> dict[str, Any]:
        stdout = stdout if isinstance(stdout, str) else str(stdout)
        stderr = stderr if isinstance(stderr, str) else str(stderr)
        stdout_raw = stdout.encode("utf-8", errors="replace")
        stderr_raw = stderr.encode("utf-8", errors="replace")
        overflow = len(stdout_raw) > MAX_PROCESS_OUTPUT or len(stderr_raw) > MAX_PROCESS_OUTPUT
        stdout = stdout[:MAX_PROCESS_OUTPUT]
        stderr = stderr[:MAX_PROCESS_OUTPUT]
        exit_code = result.get("exitCode", result.get("exit_code"))
        if type(exit_code) is not int:
            raise RpcExecutionError("app-server command result lacks integer exitCode")
        timed_out = result.get("timedOut", result.get("timed_out")) is True
        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "output_overflow": overflow,
            "output_sha256": _sha(stdout_raw + b"\0" + stderr_raw),
        }

    def run(
        self,
        *,
        command: str,
        cwd: str | Path,
        timeout: float,
        network_access: bool,
        writable_roots: Iterable[str | Path],
        environment: Mapping[str, str],
    ) -> dict[str, Any]:
        if not isinstance(command, str):
            raise RpcExecutionError("Executor command must be a string")
        if not 1 <= float(timeout) <= self.maximum_timeout:
            raise RpcExecutionError("Executor call timeout is outside its bound")
        if not isinstance(environment, Mapping):
            raise RpcExecutionError("Executor environment must be a mapping")
        with self._lock:
            cwd_path = self._safe_path(cwd, "command cwd", directory=True)
            params = self._command_params(
                command=command,
                cwd=cwd_path,
                timeout=float(timeout),
                network_access=network_access,
                writable_roots=writable_roots,
                environment=environment,
            )
            client: Any | None = None
            deadline = time.monotonic() + float(timeout) + 15
            try:
                client = self.process_factory(
                    [
                        self._runtime_executables["codex"],
                        "--enable",
                        "windows_sandbox_service",
                        "app-server",
                    ],
                    self.work_root,
                    self.runtime_environment,
                )
                self._processes.add(client)
                initialize_id = "stage3-init-" + uuid.uuid4().hex
                client.send(
                    {
                        "jsonrpc": "2.0",
                        "id": initialize_id,
                        "method": "initialize",
                        "params": {
                            "clientInfo": {
                                "name": "lifeos-lads-stage3",
                                "title": "Life OS LADS Stage 3 QA",
                                "version": "1.0.0",
                            },
                            "capabilities": {"experimentalApi": True},
                        },
                    }
                )
                client.receive_id(initialize_id, deadline)
                client.send(
                    {
                        "jsonrpc": "2.0",
                        "method": "initialized",
                        "params": {},
                    }
                )
                call_id = "stage3-exec-" + uuid.uuid4().hex
                client.send(
                    {
                        "jsonrpc": "2.0",
                        "id": call_id,
                        "method": "command/exec",
                        "params": params,
                    }
                )
                result = client.receive_id(call_id, deadline)
                if getattr(client, "overflow", False):
                    raise RpcExecutionError("app-server process output exceeded its bound")
                receipt = self._receipt(
                    result,
                    result.get("stdout", result.get("output", "")),
                    result.get("stderr", ""),
                )
                receipt["runtime"] = self.runtime_metadata_value
                return receipt
            except TimeoutError as exc:
                return {
                    "stdout": "",
                    "stderr": "",
                    "exit_code": None,
                    "timed_out": True,
                    "output_overflow": False,
                    "output_sha256": None,
                    "error_type": "TimeoutError",
                    "error": bounded_error_text(exc),
                    "error_evidence": self._persist_error(exc),
                }
            except (OSError, RpcExecutionError) as exc:
                return {
                    "stdout": "",
                    "stderr": "",
                    "exit_code": None,
                    "timed_out": False,
                    "output_overflow": False,
                    "output_sha256": None,
                    "error_type": type(exc).__name__,
                    "error": bounded_error_text(exc),
                    "error_evidence": self._persist_error(exc),
                }
            finally:
                if client is not None:
                    client.close()
                    self._processes.discard(client)

    def _persist_error(self, exc: BaseException) -> dict[str, Any]:
        """Keep the entire sanitized terminal diagnostic in owned private IO."""
        directory = self.work_root / "rpc-errors"
        if directory.exists() and _is_reparse(directory):
            raise RpcExecutionError("RPC diagnostic directory is unsafe")
        directory.mkdir(exist_ok=True)
        self._safe_path(directory, "diagnostic directory", directory=True)
        text = _safe_error_text(getattr(exc, "safe_detail", str(exc)))
        data = text.encode("utf-8", errors="replace")
        maximum = MAX_PROCESS_OUTPUT
        truncated = len(data) > maximum
        if truncated:
            data = data[:maximum//2] + b"\n[DIAGNOSTIC_BOUND]\n" + data[-maximum//2:]
        path = directory / (uuid.uuid4().hex + ".txt")
        with path.open("xb") as handle:
            handle.write(data)
        return {"path": path.relative_to(self.work_root).as_posix(), "sha256": _sha(data), "bytes": len(data), "truncated": truncated}

    def terminate_all(self) -> None:
        with self._lock:
            for client in list(self._processes):
                client.close()
            self._processes.clear()

