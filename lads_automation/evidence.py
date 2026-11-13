"""Strict delivery evidence validation for LADS automation."""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from lads_knowledge.database import _is_reparse, _reject_reparse_chain

MAX_RECEIPT_BYTES = 256 * 1024
MAX_RECEIPT_ITEMS = 256
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_KEYS = {
    "stdout",
    "stderr",
    "rawoutput",
    "outputtext",
    "output",
    "outputtail",
    "workeroutput",
    "rawmessage",
    "assistantmessage",
    "transcript",
    "transcripts",
    "messages",
    "dialogue",
    "conversation",
    "prompt",
    "prompttext",
    "apikey",
    "token",
    "secret",
    "password",
    "privatekey",
    "authorization",
    "credential",
    "credentials",
    "lastassistantmessage",
    "builderbuilt",
    "builderselfreport",
}


class EvidenceError(ValueError):
    """Evidence is malformed, unbounded, or does not satisfy the gate."""


class QAEvidenceError(EvidenceError):
    """Independent QA evidence is missing or invalid."""


class ReviewEvidenceError(EvidenceError):
    """Independent final-review evidence is missing or invalid."""


def _sha(value: Any, label: str, *, lengths: tuple[int, ...] = (64,)) -> str:
    if not isinstance(value, str):
        raise EvidenceError(f"{label} must be a string")
    text = value.strip().lower()
    if len(text) not in lengths or not all(character in "0123456789abcdef" for character in text):
        expected = " or ".join(str(length) for length in lengths)
        raise EvidenceError(f"{label} must be a lowercase {expected}-hex digest")
    return text


def _uuid(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise EvidenceError(f"{label} must be a string")
    text = value.strip().lower()
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError) as exc:
        raise EvidenceError(f"{label} must be a UUID") from exc
    if str(parsed) != text:
        raise EvidenceError(f"{label} must use canonical lowercase UUID form")
    return text


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_relative(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise EvidenceError(f"{label} must be a string")
    text = value.strip()
    if not text or len(text) > 240 or "\x00" in text or "\\" in text:
        raise EvidenceError(f"{label} must be a bounded POSIX relative path")
    path = Path(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise EvidenceError(f"{label} must be contained and traversal-free")
    return path.as_posix()


@dataclass(frozen=True)
class TaskSpec:
    """Immutable candidate and scope identity used by all delivery checks."""

    task_id: str
    source_head: str
    candidate_sha256: str
    allowed_changes: tuple[str, ...]
    expected_qa_commands: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _uuid(self.task_id, "task_id"))
        object.__setattr__(self, "source_head", _sha(self.source_head, "source_head", lengths=(40, 64)))
        object.__setattr__(self, "candidate_sha256", _sha(self.candidate_sha256, "candidate_sha256"))
        allowed = tuple(_safe_relative(item, "allowed change") for item in self.allowed_changes)
        if not allowed or len(allowed) > 512 or len(set(allowed)) != len(allowed):
            raise EvidenceError("Allowed changes must be nonempty, bounded, and unique")
        object.__setattr__(self, "allowed_changes", allowed)
        if not all(isinstance(item, str) for item in self.expected_qa_commands):
            raise EvidenceError("Expected QA commands must be strings")
        commands = tuple(item.strip() for item in self.expected_qa_commands)
        if (
            not commands
            or len(commands) > 32
            or len(set(commands)) != len(commands)
            or any(not item or len(item) > 1000 or "\x00" in item for item in commands)
        ):
            raise EvidenceError("Expected QA commands must be nonempty, bounded, and unique")
        object.__setattr__(self, "expected_qa_commands", commands)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "source_head": self.source_head,
            "candidate_sha256": self.candidate_sha256,
            "allowed_changes": list(self.allowed_changes),
            "expected_qa_commands": list(self.expected_qa_commands),
        }


@dataclass(frozen=True)
class EvidenceProof:
    task_id: str
    candidate_sha256: str
    controller_sha256: str
    qa_sha256: str
    reviewer_sha256: str
    commands: tuple[str, ...]
    simulated: bool = False
    acceptance_mode: str = "independent"
    authorization_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.acceptance_mode not in {"independent", "main-authorized-exception"}:
            raise EvidenceError("Unknown acceptance mode")
        if self.acceptance_mode == "main-authorized-exception":
            _sha(self.authorization_sha256, "authorization_sha256")
        elif self.authorization_sha256 is not None:
            raise EvidenceError("Independent acceptance cannot carry a main exception grant")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "lads-stage-acceptance-v1",
            "task_id": self.task_id,
            "candidate_sha256": self.candidate_sha256,
            "controller_sha256": self.controller_sha256,
            "qa_sha256": self.qa_sha256,
            "reviewer_sha256": self.reviewer_sha256,
            "commands": list(self.commands),
            "simulated": self.simulated,
            "acceptance_mode": self.acceptance_mode,
            "authorization_sha256": self.authorization_sha256,
            "reviewer_independent": self.acceptance_mode == "independent",
            "qa_review_verified": True,
            "whole_task_complete": False,
        }


