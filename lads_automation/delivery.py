"""Stage 3 handoff validation and MAIN-ACK delivery finalization."""
from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import qa, unit_qa
from .engine import (
    EVENT_DOCUMENTS,
    AutomationEngine,
    DeliveryError,
    DocumentPublisher,
)
from .evidence import ControllerReceipts, TaskSpec, validate_stage_acceptance
from .state import (
    FileJournal,
    JournalError,
    JournalNotFound,
    default_automation_root,
)
from .team import BASE, record_worker_completed

MAX_JSON_BYTES = 2 * 1024 * 1024
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_WORKER_PROVIDER = "lifeos_deepseek"
_WORKER_MODEL = "deepseek-flash"
_SEMANTIC_EVENTS = frozenset(EVENT_DOCUMENTS) - {
    "task_state_changed",
    "release_changed",
    "issue_found",
    "task_delivered",
}
_DEFAULT_EVENTS = ("task_state_changed", "task_delivered")


class Stage3DeliveryError(DeliveryError):
    """A Stage 3 handoff is forged, incomplete, stale, or unsafe."""


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_file() and not path.is_symlink() and not (
        getattr(info, "st_file_attributes", 0) & marker
    )


def _contained_path(root: Path, relative: str, *, regular: bool = True) -> Path:
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or relative.startswith("/")
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise Stage3DeliveryError("Stage receipt path is not a contained POSIX path")
    selected = (root / relative).resolve(strict=True)
    if selected != root and root not in selected.parents:
        raise Stage3DeliveryError("Stage receipt escapes the coordinator run root")
    if regular and not _regular_file(selected):
        raise Stage3DeliveryError("Stage receipt must be a regular non-reparse file")
    if not regular and (not selected.is_dir() or selected.is_symlink()):
        raise Stage3DeliveryError("Managed directory is missing or unsafe")
    return selected


def _contained_file(root: Path, relative: str) -> Path:
    return _contained_path(root, relative, regular=True)


def _contained_directory(root: Path, relative: str) -> Path:
    return _contained_path(root, relative, regular=False)


