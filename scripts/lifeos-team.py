#!/usr/bin/env python3
"""Explicit Codex role adapter; Python 3.11+, standard library only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
import queue
import threading
import time
from types import MappingProxyType

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("scanner", "reviewer", "builder")
MAX_LINE = 256 * 1024
MAX_TOTAL = 16 * 1024 * 1024
MAX_EVENTS = 20000
MAX_FINAL = 16000
ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def sanitize(text, limit=MAX_FINAL):
    text = re.sub(r"(?i)(bearer\s+|(?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", str(text))
    text = re.sub(r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    return text[:limit]


def credential_path(value):
    return bool(re.search(r"(^|/)(auth\.json|credentials(?:\..*)?|\.env(?:\..*)?|[^/]*\.(?:pem|key))$", str(value).replace("\\", "/"), re.I))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def safe_path(value):
    require(isinstance(value, str) and value and "\\" not in value,
            "allowed_paths must use relative forward-slash file paths")
    p = Path(value)
    require(not p.is_absolute() and not p.drive and all(
        x not in ("", ".", "..", ".git") for x in value.split("/"))
        and ":" not in value, "unsafe allowed path")
    require(all(x.lower() != ".git" for x in p.parts), "Git internals forbidden")
    resolved = (ROOT / p).resolve()
    require(resolved.is_relative_to(ROOT) and resolved != ROOT,
            "path escapes repository")
    require(not resolved.is_dir(), "allow exact files, not directories")
    require(not credential_path(value), "credential-like path forbidden")
    return value


def baseline():
    names = sorted(set(git("ls-files", "-z", "--cached", "--others",
                           "--exclude-standard").decode("utf-8").split("\0")) - {""})
    records = {}
    for name in names:
        safe_path(name)
        p = ROOT / name
        require(not p.is_symlink(), "symlink in baseline; review manually")
        # Never open credential files even if accidentally added to Git.
        require(not re.search(r"(^|/)(auth\.json|\.env(?:\..*)?|[^/]*\.(?:pem|key))$",
                              name, re.I), "credential-like path in baseline; review manually")
        records[name] = sha(p.read_bytes()) if p.exists() else None
    digest = sha(json.dumps(records, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False).encode("utf-8"))
    return {"head": git("rev-parse", "HEAD").decode().strip(),
            "baseline_sha256": digest, "files": records}


def approval(path, task_id, state, task):
    require(path is not None, "builder requires --approval-file")
    a = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    require(a.get("decision") == "APPROVE", "approval decision must be APPROVE")
    require(a.get("task_id") == task_id, "approval task_id mismatch")
    require(a.get("head") == state["head"], "approval HEAD mismatch")
    require(a.get("baseline_sha256") == state["baseline_sha256"], "approval baseline mismatch")
    require(a.get("task_sha256") == sha(task.encode("utf-8")), "approval task content mismatch")
    paths = a.get("allowed_paths")
    require(isinstance(paths, list) and paths and len(paths) == len(set(paths)),
            "approval requires unique exact allowed_paths")
    for p in paths:
        safe_path(p)
    require(isinstance(a.get("reviewer_evidence"), str) and a["reviewer_evidence"].strip(),
            "approval requires reviewer evidence")
    require(isinstance(a.get("user_authorization"), str) and a["user_authorization"].strip(),
            "approval requires separate user authorization reference")
    require(isinstance(a.get("evidence_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", a["evidence_sha256"]), "approval requires evidence hash")
    evidence = ROOT / safe_path(a["reviewer_evidence"])
    require(evidence.is_file() and sha(evidence.read_bytes()) == a["evidence_sha256"], "reviewer evidence mismatch")
    return set(paths)


def configuration(env, role):
    home = Path(env.get("CODEX_HOME") or Path(env.get("USERPROFILE") or Path.home()) / ".codex").resolve()
    env["CODEX_HOME"] = str(home)
    # Codex searches global and ancestor project configurations.
    candidates = {home / "config.toml"}
    candidates.update(p / ".codex/config.toml" for p in (ROOT, *ROOT.parents))
    snapshots, servers, apps = {}, set(), set()
    for p in sorted(candidates):
        raw = p.read_bytes() if p.exists() else b""
        snapshots[str(p)] = sha(raw)
        data = tomllib.loads(raw.decode("utf-8-sig")) if raw else {}
        require(not data.get("profile"), "active config profiles require explicit review")
        table = data.get("mcp_servers", {})
        require(isinstance(table, dict), "cannot enumerate MCP servers")
        servers.update(table)
        app_table = data.get("apps", {})
        require(isinstance(app_table, dict), "cannot enumerate apps")
        apps.update(app_table)
        # Profiles may introduce servers even without a selected profile.
        for profile in data.get("profiles", {}).values():
            require(isinstance(profile, dict), "invalid profile configuration")
            extra = profile.get("mcp_servers", {})
            require(isinstance(extra, dict), "cannot enumerate profile MCP servers")
            servers.update(extra)
            extra_apps = profile.get("apps", {})
            require(isinstance(extra_apps, dict), "cannot enumerate profile apps")
            apps.update(extra_apps)
    p = ROOT / ".codex/agents" / (role + ".toml")
    raw = p.read_bytes()
    snapshots[str(p)] = sha(raw)
    data = tomllib.loads(raw.decode("utf-8-sig"))
    require(data.get("name") == role and isinstance(data.get("developer_instructions"), str)
            and data["developer_instructions"].strip(), "invalid whitelisted role TOML")
    instructions = data["developer_instructions"]
    marker = f"LIFEOS_ROLE role={role} file=.codex/agents/{role}.toml sha256={sha(instructions.encode())}"
    mode = "workspace-write" if role == "builder" else "read-only"
    values = {"sandbox_mode": mode, "approval_policy": "never",
              "developer_instructions": marker + "\n" + instructions,
              "features.apps": False, "apps._default.enabled": False}
    for server in sorted(servers):
        require(isinstance(server, str) and re.fullmatch(r"[A-Za-z0-9_-]+", server),
                "MCP name cannot be safely addressed by CLI; stop")
        values[f"mcp_servers.{server}.enabled"] = False
    for app in sorted(apps):
        require(re.fullmatch(r"[A-Za-z0-9_-]+", app), "app name cannot be safely disabled")
        values[f"apps.{app}.enabled"] = False
    argv = ["--disable", "plugins"]
    for key, value in values.items():
        encoded = json.dumps(value, ensure_ascii=True)
        # JSON strings/booleans here are valid TOML; verify rather than fallback parsing.
        tomllib.loads("value=" + encoded)
        argv += ["-c", key + "=" + encoded]
    return argv, snapshots, marker, mode, len(servers)


def launch_env():
    env = dict(os.environ)
    if os.name == "nt" and not any(env.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
                enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
                proxy = winreg.QueryValueEx(key, "ProxyServer")[0] if enabled else ""
        except FileNotFoundError:
            proxy = ""
        if proxy:
            entries = dict(x.split("=", 1) for x in proxy.split(";") if "=" in x)
            for scheme in ("http", "https"):
                value = entries.get(scheme) if entries else proxy
                if value:
                    env[scheme.upper() + "_PROXY"] = value if "://" in value else "http://" + value
    return env


def bounded_process(command, env, prompt, timeout, consume):
    """Drain both pipes with hard budgets; never retain raw events or stderr."""
    proc = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    events = queue.Queue(maxsize=64)
    stop = threading.Event()

    def send(item):
        while not stop.is_set():
            try:
                events.put(item, timeout=.1)
                return
            except queue.Full:
                pass

    def reader(pipe, stream):
        try:
            while not stop.is_set():
                line = pipe.readline(MAX_LINE + 1)
                if not line:
                    break
                send((stream, line))
                if len(line) > MAX_LINE:
                    break
        finally:
            send((stream, None))

    def writer():
        try:
            proc.stdin.write(prompt.encode("utf-8"))
            proc.stdin.close()
        except (OSError, ValueError):
            pass

    for pipe, stream in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
        threading.Thread(target=reader, args=(pipe, stream), daemon=True).start()
    threading.Thread(target=writer, daemon=True).start()
    deadline, ended, total, count = time.monotonic() + timeout, set(), 0, 0
    try:
        while len(ended) != 2 or proc.poll() is None:
            require(time.monotonic() < deadline, "process timeout")
            try:
                stream, line = events.get(timeout=min(.1, max(.001, deadline-time.monotonic())))
            except queue.Empty:
                continue
            if line is None:
                ended.add(stream)
                continue
            total += len(line)
            count += 1
            require(len(line) <= MAX_LINE and total <= MAX_TOTAL and count <= MAX_EVENTS, "process output budget exceeded")
            if stream == "stdout":
                consume(line.decode("utf-8", errors="strict"))
        return proc.returncode
    finally:
        stop.set()
        if proc.poll() is None:
            # Kill process tree on Windows; no shell, no path or task interpolation.
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=10)
            else:
                proc.kill()
            proc.wait(timeout=10)


class EventReader:
    def __init__(self):
        self.parent = None
        self.children = set()
        self.final = None
        self.completed = False
        self.errors = []

    def feed(self, line):
        require(len(line.encode("utf-8")) <= MAX_LINE, "event line too large")
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            raise ValueError("malformed JSON event") from None
        require(isinstance(event, dict) and isinstance(event.get("type"), str), "malformed event shape")
        kind = event["type"]
        if kind == "thread.started":
            parent = event.get("thread_id")
            require(isinstance(parent, str) and re.fullmatch(ID, parent), "invalid parent ID")
            require(self.parent in (None, parent), "multiple parent IDs")
            self.parent = parent
        if kind in ("error", "turn.failed"):
            self.errors.append(sanitize(event.get("message", "CLI turn failed"), 1000))
        if kind == "turn.completed":
            self.completed = True
        item = event.get("item", event)
        if not isinstance(item, dict):
            raise ValueError("malformed event item")
        item_kind = item.get("type", "")
        if item_kind in ("agent_message", "AgentMessage") and kind in ("item.completed", "agent_message", "AgentMessage"):
            text = item.get("text", item.get("message"))
            require(isinstance(text, str) and len(text) <= MAX_FINAL, "missing or oversized assistant result")
            self.final = text
        if "collab" in str(item_kind).lower() or "collab" in kind.lower():
            tool = str(item.get("tool", item.get("tool_name", item_kind))).lower()
            if "spawn" in tool:
                ids = item.get("receiver_thread_ids", [])
                require(isinstance(ids, list), "malformed child metadata")
                ids = ids + [item[k] for k in ("child_id", "new_agent_id", "new_thread_id") if k in item]
                for child in ids:
                    require(isinstance(child, str) and re.fullmatch(ID, child), "invalid child ID")
                    self.children.add(child)

    def result(self, code):
        require(code == 0, "nonzero CLI exit")
        require(not self.errors, "CLI error: " + "; ".join(self.errors)[:1000])
        require(self.parent and self.completed and self.final, "missing parent/final/completion evidence")
        require(len(self.children) == 1, "missing or ambiguous child spawn evidence")
        child = next(iter(self.children))
        require(child != self.parent and child in self.final, "parent final must report actual child ID")
        require("CHILD_COMPLETED" in self.final, "parent must explicitly confirm child completion")
        return {"parent_id": self.parent, "child_id": child, "exit_code": code,
                "final_result": sanitize(self.final), "completion_evidence": "parent report plus actual spawn metadata"}


def rollout_children(home, parent):
    """Open only the exact current parent's rollout, never other session contents."""
    require(re.fullmatch(ID, parent) is not None, "invalid rollout parent")
    base = (Path(home) / "sessions").resolve()
    paths = list(base.glob(f"*/*/*/rollout-*-{parent}.jsonl"))
    require(len(paths) == 1, "exact parent rollout unavailable or ambiguous")
    path = paths[0]
    require(not path.is_symlink() and path.resolve().is_relative_to(base), "unsafe rollout path")
    total, children, calls, verified = 0, set(), set(), False
    with path.open("rb") as source:
        for number in range(MAX_EVENTS + 1):
            line = source.readline(MAX_LINE + 1)
            if not line:
                break
            total += len(line)
            require(number < MAX_EVENTS and len(line) <= MAX_LINE and total <= MAX_TOTAL, "rollout budget exceeded")
            try:
                event = json.loads(line)
            except ValueError:
                raise ValueError("malformed parent rollout") from None
            payload = event.get("payload", {})
            if event.get("type") == "session_meta":
                require(payload.get("id") == parent, "rollout parent mismatch")
                verified = True
            if event.get("type") == "response_item" and payload.get("type") == "function_call" and payload.get("name", "").split(".")[-1] == "spawn_agent":
                calls.add(payload.get("call_id"))
            if event.get("type") == "response_item" and payload.get("type") == "function_call_output" and payload.get("call_id") in calls:
                output = payload.get("output")
                try:
                    data = json.loads(output) if isinstance(output, str) else output
                except ValueError:
                    raise ValueError("unreadable spawn metadata") from None
                require(isinstance(data, dict), "invalid spawn output")
                child = data.get("agent_id")
                require(isinstance(child, str) and re.fullmatch(ID, child), "missing actual spawn ID")
                children.add(child)
    require(verified, "rollout identity not verified")
    return children


