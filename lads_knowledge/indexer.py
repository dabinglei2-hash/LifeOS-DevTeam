"""Explicit, local-only engineering sources. No discovery of user data or network IO."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

from .database import _reject_reparse_chain
from .safety import (KnowledgeError, canonical_json, normalize_sha, normalize_source_ref,
                     normalize_utc_iso, normalize_uuid, sanitize_text, utc_now_iso)
from .service import ConflictError, _query_rows, _query_one, _update

DEFAULT_DOCS = (
    "AGENTS.md", "README.md", "docs/PRD.md", "docs/ARCHITECTURE.md", "docs/STATUS.md",
    "docs/DEVELOPMENT_PROGRESS.md", "docs/CODEX_WORK_ENVIRONMENT.md",
    "docs/CODEX_TECH_STACK_AND_TOOLING.md", "docs/DATA_MODEL.md", "docs/AI_AGENT_SPEC.md",
    "docs/CHANGELOG.md", "docs/CODEX_MODEL_ROUTING.md", "docs/LADS_ORCHESTRATOR.md",
    "docs/LADS_KNOWLEDGE_DB.md", "docs/KNOWN_ISSUES.md", "docs/TASK_HISTORY.md",
    "docs/LADS_AUTOMATION.md",
    "docs/LADS_STAGE3_ACCEPTANCE_REPORT.md",
)
MAX_DOC_BYTES = 512 * 1024
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_STAGES = 32
CHUNK_SIZE = 6000
CHUNK_OVERLAP = 300
ROLES = {"scanner", "reviewer", "builder", "qa-tester"}
_MARKER = re.compile(r'^<!-- lads:(decision|issue) (\{.*\}) -->$')
_DENIED_PARTS = {"backups", "data-backups", "exports", "node_modules", "vendor", "sources",
                 "userdata", "user-data", "logs", ".git", ".venv", "__pycache__"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash64(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise KnowledgeError("Expected a SHA-256 digest")
    return value


def _json(data: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise KnowledgeError("Duplicate JSON key")
            result[key] = value
        return result
    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as exc:
        raise KnowledgeError("Invalid UTF-8 JSON source") from exc
    if not isinstance(result, dict):
        raise KnowledgeError("Source must be a JSON object")
    return result


def _git(root: Path, *args: str, limit: int = 2 * 1024 * 1024) -> bytes:
    """Fixed callers only, bounded pipes and timeout, no shell or network command."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    process = subprocess.Popen(["git", "--no-pager", "-c", "core.fsmonitor=false", "-C", str(root), *args],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    buffers = [bytearray(), bytearray()]
    exceeded = threading.Event()
    def drain(stream, index):
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            if len(buffers[index]) + len(chunk) > limit:
                exceeded.set()
                process.kill()
                break
            buffers[index].extend(chunk)
    readers = [threading.Thread(target=drain, args=(stream, index), daemon=True)
               for index, stream in enumerate((process.stdout, process.stderr))]
    for thread in readers:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    for thread in readers:
        thread.join(timeout=2)
    process.stdout.close()
    process.stderr.close()
    if process.returncode or exceeded.is_set() or timed_out:
        raise KnowledgeError("Git source unavailable or exceeded its bounds")
    return bytes(buffers[0])


def repository_ref(root: str | Path) -> str:
    selected = Path(root)
    _reject_reparse_chain(selected.absolute())
    resolved = selected.resolve(strict=True)
    top = Path(_git(resolved, "rev-parse", "--show-toplevel", limit=4096).decode("utf-8").strip()).resolve(strict=True)
    if top != resolved:
        raise KnowledgeError("Select the exact Git worktree root")
    value = os.path.normcase(str(resolved)).replace("\\", "/")
    return "repositories/" + sha256(value.encode("utf-8"))


