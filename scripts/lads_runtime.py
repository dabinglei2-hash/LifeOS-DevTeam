"""Standard-library execution primitives for LADS; no models or product storage.

The coordinator owns backups and execution receipts. Windows children enter an
owned kill-on-close job before their initial thread is resumed, including Python
venv launcher descendants. Output stays bounded without discarding its real end.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass


class ExecutionBoundaryError(RuntimeError):
    pass


def redact_complete(value, patterns):
    text = value if isinstance(value, str) else repr(value)
    for pattern in patterns:
        text = pattern.sub(r"\1[REDACTED]" if pattern.groups else "[REDACTED]", text)
    return text


def error_events(stdout, patterns, maximum=20):
    rows = []
    for line in stdout.splitlines():
        if not line.strip() or len(line.encode("utf-8")) > 1024 * 1024:
            continue
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(event, dict) or event.get("type") not in {"error", "turn.failed"}:
            continue
        row = {"type": event["type"]}
        details = event.get("error")
        if not isinstance(details, dict):
            details = event
        for key in ("code", "status", "message"):
            value = details.get(key)
            if type(value) in (str, int):
                row[key] = redact_complete(str(value), patterns)[:1000]
        rows.append(row)
        rows = rows[-maximum:]
    return rows


def failure_diagnostics(result, patterns, tail_limit=4000):
    stdout, stderr = str(result.stdout), str(result.stderr)
    rows = error_events(stdout, patterns)
    diagnostic = "\n".join([stderr, *(json.dumps(x, ensure_ascii=True) for x in rows)])
    limit = max(1, min(4000, tail_limit))
    code = getattr(result, "exit_code", None)
    timeout = getattr(result, "timed_out", False) is True
    safe_stdout = redact_complete(stdout, patterns)
    safe_stderr = redact_complete(stderr, patterns)
    return {
        "status": "timeout" if timeout else "completed" if type(code) is int and code == 0 else "failed" if type(code) is int else "unknown",
        "exit_code": code if type(code) is int else None,
        "timed_out": timeout,
        "bounded_output": getattr(result, "bounded_output", False) is True,
        "error_events": rows,
        "error_tail": redact_complete(diagnostic, patterns)[-limit:],
        "stdout_tail": safe_stdout[-limit:],
        "stderr_tail": safe_stderr[-limit:],
        "evidence_sha256": hashlib.sha256((safe_stderr + "\n" + safe_stdout).encode("utf-8")).hexdigest(),
    }


def classify_failure(result, diagnostics):
    """Use terminal evidence, not an incidental 'auth' word in a transcript."""
    if result.timed_out:
        return "timeout"
    if result.bounded_output:
        return "output_limit"
    rows = diagnostics.get("error_events", [])
    codes = {str(x.get(k, "")).casefold() for x in rows for k in ("code", "status")}
    text = diagnostics.get("error_tail", "")
    if codes & {"401", "403", "invalid_api_key", "authentication_error", "insufficient_permissions"} or re.search(r"(?i)\b(?:HTTP(?:/[0-9.]+)?\s+|status(?:\s+code)?\s*[:=]?\s*)(?:401|403)\b", text):
        return "authentication"
    if codes & {"429", "500", "502", "503", "504", "rate_limited", "rate_limit_exceeded", "server_error", "connection_error"} or re.search(r"(?i)\b(?:HTTP(?:/[0-9.]+)?\s+|status(?:\s+code)?\s*[:=]?\s*)(?:429|500|502|503|504)\b|connection (?:reset|refused)|stream disconnected", text):
        return "connection"
    return "execution"


class _Tail:
    def __init__(self, maximum):
        self.maximum, self.data, self.total = maximum, bytearray(), 0

    def append(self, chunk):
        self.total += len(chunk)
        self.data.extend(chunk)
        if len(self.data) > self.maximum:
            del self.data[:-self.maximum]

    def text(self):
        data = bytes(self.data)
        if self.total > self.maximum:
            # Never retain a secret suffix cut away from its redaction prefix.
            newline = data.find(b"\n")
            data = data[newline + 1:] if newline >= 0 else b""
        return data.decode("utf-8", errors="replace")


class WindowsJob:
    """Owned process job; failure never falls back to uncontained execution."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes = ctypes
        k = self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([w.LPVOID, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "QueryInformationJobObject": ([w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, w.LPVOID], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
            "Thread32First": ([w.HANDLE, w.LPVOID], w.BOOL),
            "Thread32Next": ([w.HANDLE, w.LPVOID], w.BOOL),
            "OpenThread": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "ResumeThread": ([w.HANDLE], w.DWORD),
        }
        for name, (args, result) in signatures.items():
            getattr(k, name).argtypes, getattr(k, name).restype = args, result

        class Basic(ctypes.Structure):
            _fields_ = [("per_process", ctypes.c_longlong), ("per_job", ctypes.c_longlong),
                        ("flags", w.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                        ("active_limit", w.DWORD), ("affinity", ctypes.c_size_t),
                        ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_mem", ctypes.c_size_t),
                        ("job_mem", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise ExecutionBoundaryError("CreateJobObjectW failed: " + str(ctypes.get_last_error()))
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            k.CloseHandle(self.handle)
            self.handle = None
            raise ExecutionBoundaryError("SetInformationJobObject failed: " + str(error))

    def attach_and_resume(self, process):
        c, k = self.ctypes, self.k
        if not k.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ExecutionBoundaryError("AssignProcessToJobObject failed: " + str(c.get_last_error()))
        from ctypes import wintypes as w
        class Thread(c.Structure):
            _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("tid", w.DWORD),
                        ("pid", w.DWORD), ("base", w.LONG), ("delta", w.LONG), ("flags", w.DWORD)]
        snapshot = k.CreateToolhelp32Snapshot(4, 0)
        if not snapshot or int(snapshot) == c.c_void_p(-1).value:
            raise ExecutionBoundaryError("Thread snapshot failed")
        tids = []
        try:
            row = Thread()
            row.size = c.sizeof(row)
            active = k.Thread32First(snapshot, c.byref(row))
            while active:
                if row.pid == process.pid:
                    tids.append(row.tid)
                active = k.Thread32Next(snapshot, c.byref(row))
        finally:
            k.CloseHandle(snapshot)
        if len(tids) != 1:
            raise ExecutionBoundaryError("Suspended process has no unique initial thread")
        thread = k.OpenThread(2, False, tids[0])
        if not thread:
            raise ExecutionBoundaryError("OpenThread failed")
        try:
            if k.ResumeThread(thread) != 1:
                raise ExecutionBoundaryError("Initial thread was not resumed exactly once")
        finally:
            k.CloseHandle(thread)

    def close(self):
        if not self.handle:
            return
        c, k = self.ctypes, self.k
        class Accounting(c.Structure):
            _fields_ = [(name, c.c_longlong) for name in ("user", "kernel", "period_user", "period_kernel")]
            _fields_ += [(name, c.c_ulong) for name in ("faults", "total", "active", "terminated")]
        failed = None
        try:
            if not k.TerminateJobObject(self.handle, 1):
                failed = "TerminateJobObject failed: " + str(c.get_last_error())
            deadline = time.monotonic() + 5
            while failed is None:
                row = Accounting()
                if not k.QueryInformationJobObject(self.handle, 1, c.byref(row), c.sizeof(row), None):
                    failed = "Job accounting query failed"
                    break
                if row.active == 0:
                    break
                if time.monotonic() >= deadline:
                    failed = "Owned job still has active processes"
                    break
                time.sleep(0.02)
        finally:
            if not k.CloseHandle(self.handle) and failed is None:
                failed = "Job CloseHandle failed"
            self.handle = None
        if failed:
            raise ExecutionBoundaryError(failed)


@dataclass
class ProcessResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    bounded_output: bool


def run_process(argv, cwd, prompt, timeout, environment=None, *, maximum=8 * 1024 * 1024):
    if timeout <= 0 or maximum < 128:
        raise ValueError("Invalid process bounds")
    env = dict(os.environ if environment is None else environment)
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    job = WindowsJob() if os.name == "nt" else None
    process, readers = None, []
    buffers = {name: _Tail(maximum) for name in ("stdout", "stderr")}
    try:
        process = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=os.name != "nt",
                                   creationflags=0x08000004 if os.name == "nt" else 0)
        if job:
            try:
                job.attach_and_resume(process)
            except Exception:
                process.kill()
                process.wait(timeout=10)
                raise
        def drain(stream, buffer):
            while chunk := stream.read(65536):
                buffer.append(chunk)
        readers = [threading.Thread(target=drain, args=(getattr(process, name), buffer), daemon=True)
                   for name, buffer in buffers.items()]
        for reader in readers:
            reader.start()
        try:
            process.stdin.write(prompt.encode("utf-8"))
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if not job:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if job:
            job.close()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=10)
        for reader in readers:
            reader.join(timeout=5)
        if any(reader.is_alive() for reader in readers):
            raise ExecutionBoundaryError("Owned process pipe reader did not finish")
        return ProcessResult(process.returncode, buffers["stdout"].text(), buffers["stderr"].text(),
                             timed_out, any(b.total > maximum for b in buffers.values()))
    finally:
        if job:
            job.close()
        if process:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
