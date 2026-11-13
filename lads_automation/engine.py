"""Event-driven documentation, index synchronization, and delivery gating."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

from lads_knowledge.database import _is_reparse, _reject_reparse_chain
from lads_knowledge.indexer import (
    DEFAULT_DOCS,
    MAX_DOC_BYTES,
    KnowledgeIndexer,
    _git,
    repository_ref,
)
from lads_knowledge.safety import (
    canonical_json,
    normalize_sha,
    sanitize_metadata,
    sanitize_text,
    utc_now_iso,
)

from .evidence import (
    EvidenceError,
    EvidenceProof,
    QAEvidenceError,
    ReviewEvidenceError,
    TaskSpec,
)
from .state import (
    JournalConflict,
    JournalError,
    JournalNotFound,
    default_automation_root,
)


class DeliveryError(RuntimeError):
    """The hard delivery gate rejected an operation."""


class WorkflowState(StrEnum):
    TASK_CREATED = "TASK_CREATED"
    QA_REVIEW_VERIFIED = "QA_REVIEW_VERIFIED"
    DOCS_PENDING = "DOCS_PENDING"
    DOCS_SYNCED = "DOCS_SYNCED"
    DOCS_FAILED = "DOCS_FAILED"
    INDEX_PENDING = "INDEX_PENDING"
    INDEX_FAILED = "INDEX_FAILED"
    DELIVERY_READY = "DELIVERY_READY"
    QA_FAILED = "QA_FAILED"
    REVIEW_FAILED = "REVIEW_FAILED"
    BLOCKED = "BLOCKED"


EVENT_DOCUMENTS: dict[str, str] = {
    "requirements_changed": "docs/PRD.md",
    "architecture_changed": "docs/ARCHITECTURE.md",
    "data_model_changed": "docs/DATA_MODEL.md",
    "agent_rules_changed": "docs/AI_AGENT_SPEC.md",
    "toolchain_changed": "docs/CODEX_TECH_STACK_AND_TOOLING.md",
    "workflow_changed": "docs/CODEX_WORK_ENVIRONMENT.md",
    "task_state_changed": "docs/DEVELOPMENT_PROGRESS.md",
    "release_changed": "docs/CHANGELOG.md",
    "issue_found": "docs/KNOWN_ISSUES.md",
    "task_delivered": "docs/TASK_HISTORY.md",
}

KNOWN_DOCUMENTS = frozenset(DEFAULT_DOCS)
_SEMANTIC_EVENTS = frozenset(EVENT_DOCUMENTS) - {
    "task_state_changed",
    "release_changed",
    "issue_found",
    "task_delivered",
}
_ALLOWED_EVENT_FIELDS = frozenset(
    {"summary", "reason", "details", "component", "severity", "doc_approval"}
)
_EVENT_MARKER_RE = re.compile(
    r"^<!-- lads-automation:event (\{.*\}) -->\r?$",
    re.MULTILINE,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_RETRY_ATTEMPTS = 3


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stable_uuid(task_id: str, *parts: str) -> str:
    return str(uuid.uuid5(uuid.UUID(task_id), "|".join(parts)))


def _bounded_text(value: Any, label: str, *, default: str = "") -> str:
    text = default if value is None else str(value)
    text = text.strip()
    if len(text) > 4000 or "\x00" in text:
        raise DeliveryError(f"{label} exceeds its bounded text limit")
    return sanitize_text(text, limit=4000)


def _render_metadata_value(value: Any, label: str) -> str:
    rendered = json.dumps(_bounded_text(value, label), ensure_ascii=True)
    return (
        rendered.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("`", "\\u0060")
    )


def _validate_event_payload(event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if event_type not in EVENT_DOCUMENTS:
        raise DeliveryError("Event type is not in the fixed event-to-document map")
    if not isinstance(payload, Mapping):
        raise DeliveryError("Event payload must be an object")
    if set(payload) - _ALLOWED_EVENT_FIELDS:
        raise DeliveryError("Event payload contains fields outside the allowlist")
    clean = {key: payload[key] for key in payload}
    for key in ("summary", "reason", "details", "component"):
        if key in clean:
            if not isinstance(clean[key], str):
                raise DeliveryError(f"{key} must be a string")
            clean[key] = _bounded_text(clean[key], key)
    if "severity" in clean:
        if not isinstance(clean["severity"], str):
            raise DeliveryError("Issue severity must be a string")
        if clean["severity"].casefold() not in {"low", "medium", "high", "critical"}:
            raise DeliveryError("Issue severity is not recognized")
    target = EVENT_DOCUMENTS[event_type]
    if event_type in _SEMANTIC_EVENTS:
        approval = clean.get("doc_approval")
        if not isinstance(approval, Mapping) or set(approval) != {
            "path",
            "approved",
            "document_sha256",
        }:
            raise DeliveryError("Semantic events require an exact approved document acknowledgement")
        if approval.get("path") != target or approval.get("approved") is not True:
            raise DeliveryError("Document acknowledgement path or approval flag is invalid")
        digest = str(approval.get("document_sha256") or "").lower()
        if not _SHA_RE.fullmatch(digest):
            raise DeliveryError("Approved document digest is invalid")
        clean["doc_approval"] = {
            "path": target,
            "approved": True,
            "document_sha256": digest,
        }
    safe = sanitize_metadata(clean)
    if len(canonical_json(safe).encode("utf-8")) > 16 * 1024:
        raise DeliveryError("Event metadata exceeds its safe bound")
    return safe


def _event_entries(events: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in events:
        event_type = row.get("event_type")
        document = EVENT_DOCUMENTS.get(event_type)
        if document is None:
            continue
        result.setdefault(document, []).append(
            {
                "event_id": row["id"],
                "event_type": event_type,
                "payload": row["payload"],
                "payload_sha256": row["payload_sha256"],
                "occurred_at": row["occurred_at"],
            }
        )
    return result


def _entry_block(
    task_id: str,
    document: str,
    entries: Sequence[dict[str, Any]],
    evidence: EvidenceProof,
) -> bytes:
    lines = [""]
    proof = evidence.as_dict()
    proof_json = canonical_json(
        {
            "qa_sha256": proof["qa_sha256"],
            "reviewer_sha256": proof["reviewer_sha256"],
            "controller_sha256": proof["controller_sha256"],
            "candidate_sha256": proof["candidate_sha256"],
            "simulated": proof["simulated"],
            "acceptance_mode": proof["acceptance_mode"],
            "authorization_sha256": proof["authorization_sha256"],
        }
    )
    for entry in entries:
        marker = canonical_json(
            {
                "id": entry["event_id"],
                "doc": document,
                "payload_sha256": entry["payload_sha256"],
            }
        )
        lines.extend(
            [
                f"<!-- lads-automation:event {marker} -->",
                f"### LADS event {entry['event_id']}",
                "",
                f"- Task: `{task_id}`",
                f"- Event: `{entry['event_type']}`",
                f"- Recorded: `{entry['occurred_at']}`",
                f"- QA/review verified: `true` (machine delivery receipt `{proof_json}`)",
            ]
        )
        payload = entry["payload"]
        for key in ("summary", "reason", "details", "component", "severity"):
            if payload.get(key):
                lines.append(f"- {key}: {_render_metadata_value(payload[key], key)}")
        lines.append("<!-- /lads-automation:event -->")
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def _existing_markers(data: str) -> dict[str, dict[str, Any]]:
    markers: dict[str, dict[str, Any]] = {}
    for match in _EVENT_MARKER_RE.finditer(data):
        try:
            parsed = json.loads(match.group(1))
        except ValueError as exc:
            raise DeliveryError("Existing automation event marker is malformed") from exc
        if (
            not isinstance(parsed, dict)
            or set(parsed) != {"id", "doc", "payload_sha256"}
            or not _SHA_RE.fullmatch(str(parsed.get("payload_sha256", "")))
        ):
            raise DeliveryError("Existing automation event marker schema is invalid")
        identity = str(parsed.get("id"))
        try:
            if str(uuid.UUID(identity)) != identity:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise DeliveryError("Existing automation event marker ID is invalid") from exc
        if identity in markers:
            raise DeliveryError("Existing automation event ID is duplicated or conflicting")
        markers[identity] = parsed
    return markers


def _safe_document(root: Path, document: str) -> Path:
    if document not in KNOWN_DOCUMENTS:
        raise DeliveryError("Document path is outside the fixed known allowlist")
    selected = root / document
    _reject_reparse_chain(selected)
    resolved_parent = selected.parent.resolve(strict=True)
    if resolved_parent != root and root not in resolved_parent.parents:
        raise DeliveryError("Document path escapes the registered repository")
    if selected.exists() and (selected.is_symlink() or _is_reparse(selected) or not selected.is_file()):
        raise DeliveryError("Document must be a regular non-reparse file")
    return selected


def _read_document(path: Path) -> bytes | None:
    if not path.exists():
        return None
    if path.stat().st_size > MAX_DOC_BYTES:
        raise DeliveryError("Document exceeds the bounded UTF-8 size")
    data = path.read_bytes()
    try:
        data.decode("utf-8")
    except UnicodeError as exc:
        raise DeliveryError("Document is not valid UTF-8") from exc
    return data


def _strict_old_sha(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).lower()
    if not _SHA_RE.fullmatch(text):
        raise DeliveryError("Expected old document SHA-256 is invalid")
    return text


def _creatable(document: str) -> bool:
    return document in {"docs/KNOWN_ISSUES.md", "docs/TASK_HISTORY.md"}


def _missing_heading(document: str) -> bytes:
    if document == "docs/KNOWN_ISSUES.md":
        return "# LADS 工程系统已知问题\n".encode("utf-8")
    if document == "docs/TASK_HISTORY.md":
        return "# LADS 工程任务历史\n".encode("utf-8")
    raise DeliveryError("Only KNOWN_ISSUES and TASK_HISTORY may be created when missing")


def _journal_has_event(journal: Any, task_id: str, event_id: str) -> dict[str, Any] | None:
    for row in journal.list_events(task_id):
        if row.get("id") == event_id:
            return row
    return None


def _document_receipt(
    task_id: str,
    document: str,
    before_sha256: str | None,
    after_sha256: str | None,
    event_ids: Sequence[str],
) -> dict[str, Any]:
    values = list(event_ids)
    receipt = {
        "document": document,
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "event_ids": values,
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        canonical_json({"task_id": task_id, **receipt}).encode("utf-8")
    ).hexdigest()
    return receipt


def _durable_document_receipt(
    row: dict[str, Any],
    *,
    task_id: str,
    document: str,
    event_ids: Sequence[str],
    label: str,
) -> dict[str, Any]:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        raise DeliveryError(f"{label} receipt is malformed")
    before_sha = _strict_old_sha(payload.get("before_sha256"))
    after_sha = str(payload.get("after_sha256") or "").lower()
    if not _SHA_RE.fullmatch(after_sha):
        raise DeliveryError(f"{label} receipt has an invalid after digest")
    expected = _document_receipt(
        task_id,
        document,
        before_sha,
        after_sha,
        event_ids,
    )
    if payload != expected:
        raise DeliveryError(f"{label} receipt conflicts with the current document events")
    return expected


class MemoryPublisher:
    """In-memory document publisher with the production idempotence contract."""

    def __init__(self, documents: Mapping[str, str] | None = None) -> None:
        self.documents = dict(documents or {})
        self.backups: dict[str, bytes] = {}
        self.receipts: list[dict[str, Any]] = []

    def publish(
        self,
        *,
        task_id: str,
        source_head: str,
        required_docs: Sequence[str],
        entries: Mapping[str, Sequence[dict[str, Any]]],
        expected_old_shas: Mapping[str, str | None],
        journal: Any,
        evidence: EvidenceProof,
        repository_ref_value: str | None = None,
    ) -> dict[str, Any]:
        del source_head, repository_ref_value
        requested = tuple(required_docs)
        if len(set(requested)) != len(requested) or set(entries) != set(requested):
            raise DeliveryError("Document entries must exactly match required documents")
        if set(expected_old_shas) != set(requested):
            raise DeliveryError("Optimistic document hashes must cover every required document")
        results: dict[str, Any] = {}
        for document in requested:
            if document not in KNOWN_DOCUMENTS:
                raise DeliveryError("Document is outside the fixed known allowlist")
            current_text = self.documents.get(document)
            current = current_text.encode("utf-8") if current_text is not None else None
            marker_rows = entries[document]
            expected = _strict_old_sha(expected_old_shas[document])
            before_sha = _sha_bytes(current) if current is not None else None
            existing = _existing_markers(current.decode("utf-8")) if current is not None else {}
            wanted = {row["event_id"]: row for row in marker_rows}
            for identity, marker in existing.items():
                if marker["doc"] != document:
                    raise DeliveryError("Document event marker belongs to another document")
                if identity in wanted and marker["payload_sha256"] != wanted[identity]["payload_sha256"]:
                    raise DeliveryError("Document event ID has a different durable payload")
            started_id = _stable_uuid(task_id, "document-write-started", document)
            completed_id = _stable_uuid(task_id, "document-write-completed", document)
            started = _journal_has_event(journal, task_id, started_id)
            completed = _journal_has_event(journal, task_id, completed_id)
            if completed is not None:
                durable = _durable_document_receipt(
                    completed,
                    task_id=task_id,
                    document=document,
                    event_ids=list(wanted),
                    label="Completed document write",
                )
                if before_sha != durable["after_sha256"]:
                    raise DeliveryError(
                        "Document changed after a completed write; concurrent user edit is preserved"
                    )
                result = {**durable, "idempotent": True, "text": current_text}
                results[document] = result
                self.receipts.append(result)
                continue
            if started is not None:
                durable = _durable_document_receipt(
                    started,
                    task_id=task_id,
                    document=document,
                    event_ids=list(wanted),
                    label="Started document write",
                )
                if before_sha == durable["after_sha256"]:
                    journal.append_event(
                        task_id, completed_id, "document_write_completed", durable
                    )
                    result = {**durable, "idempotent": True, "text": current_text}
                    results[document] = result
                    self.receipts.append(result)
                    continue
                if before_sha != durable["before_sha256"]:
                    raise DeliveryError(
                        "Document changed after a partial write; concurrent user edit is preserved"
                    )
                if set(wanted) <= set(existing):
                    raise DeliveryError(
                        "Document markers conflict with the incomplete durable write receipt"
                    )
            elif set(wanted) <= set(existing):
                receipt = _document_receipt(
                    task_id, document, before_sha, before_sha, list(wanted)
                )
                journal.append_event(
                    task_id, completed_id, "document_write_completed", receipt
                )
                result = {**receipt, "idempotent": True, "text": current_text}
                results[document] = result
                self.receipts.append(result)
                continue
            if before_sha != expected:
                raise DeliveryError("Document changed since the approved optimistic baseline")
            if current is None and not _creatable(document):
                raise DeliveryError("Required document is missing and is not creatable")
            block = _entry_block(task_id, document, marker_rows, evidence)
            prefix = current if current is not None else _missing_heading(document)
            if prefix and not prefix.endswith(b"\n"):
                prefix += b"\n"
            after = prefix + block
            after_sha = _sha_bytes(after)
            receipt = _document_receipt(
                task_id, document, before_sha, after_sha, list(wanted)
            )
            journal.append_event(
                task_id,
                started_id,
                "document_write_started",
                receipt,
            )
            if current is not None:
                self.backups[document] = current
            self.documents[document] = after.decode("utf-8")
            journal.append_event(task_id, completed_id, "document_write_completed", receipt)
            result = {**receipt, "idempotent": False, "text": self.documents[document]}
            self.receipts.append(result)
            results[document] = result
        return {"documents": results, "all_idempotent": all(row["idempotent"] for row in results.values())}


class DocumentPublisher:
    """Bounded append-only publisher for repository document files."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        backup_root: str | Path,
        registered_repository_ref: str,
    ) -> None:
        root = Path(repo_root).resolve(strict=True)
        _reject_reparse_chain(root)
        self.repo_root = root
        self.repository_ref = repository_ref(root)
        if registered_repository_ref != self.repository_ref:
            raise DeliveryError("Repository fingerprint does not match the registered task")
        backup = Path(backup_root)
        if not backup.is_absolute():
            raise DeliveryError("Private backup root must be absolute")
        _reject_reparse_chain(backup)
        backup = backup.resolve(strict=False)
        if backup == root or root in backup.parents or backup in root.parents:
            raise DeliveryError("Document backups must remain outside the repository")
        private_base = default_automation_root().parent.resolve(strict=False)
        if not (backup.is_relative_to(private_base / "automation") or backup.is_relative_to(private_base / "acceptance")):
            raise DeliveryError("Document backups must use the fixed private LADS namespace")
        self.backup_root = backup

    def _backup(self, task_id: str, document: str, before: bytes) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        directory = self.backup_root / f"{task_id}-{stamp}-{uuid.uuid4().hex[:8]}"
        directory.mkdir(parents=True, exist_ok=False)
        relative = Path(document)
        destination = directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as handle:
            handle.write(before)
            handle.flush()
            os.fsync(handle.fileno())
        if _sha_bytes(destination.read_bytes()) != _sha_bytes(before):
            raise DeliveryError("Independent document backup digest mismatch")
        manifest = {
            "task_id": task_id,
            "document": document,
            "backup_sha256": _sha_bytes(before),
            "created_at": utc_now_iso(),
        }
        (directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return destination

    def _replace(self, path: Path, before: bytes | None, after: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".lads-doc-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(after)
                handle.flush()
                os.fsync(handle.fileno())
            if _read_document(path) != before:
                raise DeliveryError("Document changed concurrently; user edit was preserved")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        if _read_document(path) != after:
            raise DeliveryError("Document post-write digest mismatch")

    def publish(
        self,
        *,
        task_id: str,
        source_head: str,
        required_docs: Sequence[str],
        entries: Mapping[str, Sequence[dict[str, Any]]],
        expected_old_shas: Mapping[str, str | None],
        journal: Any,
        evidence: EvidenceProof,
        repository_ref_value: str | None = None,
    ) -> dict[str, Any]:
        del source_head
        if repository_ref_value is not None and repository_ref_value != self.repository_ref:
            raise DeliveryError("Registered repository fingerprint changed")
        requested = tuple(required_docs)
        if len(set(requested)) != len(requested) or set(entries) != set(requested):
            raise DeliveryError("Document entries must exactly match required documents")
        if set(expected_old_shas) != set(requested):
            raise DeliveryError("Optimistic document hashes must cover every required document")
        results: dict[str, Any] = {}
        for document in requested:
            path = _safe_document(self.repo_root, document)
            before = _read_document(path)
            before_sha = _sha_bytes(before) if before is not None else None
            current_text = before.decode("utf-8") if before is not None else ""
            existing = _existing_markers(current_text) if before is not None else {}
            wanted = {row["event_id"]: row for row in entries[document]}
            for identity, marker in existing.items():
                if marker["doc"] != document:
                    raise DeliveryError("Document event marker belongs to another document")
                if identity in wanted and marker["payload_sha256"] != wanted[identity]["payload_sha256"]:
                    raise DeliveryError("Document event ID has a different durable payload")
            completed_id = _stable_uuid(task_id, "document-write-completed", document)
            started_id = _stable_uuid(task_id, "document-write-started", document)
            completed = _journal_has_event(journal, task_id, completed_id)
            started = _journal_has_event(journal, task_id, started_id)
            if completed is not None:
                durable = _durable_document_receipt(
                    completed,
                    task_id=task_id,
                    document=document,
                    event_ids=list(wanted),
                    label="Completed document write",
                )
                if before_sha == durable["after_sha256"]:
                    results[document] = {**durable, "idempotent": True}
                    continue
                raise DeliveryError(
                    "Document changed after a completed write; concurrent user edit is preserved"
                )
            if started is not None:
                durable = _durable_document_receipt(
                    started,
                    task_id=task_id,
                    document=document,
                    event_ids=list(wanted),
                    label="Started document write",
                )
                if before_sha == durable["after_sha256"]:
                    journal.append_event(
                        task_id, completed_id, "document_write_completed", durable
                    )
                    results[document] = {**durable, "idempotent": True}
                    continue
                if before_sha != durable["before_sha256"]:
                    raise DeliveryError(
                        "Document changed after a partial write; concurrent user edit is preserved"
                    )
            if set(wanted) <= set(existing):
                receipt = _document_receipt(
                    task_id, document, before_sha, before_sha, list(wanted)
                )
                journal.append_event(task_id, completed_id, "document_write_completed", receipt)
                results[document] = {**receipt, "idempotent": True}
                continue
            expected = _strict_old_sha(expected_old_shas[document])
            if before_sha != expected:
                raise DeliveryError("Document changed since the approved optimistic baseline")
            if before is None and not _creatable(document):
                raise DeliveryError("Required document is missing and is not creatable")
            prefix = before if before is not None else _missing_heading(document)
            if prefix and not prefix.endswith(b"\n"):
                prefix += b"\n"
            after = prefix + _entry_block(task_id, document, entries[document], evidence)
            after_sha = _sha_bytes(after)
            receipt = _document_receipt(
                task_id, document, before_sha, after_sha, list(wanted)
            )
            journal.append_event(
                task_id,
                started_id,
                "document_write_started",
                receipt,
            )
            if before is not None:
                self._backup(task_id, document, before)
            self._replace(path, before, after)
            journal.append_event(task_id, completed_id, "document_write_completed", receipt)
            results[document] = {**receipt, "idempotent": False}
        return {"documents": results, "all_idempotent": all(row["idempotent"] for row in results.values())}


class KnowledgePublisher:
    """Persist document index and delivery metadata through the existing core."""

    def __init__(self, store: Any, project_id: str, repo_root: str | Path) -> None:
        self.store = store
        self.project_id = str(uuid.UUID(str(project_id)))
        self.repo_root = Path(repo_root).resolve(strict=True)
        self.repository_ref = repository_ref(self.repo_root)
        self.indexer = KnowledgeIndexer(store, self.project_id, documents=DEFAULT_DOCS)

    def ensure_task(self, task_id: str, requirement_summary: str) -> dict[str, Any]:
        with self.store.batch():
            project = self.store._project(self.store._local.connection, self.project_id)
            if project.get("root_ref") != self.repository_ref:
                raise DeliveryError("Knowledge project is not explicitly bound to the exact repository")
            return self.store.create_task(
                self.project_id,
                requirement_summary,
                task_id=task_id,
                assigned_agent="builder",
            )

    def _observed_head(self) -> str:
        value = _git(self.repo_root, "rev-parse", "HEAD", limit=256).decode("ascii").strip()
        normalized = normalize_sha(value)
        if normalized is None:
            raise DeliveryError("Repository HEAD is invalid")
        return normalized

    def sync(
        self,
        *,
        task_id: str,
        source_head: str,
        reason: str,
        documents: Mapping[str, str],
        expected_commands: Sequence[str],
        evidence: EvidenceProof,
        index_event_id: str,
        entries: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        clean_reason = _bounded_text(reason, "index reason") or "delivery index"
        expected = tuple(expected_commands)
        observed_head = self._observed_head()
        if observed_head != source_head:
            raise DeliveryError("Observed repository HEAD does not match the pinned source HEAD")
        with self.store.batch():
            connection = self.store._local.connection
            project = self.store._project(connection, self.project_id)
            if project.get("root_ref") != self.repository_ref:
                raise DeliveryError("Knowledge project root binding changed")
            if entries is None:
                stats = self.indexer.sync_documents(self.repo_root)
            else:
                stats = self.indexer.index_documents(
                    root_ref=self.repository_ref,
                    observed_head=observed_head,
                    entries=entries,
                )

            indexed_documents: list[dict[str, Any]] = []
            for path, text in sorted(documents.items()):
                if path not in KNOWN_DOCUMENTS or not isinstance(text, str):
                    raise DeliveryError("Indexed document metadata is outside the fixed allowlist")
                raw = text.encode("utf-8")
                if len(raw) > MAX_DOC_BYTES:
                    raise DeliveryError("Indexed document exceeds the bounded size")
                change_id = _stable_uuid(task_id, "index-document", path, _sha_bytes(raw))
                change = self.store.record_change(
                    self.project_id,
                    task_id,
                    file_path=path,
                    change_type="document_update",
                    source_ref=path,
                    source_raw_sha=_sha_bytes(raw),
                    observed_head=observed_head,
                    actual_content_commit_sha=None,
                    dirty=True,
                    diff_sha=evidence.qa_sha256,
                    change_id=change_id,
                )
                indexed_documents.append(
                    {"path": path, "raw_sha256": _sha_bytes(raw), "idempotent": change.get("idempotent", False)}
                )

            tests: list[dict[str, Any]] = []
            for command in expected:
                test_id = _stable_uuid(task_id, "qa-command", command)
                result = self.store.record_test(
                    self.project_id,
                    task_id,
                    test_name="LADS prescribed QA command",
                    command=command,
                    status="passed",
                    summary=f"controller-bound QA receipt {evidence.qa_sha256}",
                    output_sha=evidence.qa_sha256,
                    test_id=test_id,
                )
                tests.append({"name": "LADS prescribed QA command", "idempotent": result.get("idempotent", False)})

            event = self.store.record_event(
                self.project_id,
                task_id,
                "index_synced",
                {
                    "task_id": task_id,
                    "source_head": source_head,
                    "observed_head": observed_head,
                    "reason": clean_reason,
                    "reviewer_status": "APPROVED",
                    "reviewer_sha256": evidence.reviewer_sha256,
                    "qa_sha256": evidence.qa_sha256,
                    "tests": [{"command": command, "status": "passed"} for command in expected],
                    "document_count": len(indexed_documents),
                    "working_content_commit": None,
                },
                event_id=index_event_id,
            )
        return {
            "index_event_id": index_event_id,
            "index_event_idempotent": event.get("idempotent", False),
            "source_head": source_head,
            "observed_head": observed_head,
            "working_content_commit": None,
            "documents": indexed_documents,
            "tests": tests,
            "stats": stats,
        }


class AutomationEngine:
    """Hard delivery gate for one TaskSpec and one private journal."""

    def __init__(
        self,
        spec: TaskSpec,
        journal: Any,
        *,
        publisher: Any | None = None,
        knowledge: KnowledgePublisher | Any | None = None,
        repo_root: str | Path | None = None,
        repository_ref_value: str | None = None,
        max_retries: int = _MAX_RETRY_ATTEMPTS,
    ) -> None:
        if not isinstance(spec, TaskSpec):
            raise DeliveryError("AutomationEngine requires a TaskSpec")
        if not isinstance(max_retries, int) or not 1 <= max_retries <= 10:
            raise DeliveryError("Retry limit must be bounded between 1 and 10")
        self.spec = spec
        self.journal = journal
        self.publisher = publisher
        self.knowledge = knowledge
        self.repo_root = Path(repo_root).resolve(strict=True) if repo_root is not None else None
        if self.repo_root is not None:
            actual_ref = repository_ref(self.repo_root)
            if repository_ref_value is not None and repository_ref_value != actual_ref:
                raise DeliveryError("Repository fingerprint does not match the repository")
            self.repository_ref = actual_ref
        else:
            self.repository_ref = repository_ref_value
        self.max_retries = max_retries

    def register(
        self,
        requirement_summary: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        summary = _bounded_text(requirement_summary, "requirement summary")
        if not summary:
            raise DeliveryError("Requirement summary is required")
        if session_id is not None and (not isinstance(session_id, str) or not 1 <= len(session_id) <= 200):
            raise DeliveryError("Session ID is invalid")
        try:
            existing = self.journal.get_task(self.spec.task_id)
        except JournalNotFound:
            existing = None
        if existing is None:
            state: dict[str, Any] = {
                **self.spec.as_dict(),
                "status": WorkflowState.TASK_CREATED.value,
                "ready": False,
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
                "requirement_summary": summary,
                "session_id": session_id,
                "repository_ref": self.repository_ref,
                "event_ids": [],
                "failed_attempts": [],
                "retry_count": 0,
            }
            state = self.journal.create_task(state)
            if self.knowledge is not None:
                self.knowledge.ensure_task(self.spec.task_id, summary)
            return self.status(self.spec.task_id)
        for name in ("task_id", "source_head", "candidate_sha256", "allowed_changes", "expected_qa_commands"):
            expected = state_value = existing.get(name)
            wanted = getattr(self.spec, name)
            if isinstance(wanted, tuple):
                wanted = list(wanted)
            if state_value != wanted:
                raise DeliveryError(f"Registered task has a different {name}")
        if existing.get("requirement_summary") != summary:
            raise DeliveryError("Registered task has a different requirement summary")
        if self.knowledge is not None:
            self.knowledge.ensure_task(self.spec.task_id, summary)
        return self.status(self.spec.task_id)

    def _state(self) -> dict[str, Any]:
        return self.journal.get_task(self.spec.task_id)

    def _save(self, state: dict[str, Any]) -> dict[str, Any]:
        state["ready"] = state.get("status") == WorkflowState.DELIVERY_READY.value
        state["updated_at"] = utc_now_iso()
        return self.journal.save_task(state)

    def _block(self, state: dict[str, Any], reason: str) -> None:
        state["status"] = WorkflowState.BLOCKED.value
        state["ready"] = False
        state["gate_error"] = _bounded_text(reason, "delivery gate error")
        self._save(state)

    def _record_engine_event(
        self,
        state: dict[str, Any],
        event_id: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        identity = event_id or _stable_uuid(
            self.spec.task_id,
            event_type,
            canonical_json(sanitize_metadata(payload)),
        )
        row = self.journal.append_event(
            self.spec.task_id,
            identity,
            event_type,
            sanitize_metadata(payload),
        )
        if identity not in state.setdefault("event_ids", []):
            state["event_ids"].append(identity)
        return row

    def verify(
        self,
        receipts: Any,
        *,
        simulation: bool = False,
    ) -> dict[str, Any]:
        state = self._state()
        status = state.get("status")
        if status in {
            WorkflowState.QA_FAILED.value,
            WorkflowState.REVIEW_FAILED.value,
            WorkflowState.BLOCKED.value,
        }:
            raise DeliveryError("Task is blocked by a prior evidence failure")
        if status not in {
            WorkflowState.TASK_CREATED.value,
            WorkflowState.QA_REVIEW_VERIFIED.value,
        }:
            raise DeliveryError("Acceptance evidence cannot be replaced after document synchronization starts")
        try:
            from .evidence import validate_stage_acceptance

            proof = validate_stage_acceptance(self.spec, receipts, simulation=simulation)
        except EvidenceError as exc:
            state["status"] = (
                WorkflowState.QA_FAILED.value
                if isinstance(exc, QAEvidenceError)
                else WorkflowState.REVIEW_FAILED.value
            )
            state["ready"] = False
            state["evidence_error"] = _bounded_text(str(exc), "evidence error")
            self._save(state)
            raise
        existing = state.get("evidence")
        if isinstance(existing, dict) and existing != proof.as_dict():
            raise DeliveryError("Registered acceptance evidence cannot be replaced")
        state["evidence"] = proof.as_dict()
        state["status"] = WorkflowState.QA_REVIEW_VERIFIED.value
        state["ready"] = False
        self._record_engine_event(
            state,
            _stable_uuid(self.spec.task_id, "qa-review-verified", proof.qa_sha256, proof.reviewer_sha256),
            "qa_review_verified",
            {"evidence": proof.as_dict()},
        )
        return self._save(state)

    def record_event(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        clean = _validate_event_payload(event_type, payload)
        state = self._state()
        if state.get("status") not in {
            WorkflowState.TASK_CREATED.value,
            WorkflowState.QA_REVIEW_VERIFIED.value,
        }:
            raise DeliveryError(
                "New document events are closed after document synchronization starts"
            )
        if event_id is not None:
            if not isinstance(event_id, str):
                raise DeliveryError("Event ID must be a UUID string")
            try:
                identity = str(uuid.UUID(event_id))
            except (ValueError, TypeError) as exc:
                raise DeliveryError("Event ID must be a UUID string") from exc
        else:
            identity = _stable_uuid(
                self.spec.task_id,
                "domain-event",
                event_type,
                canonical_json(clean),
            )
        row = self.journal.append_event(
            self.spec.task_id,
            identity,
            event_type,
            clean,
        )
        if identity not in state.setdefault("event_ids", []):
            state["event_ids"].append(identity)
        self._save(state)
        return row

    def required_documents(self) -> tuple[str, ...]:
        rows = self.journal.list_events(self.spec.task_id)
        required = {EVENT_DOCUMENTS[row["event_type"]] for row in rows if row.get("event_type") in EVENT_DOCUMENTS}
        return tuple(path for path in EVENT_DOCUMENTS.values() if path in required)

    def _proof(self, state: dict[str, Any]) -> EvidenceProof:
        evidence = state.get("evidence")
        if not isinstance(evidence, dict):
            raise DeliveryError("QA/review evidence is not verified")
        try:
            proof = EvidenceProof(
                task_id=evidence["task_id"],
                candidate_sha256=evidence["candidate_sha256"],
                controller_sha256=evidence["controller_sha256"],
                qa_sha256=evidence["qa_sha256"],
                reviewer_sha256=evidence["reviewer_sha256"],
                commands=tuple(evidence["commands"]),
                simulated=bool(evidence.get("simulated", False)),
                acceptance_mode=evidence.get("acceptance_mode", "independent"),
                authorization_sha256=evidence.get("authorization_sha256"),
            )
        except (KeyError, TypeError) as exc:
            raise DeliveryError("Stored acceptance evidence is malformed") from exc
        if proof.task_id != self.spec.task_id or proof.candidate_sha256 != self.spec.candidate_sha256:
            raise DeliveryError("Stored acceptance evidence does not match TaskSpec")
        if proof.commands != self.spec.expected_qa_commands:
            raise DeliveryError("Stored acceptance commands changed")
        return proof

    def sync_documents(
        self,
        expected_old_shas: Mapping[str, str | None],
        *,
        reason: str = "",
    ) -> dict[str, Any]:
        state = self._state()
        if state.get("status") not in {
            WorkflowState.QA_REVIEW_VERIFIED.value,
            WorkflowState.DOCS_FAILED.value,
            WorkflowState.DOCS_PENDING.value,
        }:
            raise DeliveryError("Tests and independent review must pass before documents")
        document_attempts = sum(
            1
            for row in state.get("failed_attempts", [])
            if row.get("stage") == "DOCS"
        )
        if document_attempts >= self.max_retries:
            self._block(state, "Document retry limit reached")
            raise DeliveryError("Document retry limit reached")
        if self.publisher is None:
            raise DeliveryError("A document publisher is not configured")
        proof = self._proof(state)
        events = self.journal.list_events(self.spec.task_id)
        entries = _event_entries(events)
        required = tuple(path for path in EVENT_DOCUMENTS.values() if path in entries)
        if set(expected_old_shas) != set(required):
            raise DeliveryError("Optimistic hashes must exactly cover event-derived documents")
        for row in events:
            if row.get("event_type") not in _SEMANTIC_EVENTS:
                continue
            document = EVENT_DOCUMENTS[row["event_type"]]
            approval = row.get("payload", {}).get("doc_approval", {})
            if _strict_old_sha(expected_old_shas.get(document)) != approval.get("document_sha256"):
                raise DeliveryError("Semantic event acknowledgement does not match the optimistic document")
        if _bounded_text(reason, "document reason") == "" and state.get("status") == WorkflowState.DOCS_FAILED.value:
            reason = state.get("document_reason", "")
        state["status"] = WorkflowState.DOCS_PENDING.value
        state["ready"] = False
        state["pending_documents"] = {
            path: _strict_old_sha(value) for path, value in expected_old_shas.items()
        }
        state["document_reason"] = _bounded_text(reason, "document reason")
        self._save(state)
        try:
            result = self.publisher.publish(
                task_id=self.spec.task_id,
                source_head=self.spec.source_head,
                required_docs=required,
                entries=entries,
                expected_old_shas=expected_old_shas,
                journal=self.journal,
                evidence=proof,
                repository_ref_value=self.repository_ref,
            )
        except Exception as exc:
            state = self._state()
            state["status"] = WorkflowState.DOCS_FAILED.value
            state["ready"] = False
            state["docs_error"] = _bounded_text(str(exc), "document failure")
            state["failed_attempts"] = [
                *state.get("failed_attempts", []),
                {"stage": "DOCS", "attempt": len(state.get("failed_attempts", [])) + 1, "reason": state["docs_error"]},
            ]
            self._save(state)
            raise DeliveryError("Document synchronization failed; index and delivery remain blocked") from exc
        state = self._state()
        state["documents_receipt"] = result
        state["status"] = WorkflowState.DOCS_SYNCED.value
        state["ready"] = False
        return self._save(state)

    def _documents_text(self, state: dict[str, Any]) -> dict[str, str]:
        documents = state.get("documents_receipt", {}).get("documents", {})
        result: dict[str, str] = {}
        for path, receipt in documents.items():
            text = receipt.get("text")
            if isinstance(text, str):
                result[path] = text
                continue
            if self.repo_root is None:
                raise DeliveryError("Repository root is required to re-read published documents")
            document = _safe_document(self.repo_root, path)
            raw = _read_document(document)
            if raw is None or _sha_bytes(raw) != receipt.get("after_sha256"):
                raise DeliveryError("Published document changed before index synchronization")
            result[path] = raw.decode("utf-8")
        return result

    def sync_index(self, *, reason: str = "") -> dict[str, Any]:
        state = self._state()
        if state.get("status") not in {
            WorkflowState.DOCS_SYNCED.value,
            WorkflowState.INDEX_FAILED.value,
            WorkflowState.INDEX_PENDING.value,
        }:
            raise DeliveryError("Documents must be synchronized before indexing")
        if self.knowledge is None:
            raise DeliveryError("Explicit existing knowledge binding is not configured")
        proof = self._proof(state)
        index_attempts = int(state.get("index_attempts", 0))
        if index_attempts >= self.max_retries:
            self._block(state, "Index retry limit reached")
            raise DeliveryError("Index retry limit reached")
        state["status"] = WorkflowState.INDEX_PENDING.value
        state["ready"] = False
        state["index_attempts"] = index_attempts + 1
        retry_payload = state.get("index_retry_payload")
        if index_attempts > 0 and isinstance(retry_payload, dict):
            bounded_prior = _bounded_text(
                retry_payload.get("reason"), "index retry reason"
            )
            state["index_reason"] = bounded_prior or "delivery index"
        else:
            prior_reason = _bounded_text(
                state.get("index_reason"), "index reason"
            )
            state["index_reason"] = (
                _bounded_text(reason, "index reason")
                or prior_reason
                or "delivery index"
            )
        index_event_id = _stable_uuid(self.spec.task_id, "index-synced", proof.candidate_sha256)
        state["index_retry_payload"] = {
            "reason": state["index_reason"],
            "documents_receipt": state.get("documents_receipt"),
            "evidence": proof.as_dict(),
        }
        self._save(state)
        try:
            result = self.knowledge.sync(
                task_id=self.spec.task_id,
                source_head=self.spec.source_head,
                reason=state["index_reason"],
                documents=self._documents_text(state),
                expected_commands=self.spec.expected_qa_commands,
                evidence=proof,
                index_event_id=index_event_id,
            )
        except Exception as exc:
            state = self._state()
            state["status"] = WorkflowState.INDEX_FAILED.value
            state["ready"] = False
            state["index_error"] = _bounded_text(str(exc), "index failure")
            state["failed_attempts"] = [
                *state.get("failed_attempts", []),
                {
                    "stage": "INDEX",
                    "attempt": state.get("index_attempts"),
                    "reason": state["index_error"],
                },
            ]
            self._save(state)
            raise DeliveryError("Index synchronization failed; retry state is durable") from exc
        state = self._state()
        state["index_receipt"] = result
        state.pop("index_retry_payload", None)
        state["status"] = WorkflowState.DELIVERY_READY.value
        state["ready"] = True
        return self._save(state)

    def sync(
        self,
        expected_old_shas: Mapping[str, str | None],
        *,
        reason: str = "",
    ) -> dict[str, Any]:
        state = self._state()
        if state.get("status") == WorkflowState.BLOCKED.value:
            raise DeliveryError("Blocked task requires controller intervention")
        if state.get("status") in {
            WorkflowState.QA_FAILED.value,
            WorkflowState.REVIEW_FAILED.value,
        }:
            raise DeliveryError("QA and independent review must pass before documents")
        if state.get("status") in {
            WorkflowState.QA_REVIEW_VERIFIED.value,
            WorkflowState.DOCS_FAILED.value,
            WorkflowState.DOCS_PENDING.value,
        }:
            self.sync_documents(expected_old_shas, reason=reason)
        state = self._state()
        if state.get("status") == WorkflowState.DOCS_SYNCED.value and self.knowledge is not None:
            return self.sync_index(reason=reason)
        return self.status(self.spec.task_id)

    def retry(self, *, reason: str = "") -> dict[str, Any]:
        state = self._state()
        status = state.get("status")
        if status in {WorkflowState.DOCS_FAILED.value, WorkflowState.DOCS_PENDING.value}:
            attempts = sum(1 for row in state.get("failed_attempts", []) if row.get("stage") == "DOCS")
            if attempts >= self.max_retries:
                self._block(state, "Document retry limit reached")
                raise DeliveryError("Document retry limit reached")
            expected = state.get("pending_documents")
            if not isinstance(expected, dict):
                raise DeliveryError("Document retry payload is missing")
            return self.sync_documents(expected, reason=reason)
        if status in {WorkflowState.INDEX_FAILED.value, WorkflowState.INDEX_PENDING.value}:
            return self.sync_index(reason=reason)
        if status == WorkflowState.BLOCKED.value:
            raise DeliveryError("Blocked task requires controller intervention")
        raise DeliveryError("No retryable documentation or index failure is present")

    def status(self, task_id: str | None = None) -> dict[str, Any]:
        identity = self.spec.task_id if task_id is None else str(uuid.UUID(task_id))
        if identity != self.spec.task_id:
            raise DeliveryError("Engine is bound to one TaskSpec")
        state = self._state()
        evidence = state.get("evidence")
        documents = state.get("documents_receipt")
        index = state.get("index_receipt")
        derived_ready = bool(
            state.get("status") == WorkflowState.DELIVERY_READY.value
            and isinstance(evidence, dict)
            and evidence.get("qa_review_verified") is True
            and evidence.get("whole_task_complete") is False
            and isinstance(documents, dict)
            and isinstance(index, dict)
            and index.get("working_content_commit") is None
            and index.get("index_event_id")
        )
        state["ready"] = derived_ready
        return {
            "task_id": state["task_id"],
            "status": state["status"],
            "ready": derived_ready,
            "candidate_sha256": state["candidate_sha256"],
            "source_head": state["source_head"],
            "required_documents": list(self.required_documents()),
            "event_count": len(self.journal.list_events(self.spec.task_id)),
            "failed_attempts": state.get("failed_attempts", []),
            "index_attempts": state.get("index_attempts", 0),
        }