def unchanged(snapshots):
    for name, digest in snapshots.items():
        p = Path(name)
        require(sha(p.read_bytes() if p.exists() else b"") == digest,
                "configuration changed after preflight")


def preflight(executable, argv, env, marker, mode):
    lines = []
    code = bounded_process([executable, *argv, "debug", "prompt-input", "LifeOS preflight"],
                           env, "", 45, lines.append)
    require(code == 0, "prompt-input failed (raw output suppressed)")
    data = json.loads("".join(lines))
    developer = []

    def visit(x):
        if isinstance(x, dict):
            if x.get("role") == "developer":
                developer.append(json.dumps(x, ensure_ascii=False))
            for v in x.values():
                visit(v)
        elif isinstance(x, list):
            for v in x:
                visit(v)

    visit(data)
    texts = "\n".join(developer)
    require(marker in texts, "role marker missing from developer context")
    instruction_texts = []
    for item in developer:
        instruction_texts.extend(c.get("text", "") for c in json.loads(item).get("content", [])
                                 if isinstance(c, dict))
    injected = [t for t in instruction_texts if t.startswith(marker + "\n")]
    require(len(injected) == 1 and sha(injected[0].split("\n", 1)[1].encode()) == marker.split("sha256=")[1],
            "injected instruction hash mismatch")
    permissions = [t for t in developer if "<permissions instructions>" in t]
    require(len(permissions) == 1, "missing or ambiguous permissions block")
    text = permissions[0]
    require(f"`{mode}`" in text and "never" in text,
            "expected sandbox/approval missing from permissions")
    require("Approval policy is currently never" in text or "approval_policy" in text,
            "approval policy cannot be verified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", nargs="?", choices=ROLES)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--task-id")
    parser.add_argument("--task-file", type=Path)
    parser.add_argument("--approval-file", type=Path)
    args = parser.parse_args()
    before = baseline()
    if args.baseline:
        print(json.dumps({k: v for k, v in before.items() if k != "files"}))
        return 0
    require(args.role is not None, "role is required")
    require(args.check or args.task_id, "launch requires --task-id")
    require(args.task_id is None or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", args.task_id),
            "invalid task ID")
    task = ""
    if not args.check or args.role == "builder":
        require(not args.task_file or not credential_path(args.task_file), "credential task path forbidden")
        task = args.task_file.read_text(encoding="utf-8-sig") if args.task_file else sys.stdin.read(65537)
        require(task.strip() and len(task) <= 65536, "empty or oversized task")
    require(not args.approval_file or not credential_path(args.approval_file), "credential approval path forbidden")
    allowed = approval(args.approval_file, args.task_id, before, task) if args.role == "builder" else set()
    env = launch_env()
    executable = shutil.which("codex", path=env.get("PATH"))
    require(executable is not None, "codex executable not found")
    argv, snapshots, marker, mode, servers = configuration(env, args.role)
    snapshots[executable] = sha(Path(executable).read_bytes())
    if args.approval_file:
        snapshots[str(args.approval_file.resolve())] = sha(args.approval_file.read_bytes())
        approved = json.loads(args.approval_file.read_text(encoding="utf-8-sig"))
        proof = ROOT / safe_path(approved["reviewer_evidence"])
        snapshots[str(proof)] = sha(proof.read_bytes())
    argv = tuple(argv)
    env = MappingProxyType(env.copy())
    snapshots = MappingProxyType(snapshots.copy())
    preflight(executable, argv, env, marker, mode)
    unchanged(snapshots)
    require(baseline() == before, "workspace changed during preflight")
    if args.check:
        print(json.dumps({"check": "PASS", "role": args.role, "sandbox": mode,
                          "approval": "never", "marker": marker, "disabled_mcp_servers": servers}))
        return 0
    prompt = (f"Task {args.task_id}. Spawn exactly ONE ordinary child named {args.role}_execution "
              "with fork_turns all; no custom role selector and no further delegation. "
              "Child must quote a distinctive inherited developer instruction before reading role TOML. "
              "Only that child may write approved source files; parent waits and reports. "
              "Do not read credentials, commit, push or deploy. Major actions need user authorization. "
              "Parent final must contain actual child UUID, CHILD_COMPLETED only after child finishes, "
              "the child's substantive result/verdict, and exact inherited LIFEOS_ROLE marker. "
              "Do not replace child output with an acknowledgment. Approval is workflow evidence, not cryptographic authorization.\n"
              + "Exact approved files: " + json.dumps(sorted(allowed), ensure_ascii=False) + "\n" + task)
    unchanged(snapshots)
    require(baseline() == before, "workspace changed before launch")
    # Freeze approval and evidence too; validation cannot manufacture user authority.
    if args.role == "builder":
        require(approval(args.approval_file, args.task_id, before, task) == allowed, "approval changed")
    reader = EventReader()
    result, failure, code = {}, None, None
    try:
        code = bounded_process([executable, *argv, "exec", "--json", "-"], env, prompt, 600, reader.feed)
        if not reader.children and reader.parent:
            reader.children = rollout_children(env["CODEX_HOME"], reader.parent)
        result = reader.result(code)
        require(marker in reader.final, "parent final missing inherited role marker")
        unchanged(snapshots)
    except (ValueError, OSError, subprocess.SubprocessError, UnicodeError) as exc:
        failure = sanitize(str(exc) if isinstance(exc, ValueError) else type(exc).__name__, 1000)
    after = baseline()
    changed = sorted(p for p in before["files"].keys() | after["files"].keys()
                     if before["files"].get(p) != after["files"].get(p))
    outside = sorted(set(changed) - allowed)
    report = {"task_id": args.task_id, "role": args.role, "exit_code": code, "result": result, "error": failure,
              "head": after["head"], "baseline_sha256": before["baseline_sha256"],
              "changed_paths": changed[:200], "changed_count": len(changed),
              "out_of_scope": outside[:200], "out_of_scope_count": len(outside),
              "head_changed": before["head"] != after["head"], "marker": marker}
    evidence = ROOT / "work/team-launches"
    require(evidence.resolve().is_relative_to(ROOT), "evidence path escapes repository")
    require(subprocess.run(["git", "check-ignore", "-q", "work/team-launches/probe.json"], cwd=ROOT).returncode == 0,
            "audit directory must be ignored")
    evidence.mkdir(parents=True, exist_ok=True)
    import time
    target = evidence / (str(time.time_ns()) + ".json")
    with target.open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    require(not outside and not report["head_changed"], "out-of-scope changes detected; stop, report, never restore silently")
    require(failure is None, failure or "launch failed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError, UnicodeError) as exc:
        print("FAIL CLOSED: " + sanitize(str(exc) if isinstance(exc, ValueError) else type(exc).__name__, 1000), file=sys.stderr)
        sys.exit(2)