@dataclass(frozen=True)
class ControllerReceipts:
    """Bounded receipts loaded through controller-designated hashed stage refs."""

    controller: dict[str, Any]
    qa: dict[str, Any]
    reviewer: dict[str, Any]
    controller_sha256: str
    qa_sha256: str
    reviewer_sha256: str


def _normalized_key(key: str) -> str:
    return re.sub(r"[-_]", "", key).casefold()


def _validate_shape(value: Any, *, depth: int = 0) -> None:
    if depth > 8:
        raise EvidenceError("Receipt nesting exceeds the allowed depth")
    if isinstance(value, dict):
        if len(value) > MAX_RECEIPT_ITEMS:
            raise EvidenceError("Receipt object has too many fields")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 100:
                raise EvidenceError("Receipt key is malformed")
            if _normalized_key(key) in _FORBIDDEN_KEYS:
                raise EvidenceError("Raw output, dialogue, prompts, or credentials are not accepted")
            _validate_shape(item, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > MAX_RECEIPT_ITEMS:
            raise EvidenceError("Receipt array has too many entries")
        for item in value:
            _validate_shape(item, depth=depth + 1)
    elif isinstance(value, str):
        if len(value) > 8000 or "\x00" in value:
            raise EvidenceError("Receipt string exceeds its bound or contains NUL")
    elif value is not None and type(value) not in {bool, int, float}:
        raise EvidenceError("Receipt contains an unsupported JSON value")
    elif type(value) is float and not math.isfinite(value):
        raise EvidenceError("Receipt contains a non-finite number")
    if depth == 0 and len(_canonical(value).encode("utf-8")) > MAX_RECEIPT_BYTES:
        raise EvidenceError("Receipt exceeds its bounded JSON size")


def _strict_parse(data: bytes, label: str) -> dict[str, Any]:
    if len(data) > MAX_RECEIPT_BYTES:
        raise EvidenceError(f"{label} exceeds its size limit")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise EvidenceError(f"{label} contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        parsed = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                EvidenceError(f"{label} contains non-JSON numeric constants")
            ),
        )
    except (UnicodeError, ValueError) as exc:
        raise EvidenceError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise EvidenceError(f"{label} must be an object")
    _validate_shape(parsed)
    return parsed


def _read_contained(root: Path, relative: str) -> tuple[dict[str, Any], str]:
    canonical = _safe_relative(relative, "stage path")
    selected = root / canonical
    _reject_reparse_chain(selected)
    try:
        resolved = selected.resolve(strict=True)
    except OSError as exc:
        raise EvidenceError("Stage receipt is not a readable contained file") from exc
    if resolved != root and root not in resolved.parents:
        raise EvidenceError("Stage receipt escapes its containment root")
    if selected.is_symlink() or _is_reparse(selected) or not selected.is_file():
        raise EvidenceError("Stage receipt must be a regular non-reparse file")
    if selected.stat().st_size > MAX_RECEIPT_BYTES:
        raise EvidenceError("Stage receipt exceeds its size limit")
    data = selected.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    return _strict_parse(data, canonical), digest