def _read_regular(root: Path, relative: str, maximum: int) -> bytes:
    ref = normalize_source_ref("path", relative)
    path = root / relative
    _reject_reparse_chain(path.absolute())
    if not path.resolve(strict=False).is_relative_to(root.resolve(strict=True)):
        raise KnowledgeError("Source escaped its approved directory")
    before = path.stat()
    if not path.is_file() or before.st_size > maximum or before.st_nlink != 1:
        raise KnowledgeError("Source is not a bounded regular file")
    with path.open("rb") as handle:
        data = handle.read(maximum + 1)
        opened = os.fstat(handle.fileno())
    after = path.stat()
    if len(data) > maximum or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
        raise KnowledgeError("Source changed while being read")
    return data


def _code_path(path: str) -> str:
    result = normalize_source_ref("path", path)
    if set(result.split("/")) & _DENIED_PARTS or any(part.startswith(".env") for part in result.split("/")):
        raise KnowledgeError("Private paths are not engineering metadata sources")
    if Path(result).suffix not in {".md", ".py", ".js", ".mjs", ".css", ".html", ".toml", ".json", ".yml", ".yaml", ".txt", ".webmanifest"}:
        raise KnowledgeError("Unsupported engineering metadata path")
    if any(word in Path(result).name for word in ("auth.json", "credentials", "financial-record", "health-record", "backup")):
        raise KnowledgeError("Private metadata filename")
    return result


class SourcePolicy:
    def __init__(self, documents=DEFAULT_DOCS):
        approved = {normalize_source_ref("path", item): item for item in DEFAULT_DOCS}
        selected = [normalize_source_ref("path", item) for item in documents]
        if not selected or len(selected) > len(DEFAULT_DOCS) or len(set(selected)) != len(selected) or not set(selected) <= approved.keys():
            raise KnowledgeError("Document policy must be an explicit subset of the formal allowlist")
        self.documents = {item: approved[item] for item in selected}

    def collect_documents(self, root: str | Path) -> dict:
        binding = repository_ref(root)
        root = Path(root).resolve(strict=True)
        head = normalize_sha(_git(root, "rev-parse", "HEAD", limit=256).decode().strip())
        entries = []
        for canonical, actual in self.documents.items():
            try:
                raw = _read_regular(root, actual, MAX_DOC_BYTES)
                text = raw.decode("utf-8")
                committed = None
                try:
                    blob = _git(root, "cat-file", "blob", head + ":" + actual, limit=MAX_DOC_BYTES)
                    # Git commonly stores LF while Windows checks out CRLF. Verify
                    # complete text equivalence with only that line-ending change;
                    # the separately recorded raw SHA always hashes actual file bytes.
                    if blob.replace(b"\r\n", b"\n") == raw.replace(b"\r\n", b"\n"):
                        committed = head
                except KnowledgeError:
                    pass  # Missing/oversize committed blob is conservatively unattributed.
                entries.append({"path": canonical, "status": "present", "text": text,
                                "raw_sha": sha256(raw), "committed_sha": committed, "dirty": committed is None})
            except FileNotFoundError:
                entries.append({"path": canonical, "status": "missing"})
            except (OSError, UnicodeError, KnowledgeError):
                entries.append({"path": canonical, "status": "rejected"})
        if _git(root, "rev-parse", "HEAD", limit=256).decode().strip() != head:
            raise ConflictError("HEAD changed while collecting documents")
        return {"root_ref": binding, "observed_head": head, "entries": entries}