def _strict_json(path: Path, maximum: int = MAX_JSON_BYTES) -> dict[str, Any]:
    if not _regular_file(path) or path.stat().st_size > maximum:
        raise Stage3DeliveryError("Coordinator JSON is missing, unsafe, or oversized")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise Stage3DeliveryError("Coordinator JSON contains duplicate keys")
            value[key] = item
        return value

    try:
        value = json.loads(
            path.read_bytes().decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                Stage3DeliveryError("Coordinator JSON contains non-JSON numbers")
            ),
        )
    except (UnicodeError, ValueError) as exc:
        raise Stage3DeliveryError("Coordinator JSON is invalid UTF-8/json") from exc
    if not isinstance(value, dict):
        raise Stage3DeliveryError("Coordinator JSON root must be an object")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _changed_files(before: Mapping[str, str], after: Mapping[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted(set(before) | set(after)):
        old = before.get(path)
        new = after.get(path)
        if old == new:
            continue
        result.append(
            {
                "path": path,
                "kind": "added" if old is None else "deleted" if new is None else "modified",
                "old_sha256": old,
                "new_sha256": new,
            }
        )
    return result


def _validated_handoff_changes(
    before: Mapping[str, str], after: Mapping[str, str], declared: Any,
) -> list[dict[str, Any]]:
    # The coordinator's wire schema is change/before_sha256/after_sha256.
    # Recompute and compare it exactly before exposing the delivery schema;
    # never rewrite a signed handoff or discard a mismatching hash.
    if declared != BASE.changed_files(before, after):
        raise Stage3DeliveryError("Candidate changed-file list does not match the handoff")
    return _changed_files(before, after)


def _repo_state(repo: Path) -> tuple[str, str, dict[str, str]]:
    head = BASE.git_head(repo)
    baseline = BASE.git_status(repo)
    files = {
        relative: BASE.sha_file(repo / Path(relative))
        for relative in BASE.source_paths(repo)
    }
    return head, baseline, files


@dataclass(frozen=True)
class HandoffBundle:
    task_id: str
    run_root: Path
    repo_root: Path
    handoff: dict[str, Any]
    controller_state: dict[str, Any]
    candidate_dir: Path
    candidate_sha256: str
    source_head: str
    source_files: dict[str, str]
    candidate_files: dict[str, str]
    changed_files: tuple[dict[str, Any], ...]
    qa_stage: dict[str, Any]
    reviewer_stage: dict[str, Any]
    qa_stage_sha256: str
    reviewer_stage_sha256: str
    qa_receipt: dict[str, Any]
    reviewer_receipt: dict[str, Any]
    worker_stage_receipts: tuple[dict[str, Any], ...]
    controller_receipt: dict[str, Any]
    receipts: ControllerReceipts
    task_spec: TaskSpec
    expected_commands: tuple[str, ...]
    qa_contract_version: int
    unit_evidence: dict[str, Any] | None

    @property
    def handoff_sha256(self) -> str:
        return _digest(self.handoff)


def _qa_contract(commands: Any) -> tuple[int, tuple[str, ...]]:
    if not isinstance(commands, list):
        raise Stage3DeliveryError("QA command executions must be a list")
    actual = tuple(
        item.get("command") if isinstance(item, dict) else None
        for item in commands
    )
    version = unit_qa.contract_version(tuple(item for item in actual if isinstance(item, str)))
    contracts = {1: unit_qa.LEGACY_STAGE3_QA_COMMANDS, 2: unit_qa.STAGE4_QA_COMMANDS, 3: unit_qa.SPLIT_QA_COMMANDS}
    if version not in contracts or len(actual) != len(contracts[version]):
        raise Stage3DeliveryError("QA command set is not an exact known contract")
    expected = contracts[version]
    if any(not isinstance(item, str) for item in actual) or tuple(actual) != expected:
        raise Stage3DeliveryError("QA command set is unexpected, reordered, or duplicated")
    return version, expected


def _validate_qa_executions(
    commands: Any,
    expected_commands: Sequence[str],
) -> tuple[dict[str, Any], ...]:
    if not isinstance(commands, list) or len(commands) != len(expected_commands):
        raise Stage3DeliveryError("QA must execute the exact verifier contract")
    seen: set[str] = set()
    for execution in commands:
        if not isinstance(execution, dict):
            raise Stage3DeliveryError("QA command execution is malformed")
        command = execution.get("command")
        if command not in expected_commands or command in seen:
            raise Stage3DeliveryError("QA command set is unexpected or duplicated")
        if (
            execution.get("status") != "completed"
            or type(execution.get("exit_code")) is not int
            or execution.get("exit_code") != 0
        ):
            raise Stage3DeliveryError("QA command lacks completed integer-zero evidence")
        seen.add(command)
    if seen != set(expected_commands):
        raise Stage3DeliveryError("QA did not execute the exact command contract")
    return tuple(commands)


def _stage_reports(
    run_root: Path,
    handoff: Mapping[str, Any],
) -> list[tuple[int, dict[str, Any], str, str]]:
    refs = handoff.get("stage_reports")
    if not isinstance(refs, list) or not refs or len(refs) > 100:
        raise Stage3DeliveryError("Coordinator stage refs are missing or unbounded")
    loaded: list[tuple[int, dict[str, Any], str, str]] = []
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
            raise Stage3DeliveryError("Coordinator stage ref is malformed")
        selected = _contained_file(run_root, ref["path"])
        actual = _sha_file(selected)
        if actual != ref.get("sha256"):
            raise Stage3DeliveryError("Coordinator stage receipt digest changed")
        loaded.append((index, _strict_json(selected), ref["path"], actual))
    return loaded


def _worker_stage_records(
    loaded: Sequence[tuple[int, dict[str, Any], str, str]],
    role_runs: Sequence[Any],
    *,
    task_id: str,
    candidate_sha256: str,
    final_build: int,
) -> tuple[dict[str, Any], ...]:
    """Import bounded actual role metadata instead of fabricating final receipts."""
    if not isinstance(role_runs, list) or not role_runs:
        raise Stage3DeliveryError("Coordinator worker metadata is missing")
    actual_runs: dict[str, dict[str, Any]] = {}
    for run in role_runs:
        if not isinstance(run, dict):
            raise Stage3DeliveryError("Coordinator worker metadata contains a malformed row")
        identity = _canonical(run)
        if identity in actual_runs:
            raise Stage3DeliveryError("Coordinator worker metadata is duplicated")
        actual_runs[identity] = run
    records: list[dict[str, Any]] = []
    seen_runs: set[str] = set()
    for _index, stage, relative, stage_sha in loaded:
        metadata = stage.get("metadata")
        if not isinstance(metadata, dict):
            continue
        identity = _canonical(metadata)
        if identity not in actual_runs:
            continue
        if identity in seen_runs:
            raise Stage3DeliveryError("A worker metadata row is bound to multiple stages")
        seen_runs.add(identity)
        role = stage.get("role")
        status = stage.get("status")
        if role not in {"scanner", "reviewer", "builder", "qa-tester"}:
            raise Stage3DeliveryError("Actual worker stage role is unsupported")
        if not isinstance(status, str) or not status or len(status) > 100:
            raise Stage3DeliveryError("Actual worker stage status is malformed")
        provider = metadata.get("provider")
        model = metadata.get("model")
        if provider != _WORKER_PROVIDER or model != _WORKER_MODEL:
            raise Stage3DeliveryError("Actual worker stage provider/model is invalid")
        text_fields = (
            "role",
            "provider",
            "model",
            "sandbox",
            "start_time",
            "end_time",
            "config_snapshot_sha256",
            "home_mode",
            "home_sha256",
            "authority_sha256",
        )
        stage_metadata = {
            name: metadata.get(name)
            for name in (
                *text_fields,
                "input_tokens",
                "output_tokens",
            )
        }
        if (
            stage_metadata["role"] != role
            or not all(
                isinstance(stage_metadata[name], str) and stage_metadata[name]
                for name in text_fields
            )
            or any(
                value is not None and (type(value) is not int or value < 0)
                for value in (
                    stage_metadata["input_tokens"],
                    stage_metadata["output_tokens"],
                )
            )
        ):
            raise Stage3DeliveryError("Actual worker stage metadata is incomplete")
        exit_code = stage.get("exit_code")
        if type(exit_code) is not int:
            raise Stage3DeliveryError("Actual worker stage exit code is malformed")
        stage_candidate = None
        if role == "qa-tester":
            report = stage.get("report")
            reported = report.get("source_snapshot_sha256") if isinstance(report, dict) else None
            if not isinstance(reported, str) or not _SHA_RE.fullmatch(reported):
                raise Stage3DeliveryError("QA worker stage lacks its candidate digest")
            stage_candidate = reported
        elif role == "reviewer" and status == "APPROVED" and stage.get("build") == final_build:
            stage_candidate = candidate_sha256
        worker_result = type(
            "RecordedWorkerResult",
            (),
            {
                "stdout": "",
                "stderr": "",
                "exit_code": exit_code,
                "timed_out": False,
                "bounded_output": False,
            },
        )()
        records.append(
            {
                "task_id": task_id,
                "role": role,
                "status": status,
                "provider": provider,
                "model": model,
                "candidate_sha256": stage_candidate,
                "result": worker_result,
                "stage_metadata": stage_metadata,
                "stage_ref": {"path": relative, "sha256": stage_sha},
            }
        )
    if len(records) != len(role_runs) or seen_runs != set(actual_runs):
        raise Stage3DeliveryError(
            "Actual worker metadata is not bound one-to-one to stage receipts"
        )
    if {record["role"] for record in records} != {
        "scanner",
        "reviewer",
        "builder",
        "qa-tester",
    }:
        raise Stage3DeliveryError("Actual worker stage history is missing an assigned role")
    return tuple(records)


def _document_receipt_matches(task_id: str, receipt: Mapping[str, Any]) -> bool:
    if not isinstance(receipt, Mapping):
        return False
    base = {
        "document": receipt.get("document"),
        "before_sha256": receipt.get("before_sha256"),
        "after_sha256": receipt.get("after_sha256"),
        "event_ids": receipt.get("event_ids"),
    }
    before = base["before_sha256"]
    after = base["after_sha256"]
    event_ids = base["event_ids"]
    if (
        not isinstance(base["document"], str)
        or (
            before is not None
            and (not isinstance(before, str) or not _SHA_RE.fullmatch(before))
        )
        or not isinstance(after, str)
        or not _SHA_RE.fullmatch(after)
        or not isinstance(event_ids, list)
        or not event_ids
        or len(event_ids) > 100
        or any(not isinstance(item, str) or len(item) > 100 for item in event_ids)
        or len(set(event_ids)) != len(event_ids)
    ):
        return False
    return receipt.get("receipt_sha256") == _digest({"task_id": task_id, **base})


def _document_published_files(
    base_files: Mapping[str, str],
    state: Mapping[str, Any],
    *,
    task_id: str,
) -> dict[str, str] | None:
    documents_receipt = state.get("documents_receipt")
    if not isinstance(documents_receipt, Mapping):
        return None
    documents = documents_receipt.get("documents")
    if not isinstance(documents, Mapping) or not documents or len(documents) > 100:
        return None
    expected = dict(base_files)
    for document, receipt in documents.items():
        if (
            not isinstance(document, str)
            or "\\" in document
            or document.startswith("/")
            or any(part in {"", ".", ".."} for part in document.split("/"))
            or not _document_receipt_matches(task_id, receipt)
            or receipt.get("document") != document
            or base_files.get(document) != receipt.get("before_sha256")
        ):
            return None
        expected[document] = receipt["after_sha256"]
    return expected if expected != dict(base_files) else None


def _publisher_events_match(
    state: Mapping[str, Any],
    events: Sequence[Any],
    *,
    task_id: str,
) -> bool:
    documents_receipt = state.get("documents_receipt")
    documents = (
        documents_receipt.get("documents")
        if isinstance(documents_receipt, Mapping)
        else None
    )
    if not isinstance(documents, Mapping) or not documents:
        return False
    completed = [
        row
        for row in events
        if isinstance(row, Mapping)
        and row.get("task_id") == task_id
        and row.get("event_type") == "document_write_completed"
    ]
    for document, receipt in documents.items():
        if not isinstance(receipt, Mapping) or receipt.get("document") != document:
            return False
        durable = {
            "document": document,
            "before_sha256": receipt.get("before_sha256"),
            "after_sha256": receipt.get("after_sha256"),
            "event_ids": receipt.get("event_ids"),
        }
        if not _document_receipt_matches(task_id, receipt):
            return False
        durable["receipt_sha256"] = receipt["receipt_sha256"]
        if sum(1 for row in completed if row.get("payload") == durable) != 1:
            return False
    return True


def _validate_main_ack_fields(
    run_root: Path,
    *,
    task_id: str,
    handoff_sha256: str,
    source_head: str,
    candidate_sha256: str,
) -> None:
    value = _strict_json(_contained_file(run_root, "main-ack.json"), 32 * 1024)
    expected = {
        "schema": "lads-stage3-main-ack-v1",
        "task_id": task_id,
        "status": "ACKNOWLEDGED",
        "handoff_sha256": handoff_sha256,
        "candidate_sha256": candidate_sha256,
        "source_head": source_head,
        "authorization": "controller_action_only_not_deploy_push_or_merge",
    }
    for key, item in expected.items():
        if value.get(key) != item:
            raise Stage3DeliveryError("Explicit MAIN-ACK receipt does not bind this handoff")


def _read_persisted_delivery_state(
    task_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    root = default_automation_root()
    if not root.exists():
        return None
    try:
        journal = FileJournal.open()
        return journal.get_task(task_id), journal.list_events(task_id)
    except JournalNotFound:
        return None
    except (JournalError, OSError) as exc:
        raise Stage3DeliveryError("Persisted delivery state is unreadable") from exc


def _validate_preflight(stage: Mapping[str, Any], role: str) -> None:
    metadata = stage.get("metadata")
    if not isinstance(metadata, dict):
        raise Stage3DeliveryError(f"{role} metadata is missing")
    if metadata.get("role") != role:
        raise Stage3DeliveryError(f"{role} metadata role changed")
    if metadata.get("provider") != _WORKER_PROVIDER or metadata.get("model") != _WORKER_MODEL:
        raise Stage3DeliveryError(f"{role} did not run through the assigned DeepSeek profile")
    proof = metadata.get("preflight_proof")
    if (
        not isinstance(proof, dict)
        or proof.get("role") != role
        or proof.get("exit_code") != 0
        or type(proof.get("exit_code")) is not int
    ):
        raise Stage3DeliveryError(f"{role} lacks a successful isolated permission preflight")
    if stage.get("exit_code") != 0 or type(stage.get("exit_code")) is not int:
        raise Stage3DeliveryError(f"{role} process did not complete with integer exit 0")


def _worker_stage(
    loaded: Sequence[tuple[int, dict[str, Any], str, str]],
    *,
    role: str,
    status: str,
    build: int,
    task_id: str,
    candidate_sha256: str,
) -> tuple[int, dict[str, Any], str, str]:
    matches = [
        item
        for item in loaded
        if item[1].get("role") == role
        and item[1].get("status") == status
        and item[1].get("build") == build
        and item[1].get("task_id") == task_id
    ]
    if len(matches) != 1:
        raise Stage3DeliveryError(
            f"Exactly one final {role}/{status} receipt is required for the candidate build"
        )
    index, stage, relative, stage_sha = matches[0]
    report = stage.get("report")
    if (
        not isinstance(report, dict)
        or report.get("task_id") != task_id
        or report.get("role") != role
        or report.get("status") != status
    ):
        raise Stage3DeliveryError(f"{role} report does not bind the final stage status")
    if role == "qa-tester" and report.get("source_snapshot_sha256") != candidate_sha256:
        raise Stage3DeliveryError("QA report is bound to a different candidate digest")
    _validate_preflight(stage, role)
    return index, stage, relative, stage_sha


def load_handoff_bundle(
    *,
    run_root: str | Path,
    repo_root: str | Path,
    repo_reader: Callable[[Path], tuple[str, str, dict[str, str]]] = _repo_state,
    require_main_ack: bool = False,
    persisted_state: Mapping[str, Any] | None = None,
    persisted_events: Sequence[Any] | None = None,
) -> HandoffBundle:
    root = Path(run_root).resolve(strict=True)
    repo = Path(repo_root).resolve(strict=True)
    handoff_path = _contained_file(root, "finalhandoff.json")
    handoff = _strict_json(handoff_path)
    state = _strict_json(_contained_file(root, "state.json"))
    task_id = handoff.get("task_id")
    if (
        handoff.get("task_status") != "AWAITING_MAIN_ACCEPTANCE"
        or state.get("task_id") != task_id
        or state.get("state") != "AWAITING_MAIN_ACCEPTANCE"
    ):
        raise Stage3DeliveryError("Coordinator handoff is not awaiting explicit MAIN-ACK")
    if state.get("repo") is None or Path(state["repo"]).resolve(strict=True) != repo:
        raise Stage3DeliveryError("Coordinator repository fingerprint does not match the target")
    source_head = handoff.get("source_head")
    candidate_sha256 = handoff.get("candidate_sha256")
    builds = handoff.get("builds")
    if (
        not isinstance(source_head, str)
        or not re.fullmatch(r"[0-9a-f]{40,64}", source_head)
        or not isinstance(candidate_sha256, str)
        or not _SHA_RE.fullmatch(candidate_sha256)
        or type(builds) is not int
        or not 1 <= builds <= BASE.MAX_BUILDS
    ):
        raise Stage3DeliveryError("Coordinator handoff identity is malformed")
    source_dir = _contained_directory(root, "source")
    candidate_dir = _contained_directory(root, f"candidates/build-{builds}")
    source_files = qa.source_inventory(source_dir)
    candidate_files = qa.source_inventory(candidate_dir)
    source_digest = qa.inventory_digest(source_head, source_files)
    candidate_digest = qa.inventory_digest(source_head, candidate_files)
    if state.get("source_snapshot_sha256") != source_digest:
        raise Stage3DeliveryError("Coordinator source snapshot digest changed")
    if candidate_digest != candidate_sha256 or state.get("candidate_sha256") != candidate_digest:
        raise Stage3DeliveryError("Final candidate digest does not match the handoff")
    changed = _validated_handoff_changes(source_files, candidate_files, handoff.get("files"))
    allowed = handoff.get("allowed_paths")
    if not isinstance(allowed, list) or any(not isinstance(item, str) for item in allowed):
        raise Stage3DeliveryError("Coordinator allowed-path list is malformed")
    if any(item["path"] not in allowed for item in changed):
        raise Stage3DeliveryError("Candidate contains a change outside the approved scope")
    if persisted_state is None:
        persisted = _read_persisted_delivery_state(task_id)
        if persisted is not None:
            persisted_state, persisted_events = persisted
    head, baseline, current_files = repo_reader(repo)
    if head != source_head:
        raise Stage3DeliveryError("Current repository HEAD drifted")
    if current_files not in (source_files, candidate_files):
        verified_published = None
        if isinstance(persisted_state, Mapping):
            if (
                persisted_state.get("task_id") != task_id
                or persisted_state.get("source_head") != source_head
                or persisted_state.get("candidate_sha256") != candidate_digest
                or persisted_state.get("status")
                not in {"DOCS_SYNCED", "INDEX_PENDING", "INDEX_FAILED"}
            ):
                raise Stage3DeliveryError(
                    "Persisted delivery state does not bind the exact candidate"
                )
            _validate_main_ack_fields(
                root,
                task_id=task_id,
                handoff_sha256=_sha_file(handoff_path),
                source_head=source_head,
                candidate_sha256=candidate_digest,
            )
            if (
                not isinstance(persisted_events, Sequence)
                or not _publisher_events_match(
                    persisted_state,
                    persisted_events,
                    task_id=task_id,
                )
            ):
                raise Stage3DeliveryError(
                    "Persisted document state lacks exact publisher completion events"
                )
            for base_files in (candidate_files, source_files):
                expected = _document_published_files(
                    base_files,
                    persisted_state,
                    task_id=task_id,
                )
                if expected is not None and current_files == expected:
                    verified_published = expected
                    break
        if verified_published is None:
            raise Stage3DeliveryError(
                "Current repository drift is not the exact verified publisher document state"
            )
    if (
        current_files == source_files
        and baseline != handoff.get("source_baseline_sha256")
    ):
        raise Stage3DeliveryError("Current source baseline drifted")

    loaded = _stage_reports(root, handoff)
    qa_index, qa_stage, qa_path, qa_sha = _worker_stage(
        loaded,
        role="qa-tester",
        status="PASS",
        build=builds,
        task_id=task_id,
        candidate_sha256=candidate_digest,
    )
    reviewer_index, reviewer_stage, reviewer_path, reviewer_sha = _worker_stage(
        loaded,
        role="reviewer",
        status="APPROVED",
        build=builds,
        task_id=task_id,
        candidate_sha256=candidate_digest,
    )
    if reviewer_index <= qa_index:
        raise Stage3DeliveryError("Final reviewer approval must follow the passing QA receipt")
    role_runs = handoff.get("role_runs")
    if not isinstance(role_runs, list):
        raise Stage3DeliveryError("Coordinator worker metadata is missing")
    if qa_stage.get("metadata") not in role_runs or reviewer_stage.get("metadata") not in role_runs:
        raise Stage3DeliveryError("Final stage metadata is not present in coordinator stage history")
    worker_stage_receipts = _worker_stage_records(
        loaded,
        role_runs,
        task_id=task_id,
        candidate_sha256=candidate_digest,
        final_build=builds,
    )

    commands = qa_stage.get("commands")
    qa_contract_version, expected_commands = _qa_contract(commands)
    _validate_qa_executions(commands, expected_commands)
    unit_evidence = None
    if qa_contract_version in {unit_qa.UNIT_CONTRACT_VERSION, 3}:
        try:
            if qa_contract_version == 3:
                from .split_qa import verify_report as unit_verifier
            else:
                unit_verifier = unit_qa.load_and_verify_unit_report
            unit_evidence = unit_verifier(
                root / unit_qa.UNIT_REPORT_NAME,
                candidate_root=candidate_dir,
                task_id=task_id,
                source_head=source_head,
                expected_inventory_sha256=candidate_digest,
            )
        except Exception as exc:
            raise Stage3DeliveryError(
                "Stage4 unit proof is missing or invalid: " + type(exc).__name__
            ) from exc

    controller_receipt = {
        "schema": "lads-stage3-controller-receipt-v1",
        "task_id": task_id,
        "status": "VALID",
        "source_head": source_head,
        "candidate_sha256": candidate_digest,
        **(
            {
                "qa_contract_version": qa_contract_version,
                "unit_evidence_sha256": unit_evidence["evidence_sha256"],
            }
            if qa_contract_version in {unit_qa.UNIT_CONTRACT_VERSION, 3}
            and unit_evidence is not None
            else {}
        ),
        "stage_files": {
            "qa": {"path": qa_path, "sha256": qa_sha},
            "reviewer": {"path": reviewer_path, "sha256": reviewer_sha},
        },
    }
    qa_receipt = {
        "task_id": task_id,
        "role": "qa-tester",
        "status": "PASS",
        "candidate_sha256": candidate_digest,
        "source_snapshot_sha256": candidate_digest,
        **(
            {
                "qa_contract_version": qa_contract_version,
                "unit_evidence": unit_evidence,
            }
            if qa_contract_version in {unit_qa.UNIT_CONTRACT_VERSION, 3}
            and unit_evidence is not None
            else {}
        ),
        "command_executions": [
            {
                "command": item["command"],
                "execution_type": "command_execution",
                "status": item["status"],
                "exit_code": item["exit_code"],
                "candidate_sha256": candidate_digest,
            }
            for item in commands
        ],
    }
    reviewer_receipt = {
        "task_id": task_id,
        "role": "reviewer",
        "status": "APPROVED",
        "independent": True,
        "review_type": "final_diff",
        "candidate_sha256": candidate_digest,
    }
    receipts = ControllerReceipts(
        controller=controller_receipt,
        qa=qa_receipt,
        reviewer=reviewer_receipt,
        controller_sha256=_digest(controller_receipt),
        qa_sha256=qa_sha,
        reviewer_sha256=reviewer_sha,
    )
    spec = TaskSpec(
        task_id=task_id,
        source_head=source_head,
        candidate_sha256=candidate_digest,
        allowed_changes=tuple(allowed),
        expected_qa_commands=expected_commands,
    )
    validate_stage_acceptance(spec, receipts)
    bundle = HandoffBundle(
        task_id=task_id,
        run_root=root,
        repo_root=repo,
        handoff=handoff,
        controller_state=state,
        candidate_dir=candidate_dir,
        candidate_sha256=candidate_digest,
        source_head=source_head,
        source_files=source_files,
        candidate_files=candidate_files,
        changed_files=tuple(changed),
        qa_stage=qa_stage,
        reviewer_stage=reviewer_stage,
        qa_stage_sha256=qa_sha,
        reviewer_stage_sha256=reviewer_sha,
        qa_receipt=qa_receipt,
        reviewer_receipt=reviewer_receipt,
        worker_stage_receipts=worker_stage_receipts,
        controller_receipt=controller_receipt,
        receipts=receipts,
        task_spec=spec,
        expected_commands=expected_commands,
        qa_contract_version=qa_contract_version,
        unit_evidence=unit_evidence,
    )
    if require_main_ack:
        validate_main_ack(bundle)
    return bundle


def create_main_ack(bundle: HandoffBundle) -> dict[str, Any]:
    return {
        "schema": "lads-stage3-main-ack-v1",
        "task_id": bundle.task_id,
        "status": "ACKNOWLEDGED",
        "handoff_sha256": _sha_file(bundle.run_root / "finalhandoff.json"),
        "candidate_sha256": bundle.candidate_sha256,
        "source_head": bundle.source_head,
        "authorization": "controller_action_only_not_deploy_push_or_merge",
        "created_at": datetime.now(UTC).isoformat(),
    }


def write_main_ack(bundle: HandoffBundle) -> dict[str, Any]:
    value = create_main_ack(bundle)
    destination = bundle.run_root / "main-ack.json"
    if destination.exists():
        current = _strict_json(destination, 32 * 1024)
        stable = {key: item for key, item in value.items() if key != "created_at"}
        prior = {key: item for key, item in current.items() if key != "created_at"}
        if prior != stable:
            raise Stage3DeliveryError("Existing MAIN-ACK receipt conflicts with this handoff")
        return current
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return value


def validate_main_ack(bundle: HandoffBundle) -> dict[str, Any]:
    path = bundle.run_root / "main-ack.json"
    try:
        value = _strict_json(path, 32 * 1024)
    except Stage3DeliveryError as exc:
        raise Stage3DeliveryError("Explicit MAIN-ACK receipt is missing") from exc
    expected = create_main_ack(bundle)
    for key in (
        "schema",
        "task_id",
        "status",
        "handoff_sha256",
        "candidate_sha256",
        "source_head",
        "authorization",
    ):
        if value.get(key) != expected[key]:
            raise Stage3DeliveryError("Explicit MAIN-ACK receipt does not bind this handoff")
    return value


def _event_details(bundle: HandoffBundle, reason: str) -> str:
    all_paths = sorted(item["path"] for item in bundle.changed_files)
    unit_evidence = getattr(bundle, "unit_evidence", None)
    qa_contract_version = getattr(bundle, "qa_contract_version", 1)
    if type(qa_contract_version) is not int or qa_contract_version not in {1, 2, 3}:
        qa_contract_version = 1
    detail = {
        "task_id": bundle.task_id,
        "source_head": bundle.source_head,
        "observed_head": bundle.handoff.get("source_head"),
        "candidate_sha256": bundle.candidate_sha256,
        "changed_files": all_paths[:100],
        "changed_files_count": len(all_paths),
        "changed_files_sha256": hashlib.sha256(
            _canonical(all_paths).encode("utf-8")
        ).hexdigest(),
        "reason": reason,
        "review": {
            "status": "APPROVED",
            "sha256": bundle.reviewer_stage_sha256,
        },
        "qa_contract_version": qa_contract_version,
        "unit_evidence_sha256": (
            unit_evidence.get("evidence_sha256")
            if isinstance(unit_evidence, Mapping)
            else None
        ),
        "tests": [
            {"command": command, "status": "completed", "exit_code": 0}
            for command in bundle.expected_commands
        ],
        "working_content_commit": None,
        "dirty": True,
    }
    return json.dumps(detail, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def standard_event_plan(
    bundle: HandoffBundle,
    *,
    reason: str,
    event_types: Sequence[str] = _DEFAULT_EVENTS,
) -> list[dict[str, Any]]:
    if not isinstance(reason, str) or not reason.strip():
        raise Stage3DeliveryError("Delivery events require an explicit reason")
    if isinstance(event_types, (str, bytes)) or not event_types or len(event_types) > len(EVENT_DOCUMENTS):
        raise Stage3DeliveryError("Delivery event selection is empty or unbounded")
    if len(set(event_types)) != len(event_types):
        raise Stage3DeliveryError("Delivery event selection contains duplicates")
    unknown = set(event_types) - set(EVENT_DOCUMENTS)
    if unknown:
        raise Stage3DeliveryError("Requested delivery event is outside the fixed ten-event map")
    details = _event_details(bundle, reason)
    return [
        {
            "event_type": event_type,
            "payload": {
                "summary": f"Stage 3 delivery event: {event_type}",
                "reason": reason,
                "details": details,
                "component": "lads-stage3",
            },
        }
        for event_type in event_types
    ]


def _document_sha(repo: Path, document: str) -> str | None:
    path = repo / document
    if not path.exists():
        return None
    if not _regular_file(path):
        raise Stage3DeliveryError("Required delivery document is not a regular file")
    return _sha_file(path)


def register_delivery_engine(
    bundle: HandoffBundle,
    *,
    journal: Any = None,
    publisher: Any = None,
    knowledge: Any = None,
    backup_root: str | Path | None = None,
) -> AutomationEngine:
    journal = journal or FileJournal.create()
    if publisher is None:
        publisher = DocumentPublisher(
            bundle.repo_root,
            backup_root=backup_root or (default_automation_root() / "document-backups"),
            registered_repository_ref=BASE.repository_ref(bundle.repo_root)
            if hasattr(BASE, "repository_ref")
            else __import__("lads_knowledge.indexer", fromlist=["repository_ref"]).repository_ref(bundle.repo_root),
        )
    engine = AutomationEngine(
        bundle.task_spec,
        journal,
        publisher=publisher,
        knowledge=knowledge,
        repo_root=bundle.repo_root,
        max_retries=3,
    )
    engine.register(
        f"Finish LADS Stage3 task {bundle.task_id}; finalizer receives only explicit MAIN-ACK.",
        session_id=bundle.controller_state.get("session_id"),
    )
    return engine


def accept_handoff(
    bundle: HandoffBundle,
    engine: AutomationEngine,
    *,
    reason: str,
    event_plan: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    validate_main_ack(bundle)
    current = engine.status(bundle.task_id)
    if current.get("status") == "DELIVERY_READY" and current.get("ready") is True:
        return current
    if engine.knowledge is None:
        raise Stage3DeliveryError(
            "Explicit knowledge DB/project binding is required before document delivery"
        )
    for receipt in bundle.worker_stage_receipts:
        record_worker_completed(
            engine.journal,
            task_id=bundle.task_id,
            role=receipt["role"],
            status=receipt["status"],
            provider=receipt["provider"],
            model=receipt["model"],
            result=receipt["result"],
            source_head=bundle.source_head,
            candidate_sha256=receipt["candidate_sha256"],
            stage_metadata=receipt["stage_metadata"],
            stage_ref=receipt["stage_ref"],
        )
    clean_reason = str(reason).strip()[:1000] or "Stage3 MAIN-ACK delivery"
    plan = list(event_plan or standard_event_plan(bundle, reason=clean_reason))
    for item in plan:
        if not isinstance(item, Mapping) or set(item) != {"event_type", "payload"}:
            raise Stage3DeliveryError("Delivery event plan is malformed")
        event_type = item["event_type"]
        payload = item["payload"]
        if not isinstance(event_type, str) or not isinstance(payload, Mapping):
            raise Stage3DeliveryError("Delivery event plan values are malformed")
        clean_payload = dict(payload)
        if event_type in _SEMANTIC_EVENTS:
            document = EVENT_DOCUMENTS[event_type]
            digest = _document_sha(bundle.repo_root, document)
            if digest is None:
                raise Stage3DeliveryError("Semantic event document is missing")
            clean_payload["doc_approval"] = {
                "path": document,
                "approved": True,
                "document_sha256": digest,
            }
        engine.record_event(event_type, clean_payload)
    engine.verify(bundle.receipts)
    required = engine.required_documents()
    expected_old = {
        document: _document_sha(bundle.repo_root, document)
        for document in required
    }
    engine.sync_documents(expected_old, reason=clean_reason)
    return engine.sync_index(reason=clean_reason)


def retry_handoff(engine: AutomationEngine, *, reason: str) -> dict[str, Any]:
    clean_reason = str(reason).strip()[:1000]
    if not clean_reason:
        raise Stage3DeliveryError("Retry reason is required")
    return engine.retry(reason=clean_reason)


def delivery_fingerprint(bundle: HandoffBundle) -> dict[str, Any]:
    unit_evidence = getattr(bundle, "unit_evidence", None)
    qa_contract_version = getattr(bundle, "qa_contract_version", 1)
    if type(qa_contract_version) is not int or qa_contract_version not in {1, 2, 3}:
        qa_contract_version = 1
    return {
        "task_id": bundle.task_id,
        "source_head": bundle.source_head,
        "candidate_sha256": bundle.candidate_sha256,
        "handoff_sha256": _sha_file(bundle.run_root / "finalhandoff.json"),
        "qa_stage_sha256": bundle.qa_stage_sha256,
        "reviewer_stage_sha256": bundle.reviewer_stage_sha256,
        "allowed_paths": list(bundle.task_spec.allowed_changes),
        "expected_commands": list(bundle.expected_commands),
        "qa_contract_version": qa_contract_version,
        "unit_evidence_sha256": (
            unit_evidence.get("evidence_sha256")
            if isinstance(unit_evidence, Mapping)
            else None
        ),
        "main_ack_required": True,
        "simulation": False,
    }