def load_controller_receipts(
    controller_path: str | Path,
    containment_root: str | Path,
    *,
    max_bytes: int = MAX_RECEIPT_BYTES,
) -> ControllerReceipts:
    """Load controller, QA, and final-reviewer receipts only via hashed stage refs."""
    if not isinstance(max_bytes, int) or max_bytes <= 0 or max_bytes > MAX_RECEIPT_BYTES:
        raise EvidenceError("Receipt byte limit is invalid")
    root = Path(containment_root)
    if not root.is_absolute():
        raise EvidenceError("Receipt containment root must be absolute")
    _reject_reparse_chain(root)
    root = root.resolve(strict=True)
    selected = Path(controller_path)
    if not selected.is_absolute():
        selected = root / selected
    _reject_reparse_chain(selected)
    resolved = selected.resolve(strict=True)
    if resolved != root and root not in resolved.parents:
        raise EvidenceError("Controller receipt escapes its containment root")
    if selected.is_symlink() or _is_reparse(selected) or not selected.is_file():
        raise EvidenceError("Controller receipt must be a regular non-reparse file")
    controller_data = selected.read_bytes()
    if len(controller_data) > max_bytes:
        raise EvidenceError("Controller receipt exceeds its size limit")
    controller = _strict_parse(controller_data, "controller receipt")
    refs = controller.get("stage_files")
    if not isinstance(refs, dict) or set(refs) != {"qa", "reviewer"}:
        raise EvidenceError("Controller must provide exact QA and reviewer stage refs")

    loaded: dict[str, tuple[dict[str, Any], str]] = {}
    for role in ("qa", "reviewer"):
        ref = refs[role]
        if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
            raise EvidenceError("Controller stage refs must contain path and sha256 only")
        payload, actual = _read_contained(root, _safe_relative(ref["path"], f"{role} path"))
        expected = _sha(ref["sha256"], f"{role} stage sha256")
        if actual != expected:
            raise EvidenceError(f"{role} stage digest does not match the controller reference")
        loaded[role] = (payload, actual)
    return ControllerReceipts(
        controller=controller,
        qa=loaded["qa"][0],
        reviewer=loaded["reviewer"][0],
        controller_sha256=hashlib.sha256(controller_data).hexdigest(),
        qa_sha256=loaded["qa"][1],
        reviewer_sha256=loaded["reviewer"][1],
    )