def _markers(text: str) -> list[dict]:
    result, fence = [], None
    for line in text.splitlines():
        opening = re.match(r"^\s{0,3}(`{3,}|~{3,})(.*)$", line)
        if opening:
            token, rest = opening.groups()
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not rest.strip():
                fence = None
            continue
        if fence is not None:
            continue
        match = _MARKER.fullmatch(line.strip())
        if not match:
            if line.strip().startswith("<!-- lads:"):
                raise KnowledgeError("Malformed engineering marker")
            continue
        if len(line) > 4000:
            raise KnowledgeError("Engineering marker exceeds bounds")
        kind, raw = match.groups()
        marker = _json(raw.encode("utf-8"))
        required = {"id", "title", "status"} | ({"severity"} if kind == "issue" else set())
        if not required <= marker.keys() or not marker.keys() <= required | {"text"}:
            raise KnowledgeError("Engineering marker schema mismatch")
        if not isinstance(marker["id"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", marker["id"]):
            raise KnowledgeError("Invalid marker ID")
        states = {"accepted", "proposed", "deprecated"} if kind == "decision" else {"open", "resolved"}
        if marker["status"] not in states or (kind == "issue" and marker["severity"] not in {"low", "medium", "high", "critical"}):
            raise KnowledgeError("Invalid marker state or severity")
        if not isinstance(marker["title"], str) or not 1 <= len(marker["title"]) <= 300 or not isinstance(marker.get("text", ""), str) or len(marker.get("text", "")) > 2000:
            raise KnowledgeError("Marker text exceeds bounds")
        result.append({"kind": kind, **marker})
    return result


class KnowledgeIndexer:
    def __init__(self, store, project_id: str, *, documents=DEFAULT_DOCS):
        self.store = store
        self.project_id = normalize_uuid(project_id)
        self.policy = SourcePolicy(documents)

    def _bound(self, connection, root_ref: str):
        project = self.store._project(connection, self.project_id)
        if not project["root_ref"] or project["root_ref"] != root_ref:
            raise ConflictError("Source does not match the explicitly bound project root")

    def _verify_root(self, root):
        ref = repository_ref(root)
        with self.store.batch():
            self._bound(self.store._local.connection, ref)
        return ref

    def _upsert(self, connection, *, kind, source_ref, title, body, **provenance):
        source_ref = normalize_source_ref(kind, source_ref)
        row = _query_one(connection, "SELECT * FROM knowledge_items WHERE project_id=:p AND kind=:k AND source_ref=:s",
                         {"p": self.project_id, "k": kind, "s": source_ref})
        clean_title = sanitize_text(title, limit=300, allow_empty=False)
        clean_body = sanitize_text(body, limit=8000)
        if row and row["status"] == "active" and row["title"] == clean_title and row["body"] == clean_body and all(row[key] == value for key, value in provenance.items()):
            return False
        self.store.upsert_knowledge(self.project_id, kind=kind, source_ref=source_ref, title=clean_title, body=clean_body, **provenance)
        return True

    def sync_documents(self, root: str | Path) -> dict:
        self._verify_root(root)
        return self.index_documents(**self.policy.collect_documents(root))

    def index_documents(self, *, root_ref: str, observed_head: str, entries: list[dict]) -> dict:
        head = normalize_sha(observed_head)
        if head is None:
            raise KnowledgeError("An observed source HEAD is required")
        if not isinstance(entries, list) or len(entries) != len(self.policy.documents):
            raise KnowledgeError("A complete declared document snapshot is required")
        prepared, seen, marker_ids = [], set(), set()
        for entry in entries:
            if not isinstance(entry, dict) or not {"path", "status"} <= entry.keys():
                raise KnowledgeError("Document entry schema mismatch")
            path = normalize_source_ref("path", entry["path"])
            state = entry["status"]
            if path in seen or path not in self.policy.documents or state not in {"present", "missing", "rejected"}:
                raise KnowledgeError("Unexpected, duplicate or invalid document entry")
            seen.add(path)
            item = {"path": path, "status": state, "markers": [], "chunks": []}
            if state == "present":
                if set(entry) != {"path", "status", "text", "raw_sha", "committed_sha", "dirty"} or not isinstance(entry["text"], str) or type(entry["dirty"]) is not bool:
                    raise KnowledgeError("Present document schema mismatch")
                raw = entry["text"].encode("utf-8")
                if len(raw) > MAX_DOC_BYTES or sha256(raw) != _hash64(entry["raw_sha"]):
                    raise KnowledgeError("Document size or raw digest mismatch")
                committed = normalize_sha(entry["committed_sha"])
                if (committed is not None and (committed != head or entry["dirty"])) or (not entry["dirty"] and committed is None):
                    raise KnowledgeError("Inconsistent committed/dirty provenance")
                clean = sanitize_text(entry["text"].replace("\r\n", "\n"), limit=2 * MAX_DOC_BYTES)
                chunks = [clean[index:index + CHUNK_SIZE] for index in range(0, len(clean), CHUNK_SIZE - CHUNK_OVERLAP)] or [""]
                if len(chunks) > 128:
                    raise KnowledgeError("Document has too many chunks")
                try:
                    item["markers"] = _markers(entry["text"])
                except KnowledgeError:
                    item["status"] = "rejected"
                if item["status"] == "present":
                    item.update(chunks=chunks, provenance={"source_raw_sha": entry["raw_sha"], "observed_head": head,
                                "actual_content_commit_sha": committed, "dirty": entry["dirty"]})
                    for marker in item["markers"]:
                        key = (marker["kind"], marker["id"])
                        if key in marker_ids:
                            raise ConflictError("Duplicate marker ID across the declared document snapshot")
                        marker_ids.add(key)
            elif set(entry) != {"path", "status"}:
                raise KnowledgeError("Missing/rejected entry has unexpected fields")
            prepared.append(item)
        stats = {"documents": len(prepared), "updated_chunks": 0, "unchanged_chunks": 0, "tombstoned": 0, "errors": []}
        with self.store.batch():
            connection = self.store._local.connection
            self._bound(connection, root_ref)
            for item in prepared:
                path, wanted = item["path"], set()
                if item["status"] == "present":
                    for number, body in enumerate(item["chunks"], 1):
                        ref = path if number == 1 else f"{path}.chunks/{number:06d}"
                        wanted.add(ref)
                        changed = self._upsert(connection, kind="document", source_ref=ref, title=path + (f" ({number})" if number > 1 else ""), body=body, **item["provenance"])
                        stats["updated_chunks" if changed else "unchanged_chunks"] += 1
                elif item["status"] == "rejected":
                    stats["errors"].append({"source_file": path, "error": "source_rejected"})
                rows = _query_rows(connection, "SELECT * FROM knowledge_items WHERE project_id=:p AND kind='document' AND status='active'", {"p": self.project_id})
                for row in rows:
                    if (row["source_ref"] == path or row["source_ref"].startswith(path + ".chunks/")) and row["source_ref"] not in wanted:
                        self.store.tombstone_knowledge(self.project_id, kind="document", source_ref=row["source_ref"])
                        stats["tombstoned"] += 1
                self._reconcile_markers(connection, path, item)
        return stats

    def _reconcile_markers(self, connection, path, item):
        wanted = set()
        for marker in item["markers"] if item["status"] == "present" else []:
            kind, identity = marker["kind"], marker["id"]
            ref = f"{path}/.lads-{kind}/{identity}"
            table = "architecture_decisions" if kind == "decision" else "known_issues"
            for old in _query_rows(connection, f"SELECT source_ref FROM {table} WHERE project_id=:p", {"p": self.project_id}):
                if old["source_ref"].endswith(f"/.lads-{kind}/{identity}") and old["source_ref"] != ref:
                    raise ConflictError("Marker identity is already owned by another document")
            wanted.add(ref)
            args = dict(source_ref=ref, title=marker["title"], status=marker["status"], **item["provenance"])
            content_key = "decision_text" if kind == "decision" else "details"
            text_value = sanitize_text(marker.get("text") or marker["title"], limit=8000)
            previous = _query_one(connection, f"SELECT * FROM {table} WHERE project_id=:p AND source_ref=:s", {"p": self.project_id, "s": ref})
            expected = {content_key: text_value, "title": sanitize_text(marker["title"], limit=300), "status": marker["status"], **item["provenance"]}
            if kind == "issue":
                expected["severity"] = marker["severity"]
            if not previous or any(previous[key] != value for key, value in expected.items()):
                if kind == "decision":
                    self.store.upsert_decision(self.project_id, decision_text=text_value, **args)
                else:
                    self.store.upsert_issue(self.project_id, details=text_value, severity=marker["severity"], **args)
            self._upsert(connection, kind=kind, source_ref=ref, title=marker["title"], body=canonical_json(marker), **item["provenance"])
        for kind, table in (("decision", "architecture_decisions"), ("issue", "known_issues")):
            prefix = path + f"/.lads-{kind}/"
            for old in _query_rows(connection, f"SELECT id,source_ref,status FROM {table} WHERE project_id=:p", {"p": self.project_id}):
                if old["source_ref"].startswith(prefix) and old["source_ref"] not in wanted and old["status"] != "inactive":
                    _update(connection, table, {"status": "inactive", "updated_at": utc_now_iso()}, {"id": old["id"], "project_id": self.project_id})
                    self.store.tombstone_knowledge(self.project_id, kind=kind, source_ref=old["source_ref"])

    def sync_git(self, root: str | Path, *, limit: int = 30) -> dict:
        binding = self._verify_root(root)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise KnowledgeError("Git history limit must be 1..100")
        root = Path(root).resolve(strict=True)
        records = []
        for line in _git(root, "log", f"-{limit}", "--format=%H%x09%cI%x09%s").decode("utf-8").splitlines():
            commit, date, title = line.split("\t", 2)
            paths = []
            raw_paths = _git(root, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", commit).split(b"\0")
            for raw in raw_paths:
                if raw:
                    try:
                        paths.append(_code_path(raw.decode("utf-8")))
                    except KnowledgeError:
                        continue
            records.append({"sha": commit, "date": date, "title": title, "files": paths[:200], "files_truncated": len(paths) > 200})
        return self.index_git_records(records, root_ref=binding)

    def index_git_records(self, records: list[dict], *, root_ref: str) -> dict:
        if not isinstance(records, list) or len(records) > 100:
            raise KnowledgeError("Git metadata batch exceeds bounds")
        prepared = []
        for row in records:
            if set(row) != {"sha", "date", "title", "files", "files_truncated"} or not isinstance(row["title"], str) or len(row["title"]) > 2000 or not isinstance(row["files"], list) or len(row["files"]) > 200:
                raise KnowledgeError("Git metadata schema mismatch")
            prepared.append({"sha": normalize_sha(row["sha"]), "date": normalize_utc_iso(row["date"]), "title": sanitize_text(row["title"], limit=300), "files": [_code_path(item) for item in row["files"]], "files_truncated": bool(row["files_truncated"])})
        count = 0
        with self.store.batch():
            connection = self.store._local.connection
            self._bound(connection, root_ref)
            for row in prepared:
                body = canonical_json(row)
                while len(body) > 7600 and row["files"]:
                    row["files"].pop()
                    row["files_truncated"] = True
                    body = canonical_json(row)
                count += self._upsert(connection, kind="git_commit", source_ref="git/commits/" + row["sha"], title=row["title"] or row["sha"], body=body,
                                      source_raw_sha=sha256(body.encode()), observed_head=None, actual_content_commit_sha=row["sha"], dirty=False)
        return {"observed": len(prepared), "updated": count, "deletion_inferred": False}

    def index_pr_snapshot(self, snapshot: dict, *, repository: str) -> dict:
        if set(snapshot) != {"repository", "repository_id", "observed_at", "complete", "items"} or snapshot["repository"] != repository or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise KnowledgeError("PR repository snapshot mismatch")
        if type(snapshot["repository_id"]) is not int or snapshot["repository_id"] < 1 or type(snapshot["complete"]) is not bool or not isinstance(snapshot["items"], list) or len(snapshot["items"]) > 100:
            raise KnowledgeError("PR snapshot exceeds bounds")
        normalize_utc_iso(snapshot["observed_at"])
        prepared, seen = [], set()
        for row in snapshot["items"]:
            required = {"number", "title", "state", "url", "head_sha", "base_sha", "merge_sha", "created_at", "updated_at"}
            if set(row) != required or type(row["number"]) is not int or row["number"] < 1 or row["number"] in seen or row["state"] not in {"OPEN", "CLOSED", "MERGED"} or not isinstance(row["title"], str) or len(row["title"]) > 1000:
                raise KnowledgeError("PR metadata schema mismatch")
            seen.add(row["number"])
            url = normalize_source_ref("url", row["url"])
            if url != f"https://github.com/{repository}/pull/{row['number']}":
                raise KnowledgeError("PR URL does not match its declared repository")
            prepared.append({**row, "title": sanitize_text(row["title"], limit=300), "url": url,
                             **{key: normalize_sha(row[key]) for key in ("head_sha", "base_sha", "merge_sha")},
                             **{key: normalize_utc_iso(row[key]) for key in ("created_at", "updated_at")}})
        count = 0
        with self.store.batch():
            connection = self.store._local.connection
            if not self.store._project(connection, self.project_id)["root_ref"]:
                raise ConflictError("Bind the project before importing PR metadata")
            for row in prepared:
                body = canonical_json({"repository_id": snapshot["repository_id"], **row})
                count += self._upsert(connection, kind="pull_request", source_ref=row["url"], title=row["title"], body=body,
                                      source_raw_sha=sha256(body.encode()), observed_head=None, actual_content_commit_sha=None, dirty=False)
        return {"observed": len(prepared), "updated": count, "deletion_inferred": False}

    def sync_pr_snapshot(self, path: str | Path, *, repository: str) -> dict:
        selected = Path(path).absolute()
        if not re.fullmatch(r"pr[-_]metadata[^/\\]*\.json", selected.name):
            raise KnowledgeError("Select a named pr-metadata JSON snapshot")
        return self.index_pr_snapshot(_json(_read_regular(selected.parent, selected.name, MAX_DOC_BYTES)), repository=repository)

    def sync_handoff(self, root: str | Path, path: str | Path, *, requirement_summary: str) -> dict:
        binding = self._verify_root(root)
        selected = Path(path).absolute()
        if selected.name != "finalhandoff.json":
            raise KnowledgeError("Select finalhandoff.json in its task UUID directory")
        task_id = normalize_uuid(selected.parent.name)
        raw = _read_regular(selected.parent, selected.name, MAX_REPORT_BYTES)
        handoff = _json(raw)
        if handoff.get("task_id") != task_id:
            raise KnowledgeError("Handoff directory/task mismatch")
        head = normalize_sha(handoff.get("source_head"))
        if head is None:
            raise KnowledgeError("Handoff source commit is required")
        _git(Path(root), "cat-file", "-e", head + "^{commit}", limit=4096)
        stages = {}
        references = handoff.get("stage_reports", [])
        if not isinstance(references, list) or not 1 <= len(references) <= MAX_STAGES:
            raise KnowledgeError("Stage reference count exceeds bounds")
        total = 0
        for item in references:
            if not isinstance(item, dict):
                raise KnowledgeError("Stage reference must be an object")
            relative = item.get("path", "")
            if not re.fullmatch(r"stages/[0-9]{4}-(?:scanner|reviewer|builder|qa-tester)-build-[0-9]+-attempt-[0-9]+\.json", relative) or relative in stages:
                raise KnowledgeError("Invalid or duplicate stage reference")
            payload = _read_regular(selected.parent, relative, MAX_REPORT_BYTES)
            total += len(payload)
            if total > 16 * MAX_REPORT_BYTES:
                raise KnowledgeError("Stage report batch exceeds bounds")
            stages[relative] = payload
        result = self.index_handoff(handoff, stages, root_ref=binding, requirement_summary=requirement_summary, raw_sha=sha256(raw))
        return {**result, "source_file": str(selected), "handoff_raw_sha256": sha256(raw)}

    def index_handoff(self, handoff: dict, stage_bytes: dict[str, bytes], *, root_ref: str,
                      requirement_summary: str, raw_sha: str | None = None) -> dict:
        task = normalize_uuid(handoff["task_id"])
        head = normalize_sha(handoff["source_head"])
        if head is None or len(canonical_json(handoff).encode("utf-8")) > MAX_REPORT_BYTES:
            raise KnowledgeError("A bounded handoff with source commit is required")
        references = handoff.get("stage_reports")
        if not isinstance(references, list) or not 1 <= len(references) <= MAX_STAGES or len(stage_bytes) != len(references):
            raise KnowledgeError("Complete hashed stages are required")
        parsed, seen = [], set()
        for reference in references:
            if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
                raise KnowledgeError("Stage reference schema mismatch")
            path = reference["path"]
            if not re.fullmatch(r"stages/[0-9]{4}-(?:scanner|reviewer|builder|qa-tester)-build-[0-9]+-attempt-[0-9]+\.json", path) or path in seen:
                raise KnowledgeError("Invalid stage path")
            seen.add(path)
            payload = stage_bytes.get(path)
            if not isinstance(payload, bytes) or len(payload) > MAX_REPORT_BYTES or sha256(payload) != _hash64(reference["sha256"]):
                raise ConflictError("Stage digest mismatch")
            stage = _json(payload)
            if stage.get("task_id") != task or stage.get("role") not in ROLES or type(stage.get("build")) is not int or stage["build"] < 0 or type(stage.get("attempt")) is not int or stage["attempt"] < 1:
                raise KnowledgeError("Stage identity mismatch")
            report = stage.get("report")
            if report is not None and (not isinstance(report, dict) or report.get("task_id") != task or report.get("role") != stage["role"]):
                raise KnowledgeError("Stage report binding mismatch")
            parsed.append((path, stage))
        digest = sha256(canonical_json({"handoff": _hash64(raw_sha) if raw_sha else sha256(canonical_json(handoff).encode()), "stages": references}).encode())
        origin = "handoffs/" + task
        tests = handoff.get("tests", [])
        files = handoff.get("files", [])
        runs = handoff.get("role_runs", [])
        if not isinstance(tests, list) or len(tests) > 64 or not isinstance(files, list) or len(files) > 200 or not isinstance(runs, list) or len(runs) > MAX_STAGES:
            raise KnowledgeError("Handoff payload exceeds bounds")
        if any(not isinstance(item, dict) for item in runs):
            raise KnowledgeError("Agent run metadata must be an object")
        outcome = sanitize_text(handoff.get("task_status"), limit=100, allow_empty=False)
        def identity(suffix):
            return str(uuid.uuid5(uuid.UUID(self.project_id), task + "/" + suffix))
        with self.store.batch():
            connection = self.store._local.connection
            self._bound(connection, root_ref)
            existing = _query_one(connection, "SELECT source_raw_sha FROM knowledge_items WHERE project_id=:p AND kind='handoff' AND source_ref=:s", {"p": self.project_id, "s": origin})
            if existing:
                if existing["source_raw_sha"] != digest:
                    raise ConflictError("An immutable handoff snapshot already exists for this task")
                current_task = self.store._task(connection, self.project_id, task)
                if current_task["requirement_summary"] != sanitize_text(requirement_summary, limit=8000, allow_empty=False):
                    raise ConflictError("Existing task summary differs from this import request")
                return {"task_id": task, "status": "unchanged", "runs": len(parsed)}
            self.store.create_task(self.project_id, requirement_summary, task_id=task, assigned_agent="LADS")
            self.store.transition_task(self.project_id, task, "active", event_id=identity("start"))
            qa_runs = {}
            for index, (path, stage) in enumerate(parsed):
                metadata = runs[index] if index < len(runs) and runs[index].get("role") == stage["role"] else stage.get("metadata", {})
                if not isinstance(metadata, dict):
                    raise KnowledgeError("Stage metadata must be an object")
                if any(metadata.get(key) is not None and (type(metadata[key]) is not int or metadata[key] < 0) for key in ("input_tokens", "output_tokens")):
                    raise KnowledgeError("Reported token counts must be nonnegative integers or null")
                provider = metadata.get("provider", "unknown")
                model = metadata.get("model", "unknown")
                if provider not in {"lifeos_deepseek", "deepseek", "unknown"} or not isinstance(model, str) or not re.fullmatch(r"deepseek-[a-zA-Z0-9._-]{1,70}|unknown", model):
                    raise KnowledgeError("Unsupported handoff model/provider metadata")
                run_id = identity(path)
                start = normalize_utc_iso(stage["start_time"])
                end = normalize_utc_iso(stage["end_time"]) if stage.get("end_time") else None
                self.store.record_run(self.project_id, task, role=stage["role"], model_name=model, build_number=stage["build"] + 1,
                                      attempt=stage["attempt"], status=stage["status"], started_at=start, completed_at=end,
                                      input_tokens=metadata.get("input_tokens"), output_tokens=metadata.get("output_tokens"), run_id=run_id)
                self.store.record_usage(self.project_id, task, run_id, model_name=model, input_tokens=metadata.get("input_tokens"), output_tokens=metadata.get("output_tokens"), usage_id=identity(path + "/usage"))
                self.store.record_event(self.project_id, task, "agent_stage", {"role": stage["role"], "provider": provider, "model": model,
                                        "original_build": stage["build"], "original_attempt": stage["attempt"], "outcome": stage["status"],
                                        "exit_code": stage.get("exit_code"), "source_ref": origin + "/" + path, "source_sha": sha256(stage_bytes[path])},
                                        event_id=identity(path + "/event"), occurred_at=end or start)
                if stage["role"] == "qa-tester":
                    qa_runs[stage["build"]] = (run_id, start, end)
            failed = 0
            for index, test in enumerate(tests):
                if not isinstance(test, dict) or not {"build", "command", "status", "exit_code"} <= test.keys() or type(test["exit_code"]) is not int or not isinstance(test["command"], str) or len(test["command"]) > 1000:
                    raise KnowledgeError("Test metadata schema mismatch")
                failed += test["exit_code"] != 0
                linked = qa_runs.get(test["build"], (None, normalize_utc_iso(handoff["start_time"]), None))
                summary = canonical_json({key: test[key] for key in ("build", "command", "status", "exit_code")})
                ref = origin + f"/tests/{index + 1}"
                self.store.record_test(self.project_id, task, test_name=f"QA build {test['build']}", command=test["command"], status="failed" if test["exit_code"] else "passed",
                                       summary=summary, started_at=linked[1], completed_at=linked[2], run_id=linked[0], test_id=identity(ref))
                self._upsert(connection, kind="test_result", source_ref=ref, title=f"{'failed' if test['exit_code'] else 'passed'} QA build {test['build']}",
                             body=summary, task_id=task, source_raw_sha=digest, observed_head=head, actual_content_commit_sha=None, dirty=False)
            for index, change in enumerate(files):
                if not isinstance(change, dict) or not {"path", "change", "after_sha256"} <= change.keys():
                    raise KnowledgeError("Change metadata schema mismatch")
                self.store.record_change(self.project_id, task, file_path=_code_path(change["path"]), change_type=change["change"],
                                         source_ref=origin + f"/changes/{index + 1}", source_raw_sha=normalize_sha(change["after_sha256"]),
                                         observed_head=head, dirty=True, change_id=identity("change/" + str(index)))
            review = handoff.get("reviewer_final") or {}
            self.store.update_task(self.project_id, task, commit_sha=head, test_result=f"failed={failed};total={len(tests)}", review_result=review.get("status"))
            if outcome == "AWAITING_MAIN_ACCEPTANCE":
                self.store.transition_task(self.project_id, task, "testing", event_id=identity("testing"))
                self.store.transition_task(self.project_id, task, "review", event_id=identity("review"))
            elif outcome == "BLOCKED":
                self.store.transition_task(self.project_id, task, "blocked", event_id=identity("blocked"))
            self.store.record_event(self.project_id, task, "source_outcome", {"outcome": outcome, "source_ref": origin, "source_sha": digest, "main_acceptance_inferred": False}, event_id=identity("outcome"), occurred_at=normalize_utc_iso(handoff["end_time"]))
            self._upsert(connection, kind="handoff", source_ref=origin, title=f"LADS {task} {outcome}", body=canonical_json({"outcome": outcome, "runs": len(parsed), "tests": len(tests), "failed_tests": failed, "main_acceptance_inferred": False}),
                         task_id=task, source_raw_sha=digest, observed_head=head, actual_content_commit_sha=None, dirty=False)
        return {"task_id": task, "status": "indexed", "runs": len(parsed), "tests": len(tests), "failed_tests": failed, "source_outcome": outcome}
