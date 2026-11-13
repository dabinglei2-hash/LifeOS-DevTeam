#!/usr/bin/env python3
"""Bounded LADS coordinator for independent DeepSeek worker processes.

The coordinator owns snapshots, state, scope checks, and evidence collection.
Workers are separate ``codex exec`` processes driven only through explicit
DeepSeek profiles. No rollout files, GPT fallback, native children, or automatic
source integration are used. Python 3.11+ and standard library only.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import difflib
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "scripts" / "lifeos-deepseek.py"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
STATES = (
    "CREATED",
    "SCANNING",
    "PLAN_REVIEW",
    "BUILDING",
    "QA",
    "FINAL_REVIEW",
    "AWAITING_MAIN_ACCEPTANCE",
)
STATUSES = {
    "scanner": {"SCAN_COMPLETE", "BLOCKED"},
    "reviewer": {"APPROVED", "CHANGES_REQUESTED", "BLOCKED"},
    "builder": {"BUILT", "BLOCKED"},
    "qa-tester": {"PASS", "FAIL", "N/A", "BLOCKED"},
}
MAX_BUILDS = 4
MAX_READONLY_RETRIES = 1
MAX_CAPTURE = 8 * 1024 * 1024
MAX_LINE = 1024 * 1024
PREFLIGHT_PROOF_FIELDS = {
    "role",
    "home_mode",
    "debug_argv",
    "worker_prefix_length",
    "worker_prefix_sha256",
    "exit_code",
    "stdout_sha256",
    "permissions",
    "config_sha256",
    "runtime_home",
    "home_sha256",
    "authority_sha256",
}
PROTECTED_PATHS = (
    ".codex/",
    ".agents/",
    "AGENTS.md",
    ".gitignore",
    ".gitattributes",
    "scripts/lads_orchestrator.py",
    "scripts/lifeos-deepseek.py",
    "schemas/lads-task-report.schema.json",
    "tests/lads/",
    "docs/LADS_ORCHESTRATOR.md",
    "docs/LADS-STAGE1-REPORT.md",
)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"(?i)\b(DEEPSEEK_[A-Z0-9_]*KEY\s*[=:]\s*)\S+"),
    re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
)
TRANSIENT = re.compile(
    r"(?i)\b(429|rate limit|temporarily unavailable|connection reset|ECONNRESET|HTTP 50[234])\b"
)
CHILD_ENVIRONMENT_ALLOWLIST = frozenset(
    name.casefold()
    for name in (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "CODEX_HOME",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    )
)


class Blocked(RuntimeError):
    """A fail-closed coordinator condition."""

    def __init__(self, message, *, command=(), exit_code=None, output_tail="", output_sha256=None):
        super().__init__(message)
        self.command = list(command)
        self.exit_code = exit_code
        self.output_tail = output_tail
        self.output_sha256 = output_sha256


class TransientWorkerError(RuntimeError):
    """A narrowly classified read-only transient worker failure."""


def now():
    return datetime.now(timezone.utc).isoformat()


def require(value, message):
    if not value:
        raise Blocked(message)


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def bounded(text, limit=12000):
    value = text if isinstance(text, str) else repr(text)
    return value if len(value) <= limit else value[:limit] + "\n...[bounded]"


def redact(text):
    value = bounded(text, MAX_CAPTURE)
    for pattern in SECRET_PATTERNS:
        if pattern.groups:
            value = pattern.sub(r"\1[REDACTED]", value)
        else:
            value = pattern.sub("[REDACTED]", value)
    return bounded(value)


def redacted_tail(text, limit=4000):
    """Return the true tail after the complete redaction pipeline."""
    safe = redact(text)
    return safe[-limit:] if limit > 0 else ""


def secretlike(text):
    value = text if isinstance(text, str) else repr(text)
    return any(pattern.search(value) for pattern in SECRET_PATTERNS)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def append_event(path, event):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {key: redact(item) if isinstance(item, str) else item for key, item in event.items()}
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def is_reparse(path):
    path = Path(path)
    try:
        info = path.lstat()
    except OSError:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def reject_reparse_chain(root, relative, *, allow_missing=False):
    current = Path(root)
    for part in PurePosixPath(relative).parts:
        current = current / part
        if allow_missing:
            try:
                current.lstat()
            except FileNotFoundError:
                continue
        require(not is_reparse(current), f"reparse/symlink path refused: {relative}")


def path_within(path, parent):
    path_value = os.path.normcase(os.path.abspath(str(Path(path).resolve(strict=False))))
    parent_value = os.path.normcase(os.path.abspath(str(Path(parent).resolve(strict=False))))
    try:
        return os.path.commonpath((path_value, parent_value)) == parent_value
    except ValueError:
        return False


def reject_reparse_tree(root):
    root = Path(root)
    require(root.is_dir(), f"managed directory missing: {root.name}")
    require(not is_reparse(root), f"managed directory is a reparse/symlink: {root.name}")
    for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in sorted(dirs):
            path = current_path / name
            require(not is_reparse(path), f"managed tree contains a reparse/symlink directory: {name}")
        for name in sorted(names):
            path = current_path / name
            require(not is_reparse(path), f"managed tree contains a reparse/symlink file: {name}")
            require(path.is_file(), f"managed tree contains a non-regular file: {name}")


def validate_run_root(repo, run_root):
    repo = Path(repo).resolve()
    run_root = Path(run_root)
    require(run_root.is_absolute(), "run root must be absolute")
    require(UUID_RE.fullmatch(run_root.name), "run root is not a fresh task UUID")
    require(run_root.is_dir(), "run root missing")
    expected_parent = repo / "work" / "lads" / "runs"
    require(path_within(run_root, repo), "run root escapes repository")
    require(os.path.normcase(str(run_root.resolve())) == os.path.normcase(str(run_root)),
            "run root resolved through an unsafe parent")
    require(os.path.normcase(str(run_root.resolve().parent)) == os.path.normcase(str(expected_parent.resolve())),
            "run root is not the expected UUID child")
    reject_reparse_chain(repo, f"work/lads/runs/{run_root.name}")
    return run_root


def managed_path(run_root, relative, *, directory=False):
    relative = safe_relative(relative)
    run_root = Path(run_root)
    require(run_root.is_dir(), "managed run root missing")
    require(not is_reparse(run_root), "managed run root is a reparse/symlink")
    target = run_root / Path(relative)
    require(path_within(target, run_root), "managed path escapes run root")
    reject_reparse_chain(run_root, relative, allow_missing=True)
    if directory:
        target.mkdir(parents=True, exist_ok=True)
        reject_reparse_chain(run_root, relative)
    return target


def credential_path(value):
    name = PurePosixPath(value).name.casefold()
    if name in {"auth.json", "credentials.json", "secrets.json"}:
        return True
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    if name.endswith((".pem", ".key", ".p12", ".pfx")):
        return True
    return any(word in name for word in ("private-key", "private_key", "credential", "password", "secret"))


def safe_relative(value):
    require(isinstance(value, str) and value, "allowed paths must be non-empty strings")
    require("\\" not in value and "\x00" not in value, "allowed paths must use POSIX separators")
    relative = PurePosixPath(value)
    require(not relative.is_absolute(), "absolute allowed path")
    require(":" not in relative.parts[0], "drive-qualified allowed path")
    require(all(part not in {"", ".", ".."} for part in relative.parts), "path traversal in allowed path")
    require(all(part.casefold() != ".git" for part in relative.parts), ".git is never an allowed path")
    require(not credential_path(value), f"credential-like path refused: {value}")
    return relative.as_posix()


def validate_allowed(paths):
    require(isinstance(paths, list) and paths, "allowed_paths must be a non-empty list")
    normalized = [safe_relative(item) for item in paths]
    folded = [item.casefold() for item in normalized]
    require(len(folded) == len(set(folded)), "allowed_paths contains a case-colliding duplicate")
    for item in normalized:
        require(not any(item == p.rstrip("/") or item.startswith(p) for p in PROTECTED_PATHS),
                f"engineering/policy path is protected from normal tasks: {item}")
    return normalized


def validate_test_command(command):
    require(isinstance(command, str) and command.strip(), "test command must be non-empty")
    require(not re.search(r"[\r\n;&|><`]", command) and "$(" not in command,
            "test command must be a single stdout-only command")
    require(re.match(r"^(?:python3?|py\s+-3)\s+-B(?:\s|$)", command.strip()),
            "test command must invoke Python with -B first")
    normalized = " ".join(command.strip().split())
    require("--" not in normalized and not normalized.startswith(("python -B -c", "python3 -B -c")),
            "inline Python test commands are not allowed")
    require(not secretlike(normalized), "secret-like test command refused")
    return normalized


def git(repo, *args, check=True):
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if check and result.returncode != 0:
        raise Blocked(f"git {args[0]} failed: {bounded(result.stderr, 1000)}")
    return result


def git_head(repo):
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def git_status(repo):
    value = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    return sha_bytes(value.encode("utf-8"))


def require_ignored_work(repo):
    result = git(repo, "check-ignore", "-q", "work/lads/.state-probe", check=False)
    require(result.returncode == 0, "repo must ignore work/lads for coordinator state")


def source_paths(repo):
    raw = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
    paths = sorted({os.fsdecode(item) for item in raw.encode("utf-8").split(b"\0") if item})
    require(paths, "source snapshot is empty")
    folded = [safe_relative(path).casefold() for path in paths]
    require(len(folded) == len(set(folded)), "source contains case-colliding paths")
    return paths


@dataclass(frozen=True)
class Snapshot:
    head: str
    files: dict[str, str]
    digest: str


def snapshot_digest(head, files):
    return sha_bytes(canonical({"head": head, "files": files}))


def capture_source(repo, destination):
    repo = Path(repo).resolve()
    head = git_head(repo)
    baseline = git_status(repo)
    paths = source_paths(repo)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    files = {}
    for relative in paths:
        source = repo / Path(relative)
        reject_reparse_chain(repo, relative)
        require(source.is_file() and not is_reparse(source), f"unsafe source entry: {relative}")
        target = destination / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        source_hash, target_hash = sha_file(source), sha_file(target)
        require(source_hash == target_hash, f"source changed while snapshotting: {relative}")
        files[relative] = target_hash
    require(git_head(repo) == head and git_status(repo) == baseline,
            "source repository changed while snapshotting")
    return Snapshot(head, files, snapshot_digest(head, files))


def inventory_tree(root):
    root = Path(root).resolve()
    require(root.is_dir(), f"inventory root is not a directory: {root}")
    files = {}
    for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        kept = []
        for name in sorted(dirs):
            path = current_path / name
            if path == root / ".git":
                continue
            require(not is_reparse(path), f"reparse/symlink directory refused: {path}")
            kept.append(name)
        dirs[:] = kept
        for name in sorted(names):
            path = current_path / name
            if current_path == root and name == ".git":
                continue
            relative = path.relative_to(root).as_posix()
            safe_relative(relative)
            require(not is_reparse(path), f"reparse/symlink file refused: {relative}")
            require(path.is_file(), f"non-regular file refused: {relative}")
            files[relative] = sha_file(path)
    folded = [path.casefold() for path in files]
    require(len(folded) == len(set(folded)), "candidate contains case-colliding paths")
    return files


def copy_tree_no_git(source, destination):
    source, destination = Path(source), Path(destination)
    inventory_tree(source)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git"))
    before = inventory_tree(source)
    after = inventory_tree(destination)
    require(before == after, "snapshot copy changed bytes")
    return after


def changed_files(before, after):
    changes = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old != new:
            kind = "added" if old is None else "deleted" if new is None else "modified"
            changes.append({"path": path, "change": kind, "before_sha256": old, "after_sha256": new})
    return changes


def text_diff(source, candidate, paths):
    chunks = []
    for relative in paths:
        left_path, right_path = Path(source) / Path(relative), Path(candidate) / Path(relative)
        try:
            left = left_path.read_text(encoding="utf-8") if left_path.exists() else ""
            right = right_path.read_text(encoding="utf-8") if right_path.exists() else ""
        except (UnicodeDecodeError, OSError):
            chunks.append(f"Binary or unreadable change: {relative}\n")
            continue
        chunks.extend(
            difflib.unified_diff(
                left.splitlines(keepends=True),
                right.splitlines(keepends=True),
                fromfile="a/" + relative,
                tofile="b/" + relative,
            )
        )
    return bounded(redact("".join(chunks)), 100000)


@dataclass(frozen=True)
class Manifest:
    repo: Path
    task: str
    allowed_paths: tuple[str, ...]
    test_commands: tuple[str, ...]
    applicability: dict
    raw_sha256: str
    demo: bool = False


def load_manifest(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise Blocked(f"invalid manifest JSON: {type(error).__name__}") from error
    require(isinstance(data, dict), "manifest must be an object")
    required = {"repo", "task", "allowed_paths", "test_commands", "applicability"}
    require(set(data) == required, "manifest must contain exactly repo/task/allowed_paths/test_commands/applicability")
    repo = Path(data["repo"])
    require(repo.is_absolute(), "manifest repo must be absolute")
    repo = repo.resolve()
    require(repo.is_dir(), "manifest repo missing")
    top = Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    require(top == repo, "manifest repo must be the exact Git top level")
    task = data["task"]
    require(isinstance(task, str) and task.strip() and not secretlike(task), "task text missing or secret-like")
    allowed = validate_allowed(data["allowed_paths"])
    commands = [validate_test_command(item) for item in data["test_commands"]] if isinstance(data["test_commands"], list) else []
    require(commands, "at least one prescribed Python -B test command is required")
    require(len(commands) == len(set(commands)), "test commands must be unique")
    applicability = data["applicability"]
    require(isinstance(applicability, dict) and set(applicability) == {"browser", "multiuser"},
            "applicability must contain browser and multiuser")
    require(all(isinstance(value, bool) for value in applicability.values()),
            "applicability values must be booleans")
    return Manifest(repo, task.strip(), tuple(allowed), tuple(commands), applicability, sha_bytes(raw))


class RepoLock:
    def __init__(self, repo, task_text):
        key = sha_bytes(canonical({"repo": str(Path(repo).resolve()), "task": sha_bytes(task_text.encode("utf-8"))}))
        self.path = Path(repo) / "work" / "lads" / "locks" / (key + ".lock")
        self.token = uuid.uuid4().hex
        self.held = False

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise Blocked("repo/task lock already held") from error
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "token": self.token, "created_at": now()}))
            stream.flush()
            os.fsync(stream.fileno())
        self.held = True
        return self

    def release(self):
        if not self.held:
            return
        try:
            data = json.loads(self.path.read_text(encoding="ascii"))
            if data.get("token") == self.token:
                self.path.unlink()
        finally:
            self.held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()


@dataclass
class ProcessResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    bounded_output: bool


def windows_process_tree(root_pid):
    """Return descendant PIDs without relying on taskkill permissions."""
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
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
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid = ctypes.c_void_p(-1).value
    if not snapshot or int(snapshot) == invalid:
        return set()
    relations = {}
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(ProcessEntry)
    try:
        available = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while available:
            relations[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            available = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    descendants, frontier = set(), {int(root_pid)}
    while frontier:
        children = {pid for pid, parent in relations.items() if parent in frontier and pid not in descendants}
        descendants.update(children)
        frontier = children
    return descendants


def terminate_windows_tree(root_pid):
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    for pid in windows_process_tree(root_pid):
        handle = kernel32.OpenProcess(0x0001 | 0x00100000, False, pid)
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.WaitForSingleObject(handle, 5000)
            kernel32.CloseHandle(handle)


def kill_tree(process):
    if os.name == "nt":
        terminate_windows_tree(process.pid)
    elif process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.kill()
    except ProcessLookupError:
        pass


def run_process(argv, cwd, prompt, timeout, environment=None):
    kwargs = {
        "cwd": str(cwd),
        "env": os.environ.copy() if environment is None else environment,
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "start_new_session": os.name != "nt",
    }
    process = subprocess.Popen(argv, **kwargs)
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    flags = {"bounded": False}

    def drain(stream, name):
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            room = MAX_CAPTURE - len(buffers[name])
            if room > 0:
                buffers[name].extend(chunk[:room])
            if len(chunk) > room:
                flags["bounded"] = True

    readers = [
        threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True),
    ]
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
        kill_tree(process)
        process.wait(timeout=15)
    if os.name == "nt":
        terminate_windows_tree(process.pid)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for reader in readers:
        reader.join(timeout=15)
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()
    return ProcessResult(
        process.returncode,
        buffers["stdout"].decode("utf-8", errors="replace"),
        buffers["stderr"].decode("utf-8", errors="replace"),
        timed_out,
        flags["bounded"],
    )


CHILD_CONTROL_KEYS = frozenset({
    "child_id",
    "new_agent_id",
    "new_thread_id",
    "agent_id",
})
CONTROL_TYPE_KEYS = frozenset({
    "type",
    "kind",
    "event",
    "event_type",
    "item_type",
    "call_type",
    "activity_type",
    "control_type",
})
CONTROL_NAME_KEYS = frozenset({
    "name",
    "tool",
    "tool_name",
    "function",
    "function_name",
})
NATIVE_CONTROL_TAGS = frozenset({
    "agent" + "_" + "spawn",
    "agent" + "_" + "spawned",
    "collab" + "_" + "tool",
    "collab" + "_" + "tool" + "_call",
    "collab" + "_" + "agent" + "_" + "tool" + "_" + "call",
    "collaboration_tool",
    "collaboration_tool_call",
    "collabAgentToolCall",
    "collabToolCall",
    "sub_agent_activity",
    "subAgentActivity",
})
NATIVE_TOOL_NAMES = frozenset({
    "spawn_agent",
    "send_input",
    "wait",
    "close_agent",
    "wait_agent",
    "resume_agent",
    "list_agents",
})


def _normalized_control_name(value):
    if not isinstance(value, str):
        return ""
    return "".join(character for character in value.casefold() if character.isalnum())


NATIVE_CONTROL_TAG_NAMES = frozenset(
    _normalized_control_name(value) for value in NATIVE_CONTROL_TAGS
)
NATIVE_TOOL_NAME_NAMES = frozenset(
    _normalized_control_name(value) for value in NATIVE_TOOL_NAMES
)


def _is_native_control_tag(value):
    normalized = _normalized_control_name(value)
    if normalized in NATIVE_CONTROL_TAG_NAMES:
        return True
    collaboration_tool = (
        normalized.startswith("collab")
        and "tool" in normalized
        and ("call" in normalized or normalized.endswith("tool"))
    )
    subagent_lifecycle = (
        normalized.startswith("subagent")
        and normalized.endswith(("activity", "started", "completed", "failed", "error"))
    )
    return (
        normalized.startswith("agent" + "spawn")
        or collaboration_tool
        or subagent_lifecycle
    )


def has_native_control(value):
    """Reject typed native control surfaces without scanning ordinary data."""
    if isinstance(value, dict):
        for key, item in value.items():
            folded_key = key.casefold()
            if folded_key in CHILD_CONTROL_KEYS:
                return True
            if (folded_key in CONTROL_TYPE_KEYS
                    and _is_native_control_tag(item)):
                return True
            if (folded_key in CONTROL_NAME_KEYS
                    and _normalized_control_name(item) in NATIVE_TOOL_NAME_NAMES):
                return True
            if has_native_control(item):
                return True
    elif isinstance(value, list):
        return any(has_native_control(item) for item in value)
    return False


def parse_event_stream(stdout):
    events, commands, messages = [], [], []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        require(len(line.encode("utf-8")) <= MAX_LINE, "worker stdout line exceeded the capture bound")
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise Blocked("worker stdout contained a non-JSON event") from error
        require(isinstance(event, dict), "worker event must be an object")
        require(not has_native_control(event), "native child/agent event refused")
        events.append(event)
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and isinstance(item, dict):
            if item.get("type") == "agent_message":
                messages.append(item.get("text"))
            if item.get("type") == "command_execution":
                commands.append({
                    "command": item.get("command", ""),
                    "status": item.get("status", ""),
                    "exit_code": item.get("exit_code"),
                })
    completed = [event for event in events if event.get("type") == "turn.completed"]
    require(len(completed) == 1, "exactly one turn.completed is required")
    require(events and events[-1].get("type") == "turn.completed",
            "turn.completed must be the terminal event")
    require(not any(event.get("type") in {"turn.failed", "error"} for event in events),
            "worker event stream contains failure evidence")
    require(messages and isinstance(messages[-1], str), "last assistant JSON report missing")
    try:
        report = json.loads(messages[-1])
    except json.JSONDecodeError as error:
        raise Blocked("last assistant message is not pure JSON") from error
    return report, commands, completed[0].get("usage") or {}


def command_body(value):
    normalized = " ".join(str(value).strip().split())
    match = re.match(r"(?is)^.*?\s(?:-command|-lc|/c)\s+(?:\"([^\"]*)\"|'([^']*)'|(.+))$", normalized)
    if match:
        normalized = next(item for item in match.groups() if item is not None)
    return " ".join(normalized.strip().strip("\"'").split())


def validate_report(report, task_id, role, source_digest=None, test_commands=(), command_events=()):
    require(isinstance(report, dict), "worker report must be an object")
    allowed = {"task_id", "role", "status", "summary", "evidence", "source_snapshot_sha256"}
    require(set(report) <= allowed, "worker report has unknown fields")
    require({"task_id", "role", "status", "summary", "evidence"} <= set(report), "worker report missing fields")
    require(report["task_id"] == task_id and UUID_RE.fullmatch(task_id), "foreign or invalid task_id")
    require(report["role"] == role, "worker role mismatch")
    require(report["status"] in STATUSES[role], "status is not allowed for role")
    require(isinstance(report["summary"], str) and 0 < len(report["summary"]) <= 4000, "invalid summary")
    evidence = report["evidence"]
    require(isinstance(evidence, list) and 1 <= len(evidence) <= 50, "invalid evidence list")
    require(all(isinstance(item, str) and 0 < len(item) <= 1000 for item in evidence), "invalid evidence item")
    require(not secretlike(json.dumps(report, ensure_ascii=False)), "secret-like worker report refused")
    given_digest = report.get("source_snapshot_sha256")
    if given_digest is not None:
        require(isinstance(given_digest, str) and SHA_RE.fullmatch(given_digest), "invalid source snapshot digest")
    if role == "qa-tester":
        require(given_digest is not None and given_digest == source_digest, "QA source snapshot digest mismatch")
        require(report["status"] != "N/A" or not test_commands, "N/A is invalid when test commands are prescribed")
        require(len(command_events) == len(test_commands),
                "QA may execute only the prescribed test commands")
        for command in test_commands:
            matching = [
                item for item in command_events
                if command_body(item.get("command", "")) == command
            ]
            require(len(matching) == 1, f"QA did not execute exactly one prescribed command: {command}")
            require(any(command in " ".join(item.split()) for item in evidence),
                    f"QA evidence omitted command: {command}")
            terminal = matching[0]
            status = terminal.get("status")
            exit_code = terminal.get("exit_code")
            require(status in {"completed", "failed"},
                    f"QA command lacks a terminal status: {command}")
            require(type(exit_code) is int, f"QA command lacks an integer exit code: {command}")
            require(not (status == "failed" and exit_code == 0),
                    f"QA failed status contradicts exit 0: {command}")
        if report["status"] == "PASS":
            require(all(item.get("status") == "completed" and item.get("exit_code") == 0
                        for item in command_events),
                    "QA PASS requires every prescribed command completed with exit 0")
        elif report["status"] == "FAIL":
            require(any(item.get("exit_code") != 0 for item in command_events),
                    "QA FAIL lacks non-zero command evidence")
    else:
        require(given_digest is None, "source snapshot digest is QA-only")
    return report


def report_output_schema(role):
    require(role in STATUSES, "unknown worker role for output schema")
    properties = {
        "task_id": {"type": "string"},
        "role": {"type": "string", "enum": [role]},
        "status": {"type": "string", "enum": sorted(STATUSES[role])},
        "summary": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
    }
    if role == "qa-tester":
        properties["source_snapshot_sha256"] = {"type": "string"}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


@dataclass
class WorkerResult:
    role: str
    report: dict
    commands: list
    metadata: dict
    output_sha256: str
    command: list | None = None
    exit_code: int | None = None
    output_tail: str = ""


def sanitized_command(argv):
    sanitized = []
    for item in argv:
        text = str(item)
        if text.startswith("developer_instructions="):
            sanitized.append("developer_instructions=[REDACTED]")
        else:
            sanitized.append(redact(text))
    return sanitized


def sanitized_commands(commands, *, exact_commands=()):
    values = []
    exact = tuple(exact_commands or ())
    for item in list(commands or [])[:50]:
        if not isinstance(item, dict):
            continue
        raw_command = str(item.get("command", ""))
        body = command_body(raw_command)
        canonical = next((command for command in exact if body == command), None)
        values.append({
            "command": canonical if canonical is not None else redact(bounded(raw_command, 4000)),
            "status": redact(bounded(str(item.get("status", "")), 100)),
            "exit_code": item.get("exit_code"),
        })
    return values


def sanitized_report(report, *, exact_commands=()):
    """Return a bounded, redacted report while preserving validated QA commands."""
    value = {}
    for field in ("task_id", "role", "status", "summary", "evidence", "source_snapshot_sha256"):
        if field not in report:
            continue
        item = report[field]
        if field == "summary":
            value[field] = redact(bounded(item, 4000))
        elif field == "evidence":
            evidence = []
            for entry in item:
                normalized = " ".join(entry.split())
                matches = [command for command in exact_commands if command in normalized]
                if matches:
                    for command in matches:
                        if command not in evidence:
                            evidence.append(command)
                elif exact_commands:
                    marker = "[QA evidence omitted from control-plane data]"
                    if marker not in evidence:
                        evidence.append(marker)
                else:
                    evidence.append(redact(bounded(entry, 1000)))
            value[field] = evidence
        else:
            value[field] = item
    return value


def extract_permission_block(stdout, role):
    try:
        prompt = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise Blocked(f"{role} permission prompt is not one valid JSON document") from error
    require(isinstance(prompt, list), f"{role} permission prompt must be a JSON list")
    developer_texts = []
    for message in prompt:
        require(isinstance(message, dict), f"{role} permission prompt contains a non-object message")
        require(message.get("type") == "message", f"{role} permission prompt message type mismatch")
        message_role = message.get("role")
        require(message_role in {"developer", "user"}, f"{role} permission prompt role is invalid")
        content = message.get("content")
        require(isinstance(content, list), f"{role} permission prompt content must be a list")
        for item in content:
            require(isinstance(item, dict), f"{role} permission prompt contains non-object content")
            require(item.get("type") in {"input_text", "text"},
                    f"{role} permission prompt content type mismatch")
            text = item.get("text")
            require(isinstance(text, str), f"{role} permission prompt text must be a string")
            if message_role == "developer":
                developer_texts.append(text)

    candidates = []
    for text in developer_texts:
        opening = len(re.findall(r"<permissions instructions>", text, flags=re.IGNORECASE))
        closing = len(re.findall(r"</permissions instructions>", text, flags=re.IGNORECASE))
        if not opening and not closing:
            continue
        require(opening == 1 and closing == 1,
                f"{role} permission block is missing, split, or ambiguous")
        match = re.search(
            r"<permissions instructions>\s*(.*?)\s*</permissions instructions>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        require(match is not None, f"{role} permission block is malformed")
        candidates.append(match.group(1))
    require(len(candidates) == 1, f"{role} permission block is missing or ambiguous")
    return candidates[0]


def verify_permission_block(content, role, workspace):
    mode = "workspace-write" if role == "builder" else "read-only"
    declared_modes = re.findall(r"(?i)`sandbox_mode`\s+is\s+`([^`\r\n]+)`", content)
    require(len(declared_modes) == 1 and declared_modes[0] == mode,
            f"{role} effective sandbox mismatch")
    network_values = re.findall(r"(?i)\bnetwork access is\s+([^.\r\n]+)", content)
    require(len(network_values) == 1 and network_values[0].strip().casefold() in {"restricted", "disabled"},
            f"{role} effective network policy is not restrictive")
    approval_values = re.findall(r"(?i)\bapproval policy is currently\s+([^.\r\n]+)", content)
    require(len(approval_values) == 1 and approval_values[0].strip().casefold() == "never",
            f"{role} effective approval policy is not never")
    if role == "builder":
        root_mentions = re.findall(r"(?i)\bwritable roots?\b", content)
        roots = re.findall(
            r"(?im)^\s*The writable root is\s+`([^`\r\n]+)`\s*\.?\s*$",
            content,
        )
        require(len(roots) == 1 and len(root_mentions) == 1,
                "Builder effective writable root is missing or ambiguous")
        actual_root = Path(roots[0].strip()).resolve(strict=False)
        expected_root = Path(workspace).resolve(strict=False)
        require(os.path.normcase(str(actual_root)) == os.path.normcase(str(expected_root)),
                "Builder effective writable root is broader or different than workspace")
    else:
        require(not re.search(r"(?i)\bwritable roots?\b", content),
                f"{role} unexpectedly has a writable root")
        require(re.search(r"(?i)only permits reading files", content),
                f"{role} effective sandbox does not state read-only access")
    return {
        "role": role,
        "sandbox_mode": mode,
        "network": "restricted",
        "approval": "never",
        "writable_roots": [str(Path(workspace).resolve())] if role == "builder" else [],
    }


def verify_permission_probe(stdout, role, workspace):
    return verify_permission_block(extract_permission_block(stdout, role), role, workspace)


def validate_preflight_proof(proof, role):
    require(isinstance(proof, dict) and set(proof) == PREFLIGHT_PROOF_FIELDS,
            f"{role} successful preflight proof is missing or malformed")
    require(proof["role"] == role, f"{role} preflight proof role mismatch")
    require(proof["home_mode"] in {"shared", "frozen"},
            f"{role} preflight home mode is invalid")
    argv = proof["debug_argv"]
    require(isinstance(argv, list) and 3 <= len(argv) <= 256, f"{role} preflight argv is invalid")
    require(all(isinstance(item, str) and len(item) <= 4000 for item in argv),
            f"{role} preflight argv contains invalid items")
    require(argv[-3:] == ["debug", "prompt-input", "LADS effective permission preflight"],
            f"{role} preflight argv lacks the debug proof command")
    prefix_length = proof["worker_prefix_length"]
    require(isinstance(prefix_length, int) and 1 <= prefix_length <= len(argv),
            f"{role} preflight worker prefix length is invalid")
    prefix = argv[:prefix_length]
    require(proof["worker_prefix_sha256"] == sha_bytes(canonical(prefix)),
            f"{role} preflight worker prefix proof mismatch")
    require(proof["exit_code"] == 0, f"{role} preflight proof exit code is not zero")
    require(isinstance(proof["stdout_sha256"], str) and SHA_RE.fullmatch(proof["stdout_sha256"]),
            f"{role} preflight stdout hash is invalid")
    permissions = proof["permissions"]
    require(isinstance(permissions, dict)
            and set(permissions) == {"role", "sandbox_mode", "network", "approval", "writable_roots"},
            f"{role} preflight permissions proof is malformed")
    expected_mode = "workspace-write" if role == "builder" else "read-only"
    require(permissions["role"] == role and permissions["sandbox_mode"] == expected_mode,
            f"{role} preflight sandbox proof mismatch")
    require(permissions["network"] == "restricted" and permissions["approval"] == "never",
            f"{role} preflight restrictive policy proof missing")
    roots = permissions["writable_roots"]
    require(isinstance(roots, list) and all(isinstance(item, str) and item for item in roots),
            f"{role} preflight writable root proof is malformed")
    if role == "builder":
        require(len(roots) == 1 and Path(roots[0]).is_absolute(),
                "Builder preflight proof lacks one exact writable root")
    else:
        require(not roots, f"{role} preflight proof unexpectedly has a writable root")
    config_sha256 = proof["config_sha256"]
    require(isinstance(config_sha256, dict) and config_sha256,
            f"{role} preflight config hashes are missing")
    require(all(isinstance(name, str) and 0 < len(name) <= 1000
                and isinstance(digest, str) and SHA_RE.fullmatch(digest)
                for name, digest in config_sha256.items()),
            f"{role} preflight config hashes are malformed")
    runtime_home = proof["runtime_home"]
    require(isinstance(runtime_home, str) and runtime_home
            and Path(runtime_home).is_absolute(),
            f"{role} preflight runtime home proof is malformed")
    require(isinstance(proof["home_sha256"], str) and SHA_RE.fullmatch(proof["home_sha256"]),
            f"{role} preflight runtime home hash is invalid")
    require(isinstance(proof["authority_sha256"], str) and SHA_RE.fullmatch(proof["authority_sha256"]),
            f"{role} preflight authority hash is invalid")
    require(not secretlike(json.dumps(proof, ensure_ascii=False)), "secret-like preflight proof refused")
    return proof


def runtime_home_sha256(runtime_home, role, paths, *, home_mode):
    require(home_mode in {"shared", "frozen"}, "runtime proof mode is invalid")
    runtime_home = Path(runtime_home).resolve(strict=True)
    files = {}
    for path in paths:
        path = Path(path).resolve(strict=True)
        require(path_within(path, runtime_home), "runtime proof file escapes the authorized home")
        require(
            home_mode == "frozen" or path.name != "config.toml",
            "shared proof must not read the mutable main Codex config",
        )
        files[path.relative_to(runtime_home).as_posix()] = sha_file(path)
    require(files, "runtime proof files are missing")
    return sha_bytes(canonical({
        "role": role,
        "home_mode": home_mode,
        "home": str(runtime_home),
        "files": files,
    }))


def successful_preflight_proof(
    role,
    worker_argv,
    probe_argv,
    probe,
    permissions,
    config_snapshots,
    runtime_home,
    authority_sha256,
    *,
    home_mode,
    home_proof_paths,
):
    safe_worker = sanitized_command(worker_argv)
    safe_probe = sanitized_command(probe_argv)
    require(safe_probe[:len(safe_worker)] == safe_worker,
            f"{role} preflight command is not the exact worker prefix")
    proof = {
        "role": role,
        "home_mode": home_mode,
        "debug_argv": safe_probe,
        "worker_prefix_length": len(safe_worker),
        "worker_prefix_sha256": sha_bytes(canonical(safe_worker)),
        "exit_code": probe.exit_code,
        "stdout_sha256": sha_bytes(probe.stdout.encode("utf-8")),
        "permissions": permissions,
        "config_sha256": {
            redact(bounded(str(name), 1000)): digest
            for name, digest in sorted(config_snapshots.items())
        },
        "runtime_home": str(Path(runtime_home).resolve(strict=True)),
        "home_sha256": runtime_home_sha256(
            runtime_home,
            role,
            home_proof_paths,
            home_mode=home_mode,
        ),
        "authority_sha256": authority_sha256,
    }
    return validate_preflight_proof(proof, role)


def configuration_drift(expected, actual):
    labels = []
    for name in sorted(set(expected) | set(actual)):
        if expected.get(name) == actual.get(name):
            continue
        label = Path(name).name or "configuration entry"
        if name not in expected:
            labels.append(label + " appeared")
        elif name not in actual:
            labels.append(label + " disappeared")
        else:
            labels.append(label + " changed")
    return labels


def attach_process_evidence(
    error,
    *,
    command=None,
    exit_code=None,
    output_tail="",
    output_sha256=None,
    preflight_proof=None,
    metadata=None,
):
    """Attach bounded process and successful preflight evidence to any worker error."""
    safe_command = sanitized_command(command or [])
    safe_tail = redacted_tail(output_tail)
    combined_metadata = dict(metadata or {})
    if preflight_proof is not None:
        combined_metadata["preflight_proof"] = preflight_proof
    error.command = safe_command
    error.exit_code = exit_code
    error.output_tail = safe_tail
    error.output_sha256 = output_sha256
    error.metadata = combined_metadata
    error.preflight_proof = preflight_proof
    error.stage = {
        "command": safe_command,
        "exit_code": exit_code,
        "output_tail": safe_tail,
        "output_sha256": output_sha256,
        "metadata": combined_metadata,
    }
    return error


def load_helper():
    spec = importlib.util.spec_from_file_location("lifeos_deepseek_helper", HELPER_PATH)
    require(spec and spec.loader, "DeepSeek helper cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RealWorker:
    def __init__(self, helper=None, *, platform_name=None):
        self.helper = helper or load_helper()
        self.platform_name = platform_name
        self._preflight_proof = None
        self._repo = None
        self._run_root = None
        self._runtime_home = None
        self._runtime = None
        self._project_roots = None
        self._authority_baseline = None
        self._main_config_expected = None
        self._main_config_events = []

    def bind_run(self, repo, run_root):
        require(self._run_root is None, "worker run is already bound")
        self._repo = Path(repo).resolve()
        self._run_root = validate_run_root(self._repo, Path(run_root))
        roots = getattr(self.helper, "authorized_project_roots", None)
        require(callable(roots), "worker helper does not expose verified project roots")
        self._project_roots = tuple(roots(self._repo))
        require(self._repo in {Path(root).resolve() for root in self._project_roots},
                "worker source repository is not in its verified project roots")
        inventory = getattr(self.helper, "authority_inventory", None)
        require(callable(inventory), "worker helper does not expose authority inventory")
        authority = inventory(platform_name=self.platform_name)
        require(isinstance(authority, dict) and set(authority) == set(self.helper.MODELS),
                "worker helper authority inventory is incomplete")
        self._authority_baseline = authority
        return self

    def bind_main_config(self, snapshot):
        self._main_config_expected = dict(snapshot)

    def _configuration_guard(self, role, workspace, expected):
        if self._runtime_home is None:
            return None, ["runtime home is not bound"]
        try:
            _, actual = self.helper.command(
                role,
                workspace,
                explicit_home=self._runtime_home if self._runtime["home_mode"] == "frozen" else None,
                source_repo=self._repo,
                platform_name=self.platform_name,
            )
            authority = self.helper.authority_inventory(platform_name=self.platform_name)
        except Exception as cause:
            return None, [f"configuration snapshot unavailable ({type(cause).__name__})"]
        drift = configuration_drift(expected, actual)
        drift.extend(configuration_drift(self._authority_baseline, authority))
        return actual, list(dict.fromkeys(drift))

    def main_config_snapshot(self):
        return self.helper.main_config_snapshot()

    def main_config_transition(self, expected, actual):
        return self.helper.main_config_transition(
            expected,
            actual,
            self._project_roots,
        )

    def main_project_roots(self):
        return tuple(str(root) for root in (self._project_roots or ()))

    @staticmethod
    def _validate_main_config_event(event, role):
        require(isinstance(event, dict), f"{role} main config guard event is malformed")
        require(event.get("status") in {"UNCHANGED", "ALLOWED_TRUST_REGISTRATION"},
                f"{role} main config guard status is invalid")
        require(
            isinstance(event.get("before_sha256"), (str, type(None)))
            and isinstance(event.get("after_sha256"), (str, type(None)))
            and isinstance(event.get("allowed_project_root_names"), list)
            and isinstance(event.get("added_project_root_names"), list),
            f"{role} main config guard evidence is malformed",
        )
        return event

    def _main_config_guard(self, role, phase):
        if self._main_config_expected is None:
            self._main_config_expected = self.main_config_snapshot()
        expected = dict(self._main_config_expected)
        actual = self.main_config_snapshot()
        try:
            transition = self.main_config_transition(expected, actual)
            event = self._validate_main_config_event(
                {"phase": phase, **transition},
                role,
            )
        except Exception as cause:
            safe_failure = {
                "phase": phase,
                "status": "BLOCKED",
                "before_present": expected.get("present"),
                "before_sha256": expected.get("sha256"),
                "after_present": actual.get("present"),
                "after_sha256": actual.get("sha256"),
                "allowed_project_root_names": list(self.main_project_roots()),
                "added_project_root_names": [],
            }
            self._main_config_events.append(safe_failure)
            error = Blocked(f"{role} main Codex config guard blocked at {phase}: {cause}")
            error.main_config_events = list(self._main_config_events)
            error.main_config_snapshot = actual
            raise error from cause
        self._main_config_expected = actual
        self._main_config_events.append(event)
        return event

    def _runtime_for(self, role):
        if self._project_roots is None:
            roots = getattr(self.helper, "authorized_project_roots", None)
            require(callable(roots), "worker helper does not expose verified project roots")
            self._project_roots = tuple(roots(self._repo))
        binding = getattr(self.helper, "runtime_binding", None)
        require(callable(binding), "worker helper does not expose an explicit home-mode binding")
        runtime = binding(
            role,
            self._repo,
            self._run_root,
            platform_name=self.platform_name,
        )
        require(isinstance(runtime, dict), f"{role} runtime binding is malformed")
        expected_mode = self.helper.platform_home_mode(self.platform_name)
        require(runtime.get("home_mode") == expected_mode, f"{role} runtime home mode mismatch")
        require(runtime.get("role") == role, f"{role} runtime role mismatch")
        roots = tuple(str(Path(root).resolve()) for root in runtime.get("project_roots", ()))
        require(roots == tuple(self.main_project_roots()),
                f"{role} runtime roots do not match the verified source repository")
        home = Path(runtime["home"]).resolve(strict=True)
        if expected_mode == "frozen":
            require(path_within(home, self._run_root), f"{role} frozen home escaped the run root")
        else:
            require(
                os.path.normcase(str(home)) == os.path.normcase(str(Path(self.helper.codex_home()).resolve())),
                f"{role} shared home does not match the original user CODEX_HOME",
            )
        return runtime

    def _preflight(self, role, workspace, timeout, initial=None):
        self._preflight_proof = None
        require(role in self.helper.MODELS, "unsupported worker role")
        require(self._runtime_home is not None and self._runtime is not None,
                "runtime home is not bound")
        argv, snapshots = (
            initial
            if initial is not None
            else self.helper.command(
                role,
                workspace,
                explicit_home=self._runtime_home if self._runtime["home_mode"] == "frozen" else None,
                source_repo=self._repo,
                platform_name=self.platform_name,
            )
        )
        require(self.helper.key_present(role), f"missing dedicated key: {self.helper.KEY_ENV[role]}")
        mode = self.helper.sandbox_mode(role)
        require("--sandbox" in argv and argv[argv.index("--sandbox") + 1] == mode, "sandbox argv mismatch")
        require("--ask-for-approval" in argv and argv[argv.index("--ask-for-approval") + 1] == "never",
                "approval argv mismatch")
        required_overrides = {
            "sandbox_workspace_write.writable_roots": json.dumps([]),
            "sandbox_workspace_write.network_access": "false",
            "sandbox_workspace_write.exclude_tmpdir_env_var": "true",
            "sandbox_workspace_write.exclude_slash_tmp": "true",
        }
        for key, value in required_overrides.items():
            expected = key + "=" + value
            require(expected in argv, f"missing restrictive sandbox override: {key}")
        environment = child_environment(
            role,
            self.helper.KEY_ENV,
            codex_home_path=self._runtime_home,
            home_mode=self._runtime["home_mode"],
            original_home=self._runtime["original_home"],
        )
        self._main_config_guard(role, "before_preflight")
        probe_argv = [*argv, "debug", "prompt-input", "LADS effective permission preflight"]
        try:
            probe = run_process(probe_argv, workspace, "", min(timeout, 60), environment)
        except OSError as cause:
            error = Blocked(f"{role} permission preflight could not start: {type(cause).__name__}")
            raise attach_process_evidence(
                error,
                command=probe_argv,
                output_tail=str(cause),
            ) from cause
        try:
            require(not probe.timed_out and not probe.bounded_output and probe.exit_code == 0,
                    f"{role} permission preflight did not complete")
            permissions = verify_permission_probe(probe.stdout, role, workspace)
        except Blocked as error:
            raise attach_process_evidence(
                error,
                command=probe_argv,
                exit_code=probe.exit_code,
                output_tail="[preflight output withheld: raw prompt context]",
                output_sha256=sha_bytes(probe.stdout.encode("utf-8")),
            )
        try:
            self._preflight_proof = successful_preflight_proof(
                role,
                argv,
                probe_argv,
                probe,
                permissions,
                snapshots,
                self._runtime_home,
                sha_bytes(canonical(self._authority_baseline)),
                home_mode=self._runtime["home_mode"],
                home_proof_paths=(
                    (self._runtime["profile"], self._runtime["catalog"])
                    if self._runtime["home_mode"] == "shared"
                    else (
                        Path(self._runtime_home) / "config.toml",
                        self._runtime["profile"],
                        self._runtime["catalog"],
                    )
                ),
            )
        except Blocked as error:
            raise attach_process_evidence(
                error,
                command=probe_argv,
                exit_code=probe.exit_code,
                output_tail="[preflight output withheld: raw prompt context]",
                output_sha256=sha_bytes(probe.stdout.encode("utf-8")),
            )
        self._main_config_guard(role, "after_preflight")
        return argv, snapshots, mode, environment

    def run(self, role, workspace, prompt, task_id, timeout, source_digest=None, test_commands=()):
        started = now()
        self._preflight_proof = None
        self._runtime_home = None
        self._runtime = None
        self._main_config_events = []
        snapshots = None
        proof = None
        process = None
        command_argv = []
        try:
            require(self._run_root is not None and self._repo is not None,
                    f"{role} worker is not bound to a managed run root")
            runtime = self._runtime_for(role)
            self._runtime = runtime
            self._runtime_home = Path(runtime["home"]).resolve(strict=True)
            argv, snapshots = self.helper.command(
                role,
                workspace,
                explicit_home=self._runtime_home if runtime["home_mode"] == "frozen" else None,
                source_repo=self._repo,
                platform_name=self.platform_name,
            )
            _, preflight_drift = self._configuration_guard(role, workspace, snapshots)
            require(not preflight_drift,
                    "configuration drift detected before preflight: " + ", ".join(preflight_drift))
            self._main_config_guard(role, "before_dispatch")
            argv, snapshots, mode, environment = self._preflight(
                role,
                workspace,
                timeout,
                initial=(argv, snapshots),
            )
            proof = validate_preflight_proof(self._preflight_proof, role)
            _, post_preflight_drift = self._configuration_guard(role, workspace, snapshots)
            require(
                not post_preflight_drift,
                "configuration drift detected after permission preflight: "
                + ", ".join(post_preflight_drift),
            )
            self._main_config_guard(role, "before_exec")
            schema_path = managed_path(
                self._run_root,
                f"schemas/{role}-report.schema.json",
            )
            atomic_json(schema_path, report_output_schema(role))
            command_argv = [
                *argv,
                "exec",
                "--json",
                "--ephemeral",
                "--skip-git-repo-check",
                "--output-schema",
                str(schema_path),
                "-",
            ]
            if secretlike(" ".join(command_argv)):
                raise Blocked(f"{role} secret-like argv refused")
            process = run_process(command_argv, workspace, prompt, timeout, environment)
            self._main_config_guard(role, "after_process")
        except OSError as cause:
            error = Blocked(f"{role} process could not start: {type(cause).__name__}")
            try:
                self._main_config_guard(role, "exit_oserror")
            except Blocked as main_error:
                cause = main_error
            _, drift = self._configuration_guard(role, workspace, snapshots) if snapshots is not None else (None, [])
            if drift:
                error = Blocked(f"{error}; configuration drift detected: {', '.join(drift)}")
            raise attach_process_evidence(
                error,
                command=command_argv,
                output_tail=str(cause),
                preflight_proof=proof,
                metadata={"main_config_guard_events": list(self._main_config_events)},
            ) from cause
        except Exception as error:
            try:
                self._main_config_guard(role, "exit_error")
            except Blocked as main_error:
                error = Blocked(f"{error}; {main_error}")
            snapshots_after, drift = self._configuration_guard(role, workspace, snapshots) if snapshots is not None else (None, [])
            if drift:
                combined = Blocked(f"{error}; configuration drift detected: {', '.join(drift)}")
                raise attach_process_evidence(
                    combined,
                    command=getattr(error, "command", None) or command_argv,
                    exit_code=getattr(error, "exit_code", None),
                    output_tail=getattr(error, "output_tail", ""),
                    output_sha256=getattr(error, "output_sha256", None),
                    preflight_proof=getattr(error, "preflight_proof", None) or proof,
                    metadata={
                        **dict(getattr(error, "metadata", None) or {}),
                        "main_config_guard_events": list(self._main_config_events),
                    },
                ) from error
            raise
        output_sha256 = sha_bytes(process.stdout.encode("utf-8"))
        output_tail = redacted_tail(process.stderr + "\n" + process.stdout)
        try:
            if process.timed_out:
                raise Blocked(f"{role} timed out; process tree terminated")
            combined = process.stderr + process.stdout
            if process.exit_code != 0:
                if mode == "read-only" and TRANSIENT.search(combined) and not re.search(r"(?i)auth|401|403", combined):
                    raise TransientWorkerError(f"{role} transient process failure")
                raise Blocked(f"{role} process exited {process.exit_code}")
            if process.bounded_output:
                raise Blocked(f"{role} output exceeded the capture bound")
            report, commands, usage = parse_event_stream(process.stdout)
            report = validate_report(report, task_id, role, source_digest, test_commands, commands)
            snapshots_after, drift = self._configuration_guard(role, workspace, snapshots)
            require(not drift, f"{role} configuration drift detected: {', '.join(drift)}")
            self._main_config_guard(role, "after_validation")
        except Exception as error:
            try:
                self._main_config_guard(role, "exit_validation")
            except Blocked as main_error:
                error = Blocked(f"{error}; {main_error}")
            _, drift = self._configuration_guard(role, workspace, snapshots)
            if drift:
                error = Blocked(f"{error}; configuration drift detected: {', '.join(drift)}")
            original_exit = getattr(error, "exit_code", None)
            raise attach_process_evidence(
                error,
                command=getattr(error, "command", None) or command_argv,
                exit_code=process.exit_code if original_exit is None else original_exit,
                output_tail=getattr(error, "output_tail", "") or output_tail,
                output_sha256=getattr(error, "output_sha256", None) or output_sha256,
                preflight_proof=getattr(error, "preflight_proof", None) or proof,
                metadata={
                    **dict(getattr(error, "metadata", None) or {}),
                    "main_config_guard_events": list(self._main_config_events),
                },
            )
        ended = now()
        metadata = {
            "role": role,
            "provider": "lifeos_deepseek",
            "model": self.helper.MODELS[role],
            "sandbox": mode,
            "start_time": started,
            "end_time": ended,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "config_snapshot_sha256": sha_bytes(canonical(snapshots_after)),
            "preflight_proof": proof,
            "runtime_home": str(self._runtime_home),
            "home_mode": self._runtime["home_mode"],
            "home_sha256": proof["home_sha256"],
            "authority_sha256": proof["authority_sha256"],
            "main_config_guard_events": list(self._main_config_events),
        }
        return WorkerResult(
            role,
            sanitized_report(report, exact_commands=test_commands if role == "qa-tester" else ()),
            sanitized_commands(commands, exact_commands=test_commands if role == "qa-tester" else ()),
            metadata,
            output_sha256,
            sanitized_command(command_argv),
            process.exit_code,
            output_tail,
        )


def child_environment(
    role,
    key_env,
    source=None,
    *,
    codex_home_path=None,
    home_mode="frozen",
    original_home=None,
):
    """Expose runtime/TLS settings, one key, and the explicitly bound CODEX_HOME."""
    source_environment = dict(os.environ if source is None else source)
    dedicated = key_env[role]
    require(home_mode in {"shared", "frozen"}, "child environment home mode is invalid")
    require(codex_home_path is not None, "bound CODEX_HOME is required")
    if home_mode == "shared":
        require(original_home is not None, "shared child environment lacks the original home")
        require(
            os.path.normcase(str(Path(codex_home_path).resolve(strict=True)))
            == os.path.normcase(str(Path(original_home).resolve(strict=True))),
            "shared child environment changed CODEX_HOME",
        )
    environment = {}
    for name, value in source_environment.items():
        folded = name.casefold()
        if name == dedicated:
            environment[name] = value
        elif folded in CHILD_ENVIRONMENT_ALLOWLIST and folded != "codex_home":
            environment[name] = value
    environment["CODEX_HOME"] = str(Path(codex_home_path).resolve(strict=True))
    require(
        not any(name.casefold().startswith("openai_") for name in environment),
        "OpenAI/GPT credentials were inherited by a DeepSeek child",
    )
    return environment


def prompt_header(task_id, role, task, constraints=(), *, phase=None):
    lines = [
        f"TASK_ID: {task_id}",
        f"ROLE: {role}",
        "Use only the assigned DeepSeek profile and this isolated workspace.",
        "Do not start native children, read credentials, or use another provider.",
        f"TASK: {task}",
    ]
    if phase is not None:
        require(phase in STATES, f"unknown prompt phase: {phase}")
        lines.append(f"PHASE={phase}")
    lines.extend(constraints)
    return "\n".join(lines)


def require_report_shape(task_id, role):
    statuses = "|".join(sorted(STATUSES[role]))
    extra = ',"source_snapshot_sha256":"<64 lowercase hex>"' if role == "qa-tester" else ""
    return (
        "Your LAST assistant message must be pure JSON with exactly: "
        f'{{"task_id":"{task_id}","role":"{role}","status":"{statuses}",'
        f'"summary":"...","evidence":["..."]{extra}}}. '
        "No Markdown fence. Evidence must describe actual observations or command executions."
    )


class Orchestrator:
    def __init__(self, worker=None, timeout=900, log=None):
        self.worker = worker or RealWorker()
        self.timeout = timeout
        self.log = log or (lambda _message: None)
        self.run_root = None
        self.state = None
        self.events = None

    def emit(self, event, **fields):
        record = {"at": now(), "event": event, **fields}
        append_event(managed_path(self.run_root, "events.jsonl"), record)
        self.log(f"{event} {fields.get('state', fields.get('role', ''))}".strip())

    def write_state(self):
        atomic_json(managed_path(self.run_root, "state.json"), self.state)

    def main_config_snapshot(self):
        snapshot = getattr(self.worker, "main_config_snapshot", None)
        require(callable(snapshot), "worker does not expose the main config guard")
        value = snapshot()
        require(
            isinstance(value, dict)
            and set(value) == {
                "present",
                "sha256",
                "project_entries",
                "project_keys",
                "non_projects_sha256",
            }
            and type(value["present"]) is bool
            and (
                (value["present"] is False and value["sha256"] is None)
                or (value["present"] is True and isinstance(value["sha256"], str) and SHA_RE.fullmatch(value["sha256"]))
            ),
            "worker main config snapshot is malformed",
        )
        return dict(value)

    @staticmethod
    def _validate_main_config_transition(transition, phase):
        fields = {
            "status",
            "before_present",
            "before_sha256",
            "after_present",
            "after_sha256",
            "allowed_project_root_names",
            "added_project_root_names",
        }
        require(isinstance(transition, dict) and set(transition) == fields,
                "main config transition evidence is malformed")
        require(transition["status"] in {"UNCHANGED", "ALLOWED_TRUST_REGISTRATION"},
                "main config transition status is invalid")
        require(
            isinstance(transition["before_sha256"], (str, type(None)))
            and isinstance(transition["after_sha256"], (str, type(None)))
            and isinstance(transition["allowed_project_root_names"], list)
            and isinstance(transition["added_project_root_names"], list)
            and all(isinstance(item, str) and item for item in transition["allowed_project_root_names"])
            and all(isinstance(item, str) and item for item in transition["added_project_root_names"]),
            "main config transition evidence is unsafe or incomplete",
        )
        if transition["status"] == "UNCHANGED":
            require(
                transition["before_sha256"] == transition["after_sha256"]
                and not transition["added_project_root_names"],
                "unchanged main config evidence is inconsistent",
            )
        else:
            require(
                transition["before_sha256"] != transition["after_sha256"]
                and transition["added_project_root_names"],
                "allowed main config registration lacks a raw SHA change",
            )
        return {"phase": phase, **transition}

    def _record_main_config_event(self, event):
        events = self.state.setdefault("main_config_guard_events", [])
        events.append(event)
        self.emit(
            "main_config_guard",
            phase=event["phase"],
            status=event["status"],
            before_sha256=event["before_sha256"],
            after_sha256=event["after_sha256"],
            allowed_project_root_names=event["allowed_project_root_names"],
            added_project_root_names=event["added_project_root_names"],
        )

    def main_config_guard(self, phase):
        baseline = self.state.get("main_config_snapshot")
        if baseline is None:
            baseline = self.main_config_snapshot()
            self.state["main_config_snapshot"] = baseline
        actual = self.main_config_snapshot()
        transition_method = getattr(self.worker, "main_config_transition", None)
        try:
            require(callable(transition_method), "worker does not expose main config transition rules")
            transition = transition_method(baseline, actual)
            event = self._validate_main_config_transition(transition, phase)
        except Exception as cause:
            roots_method = getattr(self.worker, "main_project_roots", None)
            allowed = list(roots_method()) if callable(roots_method) else []
            failed = {
                "phase": phase,
                "status": "BLOCKED",
                "before_present": baseline.get("present"),
                "before_sha256": baseline.get("sha256"),
                "after_present": actual.get("present"),
                "after_sha256": actual.get("sha256"),
                "allowed_project_root_names": allowed,
                "added_project_root_names": [],
            }
            self._record_main_config_event(failed)
            self.write_state()
            raise Blocked(f"main Codex config guard blocked at {phase}: {cause}") from cause
        self.state["main_config_snapshot"] = actual
        self._record_main_config_event(event)
        self.write_state()
        return event

    def require_main_config_unchanged(self):
        return self.main_config_guard("run_level_check")

    def transition(self, state):
        require(state in STATES, f"unknown state: {state}")
        self.main_config_guard("before_" + state.casefold())
        self.state["state"] = state
        self.state["updated_at"] = now()
        self.emit("state_transition", state=state)
        self.write_state()

    def record_stage(self, role, attempt, status, started, *, result=None, error=None):
        reports = self.state.setdefault("stage_reports", [])
        index = len(reports) + 1
        relative = f"stages/{index:04d}-{role}-build-{self.state.get('builds', 0)}-attempt-{attempt}.json"
        details = dict(getattr(error, "stage", {}) or {}) if error is not None else {}
        if error is not None:
            for field in ("command", "exit_code", "output_tail", "output_sha256"):
                if hasattr(error, field):
                    value = getattr(error, field)
                    if value is not None and (value != [] or field not in details):
                        details[field] = value
        command = result.command if result is not None else details.get("command", [])
        exit_code = result.exit_code if result is not None else details.get("exit_code")
        output_tail = result.output_tail if result is not None else details.get("output_tail", "")
        output_sha256 = result.output_sha256 if result is not None else details.get("output_sha256")
        if result is not None:
            report = sanitized_report(
                result.report,
                exact_commands=self.state.get("test_commands", ()) if role == "qa-tester" else (),
            )
            report_sha256 = sha_bytes(canonical(report))
            metadata = result.metadata
        else:
            report = None
            report_sha256 = None
            metadata = dict(getattr(error, "metadata", {}) or details.get("metadata", {}) or {})
            proof = getattr(error, "preflight_proof", None)
            if proof is not None:
                metadata["preflight_proof"] = proof
        payload = {
            "task_id": self.state["task_id"],
            "role": role,
            "status": status,
            "build": self.state.get("builds", 0),
            "attempt": attempt,
            "start_time": started,
            "end_time": now(),
            "command": sanitized_command(command or []),
            "commands": sanitized_commands(result.commands if result is not None else []),
            "exit_code": exit_code,
            "output_tail": redacted_tail(output_tail),
            "metadata": metadata,
            "output_sha256": output_sha256,
            "report": report,
            "report_sha256": report_sha256,
            "reason": redact(str(error)) if error is not None else None,
        }
        require(
            payload["report_sha256"] is None
            or payload["report_sha256"] == sha_bytes(canonical(payload["report"])),
            "stage report hash does not bind the sanitized report",
        )
        path = managed_path(self.run_root, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        managed_path(self.run_root, relative)
        atomic_json(path, payload)
        reports.append({"path": relative, "sha256": sha_file(path)})
        self.write_state()

    def worker_call(self, role, workspace, prompt, source_digest=None, test_commands=()):
        attempts = MAX_READONLY_RETRIES + 1 if role != "builder" else 1
        for attempt in range(1, attempts + 1):
            started = now()
            result = None
            self.emit("role_started", role=role, attempt=attempt)
            try:
                self.main_config_guard(f"before_{role}_attempt_{attempt}")
                result = self.worker.run(
                    role,
                    workspace,
                    prompt,
                    self.state["task_id"],
                    self.timeout,
                    source_digest,
                    test_commands,
                )
                proof = validate_preflight_proof(result.metadata.get("preflight_proof"), role)
                exact_commands = test_commands if role == "qa-tester" else ()
                result.report = sanitized_report(result.report, exact_commands=exact_commands)
                result.commands = sanitized_commands(result.commands, exact_commands=exact_commands)
                result.metadata["preflight_proof"] = proof
                self.main_config_guard(f"after_{role}_attempt_{attempt}_success")
                self.state["role_runs"].append(result.metadata)
                self.record_stage(role, attempt, result.report["status"], started, result=result)
                self.emit("role_completed", role=role, status=result.report["status"])
                return result
            except TransientWorkerError as error:
                try:
                    self.main_config_guard(f"after_{role}_attempt_{attempt}_transient")
                except Blocked as main_error:
                    self.record_stage(
                        role,
                        attempt,
                        "BLOCKED",
                        started,
                        error=Blocked(f"{error}; {main_error}"),
                    )
                    raise Blocked(f"{error}; {main_error}") from main_error
                self.record_stage(role, attempt, "RETRYABLE", started, error=error)
                require(role != "builder", "Builder transient retry is forbidden")
                require(attempt < attempts, f"{role} transient retry limit reached")
                self.emit("readonly_retry", role=role, attempt=attempt)
            except Exception as error:
                if result is not None:
                    attach_process_evidence(
                        error,
                        command=result.command,
                        exit_code=result.exit_code,
                        output_tail=result.output_tail,
                        output_sha256=result.output_sha256,
                        preflight_proof=result.metadata.get("preflight_proof"),
                        metadata=result.metadata,
                    )
                try:
                    self.main_config_guard(f"after_{role}_attempt_{attempt}_error")
                except Blocked as main_error:
                    combined = Blocked(f"{error}; {main_error}")
                    attach_process_evidence(
                        combined,
                        command=getattr(error, "command", None),
                        exit_code=getattr(error, "exit_code", None),
                        output_tail=getattr(error, "output_tail", ""),
                        output_sha256=getattr(error, "output_sha256", None),
                        preflight_proof=getattr(error, "preflight_proof", None),
                        metadata=getattr(error, "metadata", None),
                    )
                    self.record_stage(role, attempt, "BLOCKED", started, error=combined)
                    raise combined from main_error
                self.record_stage(role, attempt, "BLOCKED", started, error=error)
                raise
        raise Blocked("unreachable worker retry state")

    def finish(self, status, reason=None, **fields):
        if self.state.get("main_config_snapshot") is not None:
            try:
                self.main_config_guard("finish")
            except Blocked as guard_error:
                status = "BLOCKED"
                reason = f"{reason}; {guard_error}" if reason else str(guard_error)
        runs = self.state["role_runs"]
        providers = sorted({item.get("provider") for item in runs if item.get("provider")})
        models = sorted({item.get("model") for item in runs if item.get("model")})
        input_tokens = (
            sum(item["input_tokens"] for item in runs)
            if runs and all(isinstance(item.get("input_tokens"), int) for item in runs) else None
        )
        output_tokens = (
            sum(item["output_tokens"] for item in runs)
            if runs and all(isinstance(item.get("output_tokens"), int) for item in runs) else None
        )
        handoff = {
            "task_id": self.state["task_id"],
            "task_status": status,
            "reason": redact(reason) if reason else None,
            "source_head": self.state["source_head"],
            "source_baseline_sha256": self.state["baseline_sha256"],
            "source_snapshot_sha256": self.state.get("source_snapshot_sha256"),
            "candidate_sha256": self.state.get("candidate_sha256"),
            "manifest_sha256": self.state["manifest_sha256"],
            "allowed_paths": self.state["allowed_paths"],
            "files": self.state.get("files", []),
            "diff": self.state.get("diff", ""),
            "tests": self.state.get("tests", []),
            "unmet_checks": self.state.get("unmet_checks", []),
            "role_runs": self.state["role_runs"],
            "stage_reports": self.state.get("stage_reports", []),
            "builds": self.state["builds"],
            "model_provider": providers[0] if len(providers) == 1 else providers or None,
            "model_name": models[0] if len(models) == 1 else models or None,
            "main_config_snapshot": self.state.get("main_config_snapshot"),
            "main_config_guard_events": self.state.get("main_config_guard_events", []),
            "start_time": self.state["created_at"],
            "end_time": now(),
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            "limits": [
                "Scope gate is post-run; per-file OS ACLs are not claimed.",
                "Candidate is never copied back, merged, committed, pushed, or deployed.",
                "Browser, device, real user data, and multiuser isolation are not supported by this CLI.",
                "State JSON uses atomic replace, not a filesystem-wide transaction.",
            ],
            **fields,
        }
        atomic_json(managed_path(self.run_root, "finalhandoff.json"), handoff)
        self.state["state"] = "BLOCKED" if status == "BLOCKED" else "AWAITING_MAIN_ACCEPTANCE"
        self.state["updated_at"] = now()
        self.write_state()
        self.emit("finished", status=status)
        return handoff

    def run(self, manifest):
        require_ignored_work(manifest.repo)
        with RepoLock(manifest.repo, manifest.task):
            task_id = str(uuid.uuid4())
            run_root = manifest.repo / "work" / "lads" / "runs" / task_id
            require(not run_root.exists(), "task run path already exists")
            run_root.mkdir(parents=True)
            validate_run_root(manifest.repo, run_root)
            self.run_root = run_root
            self.events = run_root / "events.jsonl"
            self.state = {
                "task_id": task_id,
                "demo": manifest.demo,
                "state": "CREATED",
                "created_at": now(),
                "updated_at": now(),
                "repo": str(manifest.repo),
                "task": manifest.task,
                "source_head": git_head(manifest.repo),
                "baseline_sha256": git_status(manifest.repo),
                "manifest_sha256": manifest.raw_sha256,
                "allowed_paths": list(manifest.allowed_paths),
                "test_commands": list(manifest.test_commands),
                "applicability": manifest.applicability,
                "role_runs": [],
                "stage_reports": [],
                "tests": [],
                "builds": 0,
                "main_config_guard_events": [],
                "unmet_checks": [
                    {"check": "browser", "status": "N/A" if not manifest.applicability["browser"] else "BLOCKED"},
                    {"check": "multiuser", "status": "N/A" if not manifest.applicability["multiuser"] else "BLOCKED"},
                ],
            }
            self.write_state()
            self.emit("task_created", task_id=task_id)
            try:
                require(not manifest.applicability["browser"], "browser applicability requested but unavailable")
                require(not manifest.applicability["multiuser"], "multiuser applicability requested but unavailable")
                bind_run = getattr(self.worker, "bind_run", None)
                if callable(bind_run):
                    bind_run(manifest.repo, run_root)
                self.state["main_config_snapshot"] = self.main_config_snapshot()
                bind_main_config = getattr(self.worker, "bind_main_config", None)
                if callable(bind_main_config):
                    bind_main_config(self.state["main_config_snapshot"])
                self.write_state()
                source_dir = run_root / "source"
                source = capture_source(manifest.repo, source_dir)
                require(source.head == self.state["source_head"], "HEAD changed before snapshot")
                self.state["source_snapshot_sha256"] = source.digest
                self.write_state()

                self.transition("SCANNING")
                scanner_prompt = (
                    prompt_header(task_id, "scanner", manifest.task,
                                  ["Read-only source snapshot: " + str(source_dir),
                                   "Inspect source, uncommitted baseline, risks, and test constraints."],
                                  phase="SCANNING")
                    + "\n" + require_report_shape(task_id, "scanner")
                )
                scan = self.worker_call("scanner", source_dir, scanner_prompt, source.digest)
                require(inventory_tree(source_dir) == source.files, "Scanner changed source snapshot")
                require(scan.report["status"] == "SCAN_COMPLETE", "scanner blocked the task")

                self.transition("PLAN_REVIEW")
                plan_constraints = [
                    "Source HEAD: " + source.head,
                    "Source snapshot SHA-256: " + source.digest,
                    "Proposed task: " + manifest.task,
                    "Proposed exact allowed paths: " + json.dumps(manifest.allowed_paths),
                    "Immutable prescribed test commands: " + json.dumps(manifest.test_commands),
                    "Required downstream flow: Builder -> QA executes those immutable tests -> "
                    "PHASE=FINAL_REVIEW performs strict review with no plan/demo exemption.",
                    "The current baseline is a buggy/unfinished candidate, not a completed implementation.",
                    "Approve only if this concrete task, exact scope, immutable tests, and downstream flow are safe.",
                ]
                if manifest.demo:
                    plan_constraints.append(
                        "DEMO SCOPE ONLY: accept the transparent initial-negative build plan, but QA must "
                        "actually fail and PHASE=FINAL_REVIEW must request changes without exemption."
                    )
                review_prompt = (
                    prompt_header(task_id, "reviewer", manifest.task,
                                  ["Read-only source snapshot: " + str(source_dir),
                                   "Scanner report: " + json.dumps(scan.report, ensure_ascii=False),
                                   *plan_constraints],
                                  phase="PLAN_REVIEW")
                    + "\n" + require_report_shape(task_id, "reviewer")
                )
                plan = self.worker_call("reviewer", source_dir, review_prompt, source.digest)
                require(inventory_tree(source_dir) == source.files, "plan Reviewer changed source snapshot")
                require(
                    plan.report["status"] == "APPROVED",
                    f"plan review did not approve ({plan.report['status']})",
                )

                builder_dir = run_root / "builder"
                git(manifest.repo, "worktree", "add", "--detach", str(builder_dir), source.head)
                clear_generated_tree(manifest.repo, run_root)
                overlay_source(source_dir, builder_dir)
                require(inventory_tree(builder_dir) == source.files, "Builder overlay does not match source snapshot")

                build_number = 0
                feedback = None
                while True:
                    build_number += 1
                    require(build_number <= MAX_BUILDS, "four-build limit reached")
                    self.state["builds"] = build_number
                    self.transition("BUILDING")
                    constraints = [
                        "Independent Git worktree: " + str(builder_dir),
                        "Source HEAD: " + source.head,
                        "Source snapshot SHA-256: " + source.digest,
                        "Build number: " + str(build_number),
                        "Allowed changed paths (exact): " + json.dumps(manifest.allowed_paths),
                        "Do not commit, push, integrate, install dependencies, or edit outside allowed paths.",
                        "Prescribed QA tests are immutable: " + json.dumps(manifest.test_commands),
                        "Safe Scanner report: " + json.dumps(scan.report, ensure_ascii=False),
                        "APPROVED plan report: " + json.dumps(plan.report, ensure_ascii=False),
                    ]
                    if manifest.demo and build_number == 1:
                        constraints.append(
                            "TRANSPARENT NEGATIVE DRILL: intentionally retain the current add.py "
                            "a+b+1 candidate without edits and report BUILT. QA failure is expected."
                        )
                    if feedback:
                        constraints.append("Rework feedback: " + json.dumps(feedback, ensure_ascii=False))
                    builder_prompt = (
                        prompt_header(task_id, "builder", manifest.task, constraints, phase="BUILDING")
                        + "\n" + require_report_shape(task_id, "builder")
                    )
                    build = self.worker_call("builder", builder_dir, builder_prompt)
                    require(build.report["status"] == "BUILT", "builder blocked the task")
                    require(git_head(builder_dir) == source.head, "Builder changed worktree HEAD")

                    candidate_files = inventory_tree(builder_dir)
                    changes = changed_files(source.files, candidate_files)
                    outside = [item for item in changes if item["path"] not in manifest.allowed_paths]
                    require(not outside, "out-of-scope Builder changes: " + json.dumps(outside[:20]))
                    candidate_hash = snapshot_digest(source.head, candidate_files)
                    candidate_dir = run_root / "candidates" / f"build-{build_number}"
                    copied_files = copy_tree_no_git(builder_dir, candidate_dir)
                    require(copied_files == candidate_files, "candidate copy changed bytes")
                    self.state["candidate_sha256"] = candidate_hash
                    self.state["files"] = changes
                    self.state["diff"] = text_diff(source_dir, candidate_dir, [item["path"] for item in changes])
                    self.write_state()
                    safe_prompt_diff = redact(bounded(self.state["diff"], 12000))
                    safe_build_report = sanitized_report(build.report)

                    qa_dir = run_root / "qa" / f"build-{build_number}"
                    qa_files = copy_tree_no_git(candidate_dir, qa_dir)
                    require(qa_files == candidate_files, "QA copy changed candidate bytes")
                    self.transition("QA")
                    qa_prompt = (
                        prompt_header(task_id, "qa-tester", manifest.task, [
                            "Read-only QA snapshot: " + str(qa_dir),
                            "Source HEAD: " + source.head,
                            "source_snapshot_sha256 REQUIRED VALUE: " + candidate_hash,
                            "Return the value above verbatim in the required source_snapshot_sha256 field.",
                            "Safe Builder report: " + json.dumps(safe_build_report, ensure_ascii=False),
                            "Exact allowed paths: " + json.dumps(manifest.allowed_paths),
                            "Changed files: " + json.dumps(changes),
                            "Bounded redacted diff:\n" + safe_prompt_diff,
                            "Run exactly these stdout-only commands: " + json.dumps(manifest.test_commands),
                            "Do not run extra exploratory commands.",
                            "Do not modify files. Browser/multiuser checks are unavailable and must be N/A "
                            "unless applicability is true, which is blocked.",
                        ], phase="QA")
                        + "\n" + require_report_shape(task_id, "qa-tester")
                    )
                    qa = self.worker_call(
                        "qa-tester", qa_dir, qa_prompt, candidate_hash, manifest.test_commands
                    )
                    require(inventory_tree(qa_dir) == qa_files, "QA source snapshot changed during QA")
                    require(qa.report["status"] in {"PASS", "FAIL"}, "QA did not produce PASS or FAIL")
                    safe_qa_commands = sanitized_commands(
                        qa.commands,
                        exact_commands=manifest.test_commands,
                    )
                    safe_qa_report = sanitized_report(
                        qa.report,
                        exact_commands=manifest.test_commands,
                    )
                    self.state["tests"].extend([
                        {**item, "build": build_number} for item in safe_qa_commands
                    ])

                    self.transition("FINAL_REVIEW")
                    final_prompt = (
                        prompt_header(task_id, "reviewer", manifest.task, [
                            "Strict final phase: no plan approval, demo scope, or expected-negative build "
                            "exempts this review.",
                            "Read-only QA snapshot: " + str(qa_dir),
                            "Candidate digest: " + candidate_hash,
                            "Allowed paths: " + json.dumps(manifest.allowed_paths),
                            "Immutable prescribed test commands: " + json.dumps(manifest.test_commands),
                            "Changed files: " + json.dumps(changes),
                            "Diff: " + safe_prompt_diff,
                            "QA report: " + json.dumps(safe_qa_report, ensure_ascii=False),
                            "Actual QA command events: " + json.dumps(safe_qa_commands),
                            "Assess the actual QA status, command exit codes, changed files, and diff.",
                            "A QA FAIL requires CHANGES_REQUESTED based on concrete evidence.",
                        ], phase="FINAL_REVIEW")
                        + "\n" + require_report_shape(task_id, "reviewer")
                    )
                    final_review = self.worker_call("reviewer", qa_dir, final_prompt, candidate_hash)
                    require(inventory_tree(qa_dir) == qa_files, "final Reviewer changed QA snapshot")
                    if final_review.report["status"] == "BLOCKED":
                        raise Blocked("final Reviewer blocked the task")
                    if qa.report["status"] == "FAIL":
                        require(
                            final_review.report["status"] == "CHANGES_REQUESTED",
                            "final Reviewer must request changes for QA FAIL",
                        )
                    if qa.report["status"] == "PASS" and final_review.report["status"] == "APPROVED":
                        self.require_main_config_unchanged()
                        self.require_source_unchanged(source)
                        approval = {
                            "task_id": task_id,
                            "source_head": source.head,
                            "baseline_sha256": self.state["baseline_sha256"],
                            "allowed_paths": list(manifest.allowed_paths),
                            "candidate_sha256": candidate_hash,
                            "qa_report": safe_qa_report,
                            "reviewer_report": final_review.report,
                        }
                        return self.finish(
                            "AWAITING_MAIN_ACCEPTANCE",
                            approval=approval,
                            qa_final=safe_qa_report,
                            reviewer_final=final_review.report,
                        )
                    feedback = {
                        "qa": safe_qa_report,
                        "reviewer": final_review.report,
                        "actual_qa_commands": safe_qa_commands,
                    }
                    require(final_review.report["status"] != "BLOCKED", "final review blocked")
                    if build_number >= MAX_BUILDS:
                        raise Blocked("QA/reviewer still failed after four builds")
            except Blocked as error:
                return self.finish("BLOCKED", reason=str(error))
            except Exception as error:
                return self.finish("BLOCKED", reason=f"{type(error).__name__}: {error}")

    def require_source_unchanged(self, source):
        repo = manifest_repo(self.state["repo"])
        head = git_head(repo)
        require(head == source.head, "source HEAD drifted during task")
        require(git_status(repo) == self.state["baseline_sha256"], "source baseline drifted during task")
        paths = source_paths(repo)
        actual = {}
        for relative in paths:
            actual[relative] = sha_file(repo / Path(relative))
        require(actual == source.files, "source snapshot drifted during task")


def manifest_repo(value):
    return Path(value).resolve()


def clear_generated_tree(repo, run_root):
    repo = Path(repo).resolve()
    run_root = validate_run_root(repo, run_root)
    root = run_root / "builder"
    require(root.is_dir(), "expected generated Builder worktree is missing")
    require(not is_reparse(root), "expected generated Builder worktree is a reparse/symlink")
    require(path_within(root, run_root), "Builder worktree escapes UUID run root")
    reject_reparse_tree(root)
    for child in root.iterdir():
        if child.name == ".git":
            continue
        require(not is_reparse(child), f"generated child is a reparse/symlink: {child.name}")
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            require(child.is_file(), f"generated child is not a regular file: {child.name}")
            child.unlink()
    require([item.name for item in root.iterdir()] == [".git"], "generated Builder cleanup target drifted")


def overlay_source(source, destination):
    for child in Path(source).iterdir():
        target = Path(destination) / child.name
        if child.is_dir():
            shutil.copytree(child, target)
        else:
            shutil.copyfile(child, target)


def create_demo_manifest(base_repo):
    demo_id = str(uuid.uuid4())
    demo_root = Path(base_repo) / "work" / "lads" / "demos" / demo_id
    source = demo_root / "source"
    (source / "tests").mkdir(parents=True)
    (source / ".gitignore").write_text("work/\n__pycache__/\n", encoding="utf-8")
    (source / "add.py").write_text("def add(a, b):\n    return a + b + 1\n", encoding="utf-8")
    (source / "tests" / "test_add.py").write_text(
        "import unittest\nfrom add import add\n\n"
        "class AddTests(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n",
        encoding="utf-8",
    )
    git(source, "init", "-b", "main")
    git(source, "config", "user.name", "LADS Demo")
    git(source, "config", "user.email", "lads-demo@invalid.local")
    git(source, "add", ".")
    git(source, "commit", "-m", "synthetic negative drill baseline")
    data = {
        "repo": str(source),
        "task": (
            "Repair add(a,b) so it returns a+b. This is a disposable synthetic demo. "
            "The first build is a transparent negative drill that retains a+b+1; later rework must fix it. "
            "On Windows, use an explicit PowerShell or pwsh tool shell; do not use a default bash stub. "
            "Run only the one prescribed Python -B test command and do not explore extra QA commands. "
            "The synthetic source contains only add.py, tests/test_add.py, and .gitignore; never traverse "
            "the parent repository or read .codex, user, or private data."
        ),
        "allowed_paths": ["add.py"],
        "test_commands": ["python -B -m unittest discover -s tests -v"],
        "applicability": {"browser": False, "multiuser": False},
    }
    manifest_path = demo_root / "manifest.json"
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = load_manifest(manifest_path)
    return Manifest(
        manifest.repo,
        manifest.task,
        manifest.allowed_paths,
        manifest.test_commands,
        manifest.applicability,
        manifest.raw_sha256,
        True,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--task-file", type=Path, help="normal approved JSON task manifest")
    choice.add_argument("--demo", action="store_true", help="run the live disposable synthetic negative drill")
    parser.add_argument("--timeout", type=int, default=900, help="per worker timeout in seconds")
    parser.add_argument("--quiet", action="store_true", help="suppress progress lines")
    args = parser.parse_args(argv)
    try:
        require(30 <= args.timeout <= 3600, "timeout must be between 30 and 3600 seconds")
        manifest = create_demo_manifest(ROOT) if args.demo else load_manifest(args.task_file)
        log = (lambda _message: None) if args.quiet else (lambda message: print(message, file=sys.stderr))
        handoff = Orchestrator(timeout=args.timeout, log=log).run(manifest)
        print(json.dumps({
            "task_id": handoff["task_id"],
            "task_status": handoff["task_status"],
            "reason": handoff["reason"],
            "candidate_sha256": handoff["candidate_sha256"],
            "source_head": handoff["source_head"],
        }, ensure_ascii=False))
        return 0 if handoff["task_status"] == "AWAITING_MAIN_ACCEPTANCE" else 2
    except Blocked as error:
        print("BLOCKED: " + redact(str(error)), file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError) as error:
        print("BLOCKED: " + type(error).__name__ + ": " + redact(str(error)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