def _receipt_value(receipt: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in receipt:
            return receipt[name]
    return None


def _validate_controller(receipts: ControllerReceipts, spec: TaskSpec) -> None:
    controller = receipts.controller
    if _uuid(controller.get("task_id"), "controller task_id") != spec.task_id:
        raise EvidenceError("Controller task ID does not match TaskSpec")
    status = str(_receipt_value(controller, "status", "validation_status") or "").upper()
    if status not in {"VALID", "PASS"}:
        raise EvidenceError("Controller did not validate the stage receipts")
    candidate = _sha(_receipt_value(controller, "candidate_sha256", "candidate_sha"), "controller candidate")
    if candidate != spec.candidate_sha256:
        raise EvidenceError("Controller candidate digest does not match TaskSpec")
    refs = controller.get("stage_files")
    if (
        _sha(refs["qa"]["sha256"], "QA stage digest") != receipts.qa_sha256
        or _sha(refs["reviewer"]["sha256"], "reviewer stage digest") != receipts.reviewer_sha256
    ):
        raise EvidenceError("Loaded stage receipts do not match controller references")


def _validate_qa_impl(qa: dict[str, Any], spec: TaskSpec) -> None:
    if _uuid(qa.get("task_id"), "QA task_id") != spec.task_id:
        raise EvidenceError("QA task ID does not match TaskSpec")
    if str(qa.get("role") or "").casefold() != "qa-tester":
        raise EvidenceError("QA receipt must be from the independent qa-tester role")
    if qa.get("status") != "PASS":
        raise EvidenceError("QA receipt must have exact PASS status")
    candidate = _sha(_receipt_value(qa, "candidate_sha256", "candidate_sha"), "QA candidate")
    if candidate != spec.candidate_sha256:
        raise EvidenceError("QA candidate digest does not match TaskSpec")
    commands = qa.get("command_executions")
    if not isinstance(commands, list) or len(commands) != len(spec.expected_qa_commands):
        raise EvidenceError("QA must execute every expected command exactly once")
    seen: dict[str, dict[str, Any]] = {}
    for execution in commands:
        if not isinstance(execution, dict):
            raise EvidenceError("QA command execution must be an object")
        command = str(execution.get("command") or "").strip()
        if command not in spec.expected_qa_commands:
            raise EvidenceError("QA contains an unexpected or caller-supplied command")
        if command in seen:
            raise EvidenceError("QA command appears more than once")
        evidence_type = str(
            _receipt_value(execution, "execution_type", "source", "evidence_type") or ""
        ).casefold()
        if evidence_type != "command_execution":
            raise EvidenceError("QA command lacks actual command_execution evidence")
        if str(execution.get("status") or "").casefold() != "completed":
            raise EvidenceError("QA command is missing, skipped, or incomplete")
        if execution.get("exit_code") != 0 or type(execution.get("exit_code")) is not int:
            raise EvidenceError("QA command must have an actual integer exit_code of 0")
        execution_candidate = _sha(
            _receipt_value(execution, "candidate_sha256", "candidate_sha"),
            "QA execution candidate",
        )
        if execution_candidate != spec.candidate_sha256:
            raise EvidenceError("QA command ran against a different candidate")
        seen[command] = execution
    if set(seen) != set(spec.expected_qa_commands):
        raise EvidenceError("QA command set does not match the immutable expected commands")


def _validate_qa(qa: dict[str, Any], spec: TaskSpec) -> None:
    try:
        _validate_qa_impl(qa, spec)
    except EvidenceError as exc:
        if isinstance(exc, QAEvidenceError):
            raise
        raise QAEvidenceError(str(exc)) from exc


def _validate_reviewer_impl(reviewer: dict[str, Any], spec: TaskSpec) -> None:
    if _uuid(reviewer.get("task_id"), "reviewer task_id") != spec.task_id:
        raise EvidenceError("Reviewer task ID does not match TaskSpec")
    if str(reviewer.get("role") or "").casefold() != "reviewer":
        raise EvidenceError("Final review must come from the reviewer role")
    if reviewer.get("status") != "APPROVED":
        raise EvidenceError("Final reviewer status must be exact APPROVED")
    if reviewer.get("independent") is not True:
        raise EvidenceError("Reviewer receipt is not marked independent")
    if str(_receipt_value(reviewer, "review_type", "review_scope") or "").casefold() not in {
        "final",
        "final_diff",
        "full_diff",
    }:
        raise EvidenceError("Reviewer receipt is not an independent final review")
    candidate = _sha(_receipt_value(reviewer, "candidate_sha256", "candidate_sha"), "reviewer candidate")
    if candidate != spec.candidate_sha256:
        raise EvidenceError("Reviewer candidate digest does not match TaskSpec")


def _validate_reviewer(reviewer: dict[str, Any], spec: TaskSpec) -> None:
    try:
        _validate_reviewer_impl(reviewer, spec)
    except EvidenceError as exc:
        if isinstance(exc, ReviewEvidenceError):
            raise
        raise ReviewEvidenceError(str(exc)) from exc


def _as_controller_receipts(
    qa: dict[str, Any],
    reviewer: dict[str, Any],
    controller: dict[str, Any],
) -> ControllerReceipts:
    _validate_shape(qa)
    _validate_shape(reviewer)
    _validate_shape(controller)
    refs = controller.get("stage_files")
    if not isinstance(refs, dict):
        raise EvidenceError("Controller stage_files are required")
    return ControllerReceipts(
        controller=controller,
        qa=qa,
        reviewer=reviewer,
        controller_sha256=_digest(controller),
        qa_sha256=_digest(qa),
        reviewer_sha256=_digest(reviewer),
    )


def validate_stage_acceptance(
    spec: TaskSpec,
    receipts_or_qa: ControllerReceipts | dict[str, Any] | tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
    reviewer: dict[str, Any] | None = None,
    controller: dict[str, Any] | None = None,
    *,
    simulation: bool = False,
) -> EvidenceProof:
    """Validate controller-bound QA and final review evidence.

    ``simulation=True`` is an explicit unit-fixture mode. It never turns a
    Builder claim into real command evidence and the returned proof remains
    marked simulated.
    """
    if not isinstance(spec, TaskSpec):
        raise EvidenceError("A validated TaskSpec is required")
    if isinstance(receipts_or_qa, ControllerReceipts):
        if reviewer is not None or controller is not None:
            raise EvidenceError("Do not mix loaded and separate controller receipts")
        receipts = receipts_or_qa
    else:
        if isinstance(receipts_or_qa, (tuple, list)) and reviewer is None and controller is None:
            if len(receipts_or_qa) != 3 or not all(isinstance(item, dict) for item in receipts_or_qa):
                raise EvidenceError("Receipt tuple must contain QA, reviewer, and controller objects")
            receipts_or_qa, reviewer, controller = receipts_or_qa
        if not isinstance(receipts_or_qa, dict) or not isinstance(reviewer, dict) or not isinstance(controller, dict):
            raise EvidenceError("QA, reviewer, and controller receipts are all required")
        receipts = _as_controller_receipts(receipts_or_qa, reviewer, controller)

    _validate_controller(receipts, spec)
    if not simulation and any(
        bool(item.get("simulation"))
        for item in (receipts.controller, receipts.qa, receipts.reviewer)
    ):
        raise EvidenceError("Simulation evidence cannot satisfy real acceptance")
    _validate_qa(receipts.qa, spec)
    _validate_reviewer(receipts.reviewer, spec)
    return EvidenceProof(
        task_id=spec.task_id,
        candidate_sha256=spec.candidate_sha256,
        controller_sha256=receipts.controller_sha256,
        qa_sha256=receipts.qa_sha256,
        reviewer_sha256=receipts.reviewer_sha256,
        commands=spec.expected_qa_commands,
        simulated=bool(simulation),
    )


def receipt_digest(receipt: dict[str, Any]) -> str:
    """Return the stable canonical digest used for synthetic controller refs."""
    _validate_shape(receipt)
    return _digest(receipt)
