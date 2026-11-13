"""Controller-owned unit test execution and strict v2 QA receipts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import qa

UNIT_REPORT_SCHEMA = "lads-stage3-unit-broker-v1"
UNIT_CONTEXT_SCHEMA = "lads-stage3-unit-context-v1"
UNIT_CONTRACT_VERSION = 2
UNIT_REPORT_NAME = "qa-unit-current.json"
UNIT_CONTEXT_NAME = "qa-unit-context.json"
UNIT_SUITES = (
    ("automation", qa.QA_TEST_COMMANDS[0]),
    ("knowledge", qa.QA_TEST_COMMANDS[1]),
)
UNIT_VERIFY_COMMANDS = tuple(
    f"python -B scripts/lads_stage3.py verify-unit {name} "
    "../../qa-unit-current.json"
    for name, _command in UNIT_SUITES
)
STAGE4_QA_COMMANDS = (*UNIT_VERIFY_COMMANDS, qa.BROKER_VERIFY_COMMAND)
LEGACY_STAGE3_QA_COMMANDS = qa.STAGE3_QA_COMMANDS
SPLIT_QA_COMMANDS = tuple(
    f"python -B ../../split-verifier.py {suite} ../../qa-unit-current.json"
    for suite in ("automation", "knowledge", "lads")
) + ("python -B ../../split-verifier.py broker ../../qa-broker-current.json",)

_REPORT_KEYS = frozenset(
    {
        "schema",
        "qa_contract_version",
        "task_id",
        "source_head",
        "source_inventory_sha256",
        "simulation",
        "network_access",
        "candidate_unchanged",
        "working_copy_unchanged",
        "ready",
        "suites",
        "receipt_sha256",
    }
)
_CONTEXT_KEYS = frozenset(
    {
        "schema",
        "qa_contract_version",
        "task_id",
        "source_head",
        "source_inventory_sha256",
        "report_sha256",
        "report_receipt_sha256",
    }
)
_SUITE_KEYS = frozenset(
    {
        "id",
        "command",
        "process",
        "test_count",
        "failure_count",
        "error_count",
        "skip_count",
    }
)
_PROCESS_KEYS = frozenset(
    {
        "status",
        "exit_code",
        "timed_out",
        "output_overflow",
        "output_sha256",
    }
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_TASK_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_HEAD_RE = re.compile(r"^[0-9a-f]{40,64}$")
_RAN_RE = re.compile(r"^Ran ([0-9]+) tests? in [0-9]+(?:\.[0-9]+)?s$")
_FAILED_RE = re.compile(r"^FAILED \(([^()]*)\)$")
_OK_COUNTS_RE = re.compile(r"^OK \(([^()]*)\)$")
_COUNT_RE = re.compile(r"^(failures|errors|skipped|expected failures|unexpected successes)=([0-9]+)$")


class UnitQAVerificationError(ValueError):
    """The v2 unit receipt cannot support a real QA PASS."""


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & marker)


def _reject_reparse_chain(path: Path) -> None:
    selected = path.absolute()
    for current in (selected, *selected.parents):
        if current.exists() and _is_reparse(current):
            raise UnitQAVerificationError("Managed path contains a link or reparse point")


def _strict_json(path: Path, *, maximum: int = 1024 * 1024) -> tuple[dict[str, Any], str]:
    if not path.is_file() or _is_reparse(path) or path.stat().st_size > maximum:
        raise UnitQAVerificationError("Unit JSON is missing, unsafe, or oversized")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise UnitQAVerificationError("Unit JSON contains duplicate keys")
            value[key] = item
        return value

    raw = path.read_bytes()
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                UnitQAVerificationError("Unit JSON contains non-JSON numbers")
            ),
        )
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, UnitQAVerificationError):
            raise
        raise UnitQAVerificationError("Unit JSON is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise UnitQAVerificationError("Unit JSON root must be an object")
    return value, hashlib.sha256(raw).hexdigest()


def _parse_counts(raw: str) -> dict[str, int]:
    result: dict[str, int] = {}
    if not raw:
        return result
    for token in raw.split(", "):
        match = _COUNT_RE.fullmatch(token)
        if match is None or match.group(1) in result:
            raise UnitQAVerificationError("Unit test summary is malformed")
        result[match.group(1)] = int(match.group(2))
    return result


def parse_unittest_output(output: str) -> dict[str, Any]:
    """Parse full unittest output without accepting a shortened summary."""
    if not isinstance(output, str) or "\x00" in output:
        raise UnitQAVerificationError("Unit test output is malformed")
    normalized = output.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.splitlines() if line.strip()]
    if len(lines) < 2:
        raise UnitQAVerificationError("Unit test output is truncated")
    ran_matches = [match for line in lines if (match := _RAN_RE.fullmatch(line))]
    if len(ran_matches) != 1:
        raise UnitQAVerificationError("Unit test output lacks one complete Ran summary")
    test_count = int(ran_matches[0].group(1))
    if test_count <= 0:
        raise UnitQAVerificationError("Unit test output reports no tests")

    summary = lines[-1]
    summaries = [
        line
        for line in lines
        if line == "OK"
        or _OK_COUNTS_RE.fullmatch(line) is not None
        or _FAILED_RE.fullmatch(line) is not None
    ]
    if len(summaries) != 1:
        raise UnitQAVerificationError("Unit test output has duplicate or missing summaries")
    failure_count = 0
    error_count = 0
    skip_count = 0
    successful = False
    if summary == "OK":
        successful = True
    else:
        failed = _FAILED_RE.fullmatch(summary)
        maybe_ok = _OK_COUNTS_RE.fullmatch(summary)
        raw_counts = failed.group(1) if failed else maybe_ok.group(1) if maybe_ok else None
        if raw_counts is None:
            raise UnitQAVerificationError("Unit test output summary is malformed")
        counts = _parse_counts(raw_counts)
        if not counts:
            raise UnitQAVerificationError("Unit test output counts are missing")
        failure_count = (
            counts.get("failures", 0)
            + counts.get("expected failures", 0)
            + counts.get("unexpected successes", 0)
        )
        error_count = counts.get("errors", 0)
        skip_count = counts.get("skipped", 0)
        successful = maybe_ok is not None and failure_count == 0 and error_count == 0
    return {
        "successful": successful,
        "test_count": test_count,
        "failure_count": failure_count,
        "error_count": error_count,
        "skip_count": skip_count,
    }


def _process_from_receipt(receipt: Mapping[str, Any], output: str) -> dict[str, Any]:
    exit_code = receipt.get("exit_code")
    timed_out = receipt.get("timed_out") is True
    overflow = receipt.get("output_overflow") is True
    digest = receipt.get("output_sha256")
    valid_digest = isinstance(digest, str) and _SHA_RE.fullmatch(digest) is not None
    parsed = {
        "test_count": 0,
        "failure_count": 0,
        "error_count": 0,
        "skip_count": 0,
    }
    if not valid_digest:
        digest = None
    if timed_out:
        status = "timeout"
    elif overflow or type(exit_code) is not int or not valid_digest:
        status = "error"
    else:
        try:
            parsed = parse_unittest_output(output)
        except UnitQAVerificationError:
            status = "error"
        else:
            if exit_code == 0 and parsed["successful"]:
                status = "completed"
            elif exit_code == 1 and not parsed["successful"]:
                status = "failed"
            else:
                status = "error"
    return {
        "status": status,
        "exit_code": exit_code if type(exit_code) is int else None,
        "timed_out": timed_out,
        "output_overflow": overflow,
        "output_sha256": digest,
    }, parsed


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    _reject_reparse_chain(path.parent)
    if path.exists() and _is_reparse(path):
        raise UnitQAVerificationError("Unit receipt destination is a link or reparse point")
    if path.parent == path.parent.parent:
        raise UnitQAVerificationError("Unit receipt destination cannot be a filesystem root")
    payload = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    if len(payload) > 1024 * 1024:
        raise UnitQAVerificationError("Unit receipt exceeds its bounded size")
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


class UnitQABroker:
    """Run exact unit commands through the controller's trusted executor."""

    def __init__(self, *, executor: Any, work_root: str | Path, timeout: float = 300.0) -> None:
        if not callable(getattr(executor, "run", None)):
            raise UnitQAVerificationError("Unit QA requires the trusted controller executor")
        if not 1 <= timeout <= 300:
            raise UnitQAVerificationError("Unit QA timeout is outside its supported bound")
        root_input = Path(work_root)
        _reject_reparse_chain(root_input)
        root = root_input.resolve(strict=True)
        if _is_reparse(root) or not root.is_dir() or root == root.parent:
            raise UnitQAVerificationError("Unit QA work root is unsafe")
        self.executor = executor
        self.work_root = root
        self.timeout = float(timeout)

    def _copy(self, source: Path, destination: Path) -> dict[str, str]:
        before = qa.source_inventory(source)
        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns(".git"),
            symlinks=False,
        )
        after = qa.source_inventory(destination)
        if before != after:
            raise UnitQAVerificationError("Unit working copy changed candidate bytes")
        return after

    def run(
        self,
        *,
        task_id: str,
        source_head: str,
        candidate_root: str | Path,
        report_path: str | Path,
    ) -> dict[str, Any]:
        if not isinstance(task_id, str) or _TASK_RE.fullmatch(task_id) is None:
            raise UnitQAVerificationError("Unit QA task ID is malformed")
        if not isinstance(source_head, str) or _HEAD_RE.fullmatch(source_head) is None:
            raise UnitQAVerificationError("Unit QA source HEAD is malformed")
        candidate_input = Path(candidate_root)
        _reject_reparse_chain(candidate_input)
        candidate = candidate_input.resolve(strict=True)
        if _is_reparse(candidate) or not candidate.is_dir():
            raise UnitQAVerificationError("Unit QA candidate root is unsafe")
        destination = Path(report_path)
        if not destination.is_absolute():
            destination = self.work_root / destination
        _reject_reparse_chain(destination)
        destination = destination.resolve(strict=False)
        if self.work_root not in destination.parents or destination.name != UNIT_REPORT_NAME:
            raise UnitQAVerificationError("Unit QA report must use the exact managed path")
        if any(destination == root or root in destination.parents for root in (candidate,)):
            raise UnitQAVerificationError("Unit QA report cannot be written inside the candidate")

        original = qa.source_inventory(candidate)
        digest = qa.inventory_digest(source_head, original)
        private = Path(tempfile.mkdtemp(prefix="uq-", dir=self.work_root))
        working = private / "working"
        temp_root = private / "tmp"
        local_app_data = private / "localappdata"
        suites: list[dict[str, Any]] = []
        copy_baseline: dict[str, str] | None = None
        cleanup_error: str | None = None
        try:
            temp_root.mkdir()
            local_app_data.mkdir()
            copy_baseline = self._copy(candidate, working)
            if copy_baseline != original or qa.inventory_digest(source_head, copy_baseline) != digest:
                raise UnitQAVerificationError("Unit QA working copy digest changed")
            environment = {
                "TEMP": str(temp_root),
                "TMP": str(temp_root),
                "LOCALAPPDATA": str(local_app_data),
            }
            for identity, command in UNIT_SUITES:
                try:
                    receipt = self.executor.run(
                        command=command,
                        cwd=str(working),
                        timeout=self.timeout,
                        network_access=False,
                        writable_roots=[
                            str(working),
                            str(temp_root),
                            str(local_app_data),
                        ],
                        environment=environment,
                    )
                    if not isinstance(receipt, dict):
                        raise UnitQAVerificationError("Unit executor returned a non-object receipt")
                    stdout = receipt.get("stdout", "")
                    stderr = receipt.get("stderr", "")
                    output = (
                        (stdout if isinstance(stdout, str) else str(stdout))
                        + "\n"
                        + (stderr if isinstance(stderr, str) else str(stderr))
                    )
                    process, parsed = _process_from_receipt(receipt, output)
                except TimeoutError:
                    process = {
                        "status": "timeout",
                        "exit_code": None,
                        "timed_out": True,
                        "output_overflow": False,
                        "output_sha256": None,
                    }
                    parsed = {
                        "test_count": 0,
                        "failure_count": 0,
                        "error_count": 0,
                        "skip_count": 0,
                    }
                except Exception:
                    process = {
                        "status": "error",
                        "exit_code": None,
                        "timed_out": False,
                        "output_overflow": False,
                        "output_sha256": None,
                    }
                    parsed = {
                        "test_count": 0,
                        "failure_count": 0,
                        "error_count": 0,
                        "skip_count": 0,
                    }
                suites.append(
                    {
                        "id": identity,
                        "command": command,
                        "process": process,
                        "test_count": parsed["test_count"],
                        "failure_count": parsed["failure_count"],
                        "error_count": parsed["error_count"],
                        "skip_count": parsed["skip_count"],
                    }
                )
                if qa.source_inventory(working) != copy_baseline:
                    break
            working_unchanged = (
                copy_baseline is not None
                and qa.source_inventory(working) == copy_baseline
            )
            candidate_unchanged = qa.source_inventory(candidate) == original
        finally:
            try:
                shutil.rmtree(private)
            except OSError as exc:
                cleanup_error = type(exc).__name__
        if cleanup_error is not None:
            raise UnitQAVerificationError("Unit QA private cleanup failed")

        ready = (
            len(suites) == 2
            and all(
                row["id"] == expected_id
                and row["command"] == expected_command
                and row["process"]["status"] == "completed"
                and type(row["process"]["exit_code"]) is int
                and row["process"]["exit_code"] == 0
                and row["process"]["timed_out"] is False
                and row["process"]["output_overflow"] is False
                and isinstance(row["process"]["output_sha256"], str)
                and _SHA_RE.fullmatch(row["process"]["output_sha256"]) is not None
                and type(row["test_count"]) is int
                and row["test_count"] > 0
                and row["failure_count"] == row["error_count"] == row["skip_count"] == 0
                for row, (expected_id, expected_command) in zip(suites, UNIT_SUITES)
            )
            and candidate_unchanged
            and working_unchanged
        )
        body = {
            "schema": UNIT_REPORT_SCHEMA,
            "qa_contract_version": UNIT_CONTRACT_VERSION,
            "task_id": task_id,
            "source_head": source_head,
            "source_inventory_sha256": digest,
            "simulation": False,
            "network_access": False,
            "candidate_unchanged": candidate_unchanged,
            "working_copy_unchanged": working_unchanged,
            "ready": ready,
            "suites": suites,
        }
        report = qa.seal_broker_report(body)
        _atomic_json(destination, report)
        report_sha256 = hashlib.sha256(destination.read_bytes()).hexdigest()
        context = {
            "schema": UNIT_CONTEXT_SCHEMA,
            "qa_contract_version": UNIT_CONTRACT_VERSION,
            "task_id": task_id,
            "source_head": source_head,
            "source_inventory_sha256": digest,
            "report_sha256": report_sha256,
            "report_receipt_sha256": report["receipt_sha256"],
        }
        _atomic_json(destination.with_name(UNIT_CONTEXT_NAME), context)
        return report


def _require_int(value: Any, label: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        raise UnitQAVerificationError(f"{label} is not the required integer count")
    return value


def _verify_suite(row: Any, expected_id: str, expected_command: str) -> dict[str, Any]:
    if not isinstance(row, dict) or set(row) != _SUITE_KEYS:
        raise UnitQAVerificationError("Unit suite receipt keys are missing or unexpected")
    if row.get("id") != expected_id or row.get("command") != expected_command:
        raise UnitQAVerificationError("Unit suite identity or command changed")
    process = row.get("process")
    if not isinstance(process, dict) or set(process) != _PROCESS_KEYS:
        raise UnitQAVerificationError("Unit process receipt keys are missing or unexpected")
    if (
        process.get("status") != "completed"
        or type(process.get("exit_code")) is not int
        or process.get("exit_code") != 0
        or process.get("timed_out") is not False
        or process.get("output_overflow") is not False
        or not isinstance(process.get("output_sha256"), str)
        or _SHA_RE.fullmatch(process["output_sha256"]) is None
    ):
        raise UnitQAVerificationError("Unit process is failed, timed out, or infrastructure-error")
    test_count = _require_int(row.get("test_count"), "Unit test_count", positive=True)
    failure_count = _require_int(row.get("failure_count"), "Unit failure_count")
    error_count = _require_int(row.get("error_count"), "Unit error_count")
    skip_count = _require_int(row.get("skip_count"), "Unit skip_count")
    if failure_count or error_count or skip_count:
        raise UnitQAVerificationError("Unit suite contains failures, errors, or skips")
    return {
        "id": expected_id,
        "command": expected_command,
        "status": "completed",
        "exit_code": 0,
        "timed_out": False,
        "output_overflow": False,
        "output_sha256": process["output_sha256"],
        "test_count": test_count,
        "failure_count": 0,
        "error_count": 0,
        "skip_count": 0,
    }


def load_and_verify_unit_report(
    path: str | Path,
    *,
    candidate_root: str | Path,
    task_id: str | None = None,
    source_head: str | None = None,
    expected_inventory_sha256: str | None = None,
    expected_suite: str | None = None,
) -> dict[str, Any]:
    """Validate the exact v2 report/context pair against actual candidate bytes."""
    candidate_input = Path(candidate_root)
    _reject_reparse_chain(candidate_input)
    candidate = candidate_input.resolve(strict=True)
    if _is_reparse(candidate) or not candidate.is_dir():
        raise UnitQAVerificationError("Unit candidate root is unsafe")
    selected = Path(path)
    if not selected.is_absolute():
        selected = candidate / selected
    _reject_reparse_chain(selected)
    selected = selected.resolve(strict=True)
    if selected.name != UNIT_REPORT_NAME or _is_reparse(selected):
        raise UnitQAVerificationError("Unit report is not the exact regular managed file")
    context_path = selected.with_name(UNIT_CONTEXT_NAME)
    _reject_reparse_chain(context_path)
    if _is_reparse(context_path) or not context_path.is_file():
        raise UnitQAVerificationError("Unit context is missing or unsafe")
    report, report_sha256 = _strict_json(selected)
    context, context_sha256 = _strict_json(context_path, maximum=32 * 1024)
    if set(context) != _CONTEXT_KEYS:
        raise UnitQAVerificationError("Unit context keys are missing or unexpected")
    if (
        context.get("schema") != UNIT_CONTEXT_SCHEMA
        or context.get("qa_contract_version") != UNIT_CONTRACT_VERSION
    ):
        raise UnitQAVerificationError("Unit context schema or contract version changed")
    if context.get("report_sha256") != report_sha256:
        raise UnitQAVerificationError("Unit report raw digest does not match its context")
    if set(report) != _REPORT_KEYS:
        raise UnitQAVerificationError("Unit report keys are missing or unexpected")
    if (
        report.get("schema") != UNIT_REPORT_SCHEMA
        or report.get("qa_contract_version") != UNIT_CONTRACT_VERSION
        or report.get("simulation") is not False
        or report.get("network_access") is not False
        or report.get("candidate_unchanged") is not True
        or report.get("working_copy_unchanged") is not True
        or report.get("ready") is not True
    ):
        raise UnitQAVerificationError("Unit report is not an actual ready v2 receipt")
    if (
        not isinstance(report.get("receipt_sha256"), str)
        or _SHA_RE.fullmatch(report["receipt_sha256"]) is None
        or report["receipt_sha256"] != qa.report_receipt(report)
        or context.get("report_receipt_sha256") != report["receipt_sha256"]
    ):
        raise UnitQAVerificationError("Unit report receipt does not match its bounded bytes")

    identity = {
        "task_id": report.get("task_id"),
        "source_head": report.get("source_head"),
        "source_inventory_sha256": report.get("source_inventory_sha256"),
    }
    for key, value in identity.items():
        if context.get(key) != value:
            raise UnitQAVerificationError("Unit report and context identity differ")
    if (
        not isinstance(identity["task_id"], str)
        or _TASK_RE.fullmatch(identity["task_id"]) is None
        or not isinstance(identity["source_head"], str)
        or _HEAD_RE.fullmatch(identity["source_head"]) is None
        or not isinstance(identity["source_inventory_sha256"], str)
        or _SHA_RE.fullmatch(identity["source_inventory_sha256"]) is None
    ):
        raise UnitQAVerificationError("Unit receipt identity is malformed")
    if task_id is not None and identity["task_id"] != task_id:
        raise UnitQAVerificationError("Unit report is bound to a different task")
    if source_head is not None and identity["source_head"] != source_head:
        raise UnitQAVerificationError("Unit report is bound to a different source HEAD")

    actual_inventory = qa.source_inventory(candidate)
    actual_digest = qa.inventory_digest(identity["source_head"], actual_inventory)
    if actual_digest != identity["source_inventory_sha256"]:
        raise UnitQAVerificationError("Unit report does not match actual candidate inventory")
    if expected_inventory_sha256 is not None and actual_digest != expected_inventory_sha256:
        raise UnitQAVerificationError("Unit report does not match the pinned candidate digest")

    suites = report.get("suites")
    if not isinstance(suites, list) or len(suites) != len(UNIT_SUITES):
        raise UnitQAVerificationError("Unit report does not contain exactly two ordered suites")
    verified_suites = [
        _verify_suite(row, expected_id, expected_command)
        for row, (expected_id, expected_command) in zip(suites, UNIT_SUITES)
    ]
    if expected_suite is not None and expected_suite not in {item[0] for item in UNIT_SUITES}:
        raise UnitQAVerificationError("Requested unit suite is not part of the v2 contract")
    evidence_body = {
        "task_id": identity["task_id"],
        "source_head": identity["source_head"],
        "source_inventory_sha256": actual_digest,
        "report_sha256": report_sha256,
        "report_receipt_sha256": report["receipt_sha256"],
        "context_sha256": context_sha256,
        "suites": verified_suites,
    }
    evidence_sha256 = hashlib.sha256(qa.canonical(evidence_body)).hexdigest()
    return {
        "status": "PASS",
        "suite": expected_suite,
        **evidence_body,
        "evidence_sha256": evidence_sha256,
    }


def verify_unit_for_run(
    suite: str,
    report_path: str | Path,
    *,
    candidate_root: str | Path,
) -> dict[str, Any]:
    """Read-only CLI verifier for one suite in the exact QA run layout."""
    if suite not in {item[0] for item in UNIT_SUITES}:
        raise UnitQAVerificationError("Unit suite must be automation or knowledge")
    selected = Path(report_path)
    if not selected.is_absolute():
        selected = Path(candidate_root) / selected
    _reject_reparse_chain(selected)
    selected = selected.resolve(strict=True)
    managed_root = selected.parent
    _reject_reparse_chain(managed_root)
    state, _state_sha256 = _strict_json(managed_root / "state.json", maximum=2 * 1024 * 1024)
    task_id = state.get("task_id")
    source_head = state.get("source_head")
    builds = state.get("builds")
    if (
        not isinstance(task_id, str)
        or not isinstance(source_head, str)
        or type(builds) is not int
        or builds < 1
    ):
        raise UnitQAVerificationError("Managed run state lacks task/head/build identity")
    candidate_input = Path(candidate_root)
    _reject_reparse_chain(candidate_input)
    candidate = candidate_input.resolve(strict=True)
    expected_candidate = managed_root / "qa" / f"build-{builds}"
    if candidate != expected_candidate.resolve(strict=True):
        raise UnitQAVerificationError("Unit verifier candidate is not the exact QA build copy")
    return load_and_verify_unit_report(
        selected,
        candidate_root=candidate,
        task_id=task_id,
        source_head=source_head,
        expected_suite=suite,
    )


def contract_version(commands: Sequence[str]) -> int | None:
    selected = tuple(commands)
    if selected == LEGACY_STAGE3_QA_COMMANDS:
        return 1
    if selected == STAGE4_QA_COMMANDS:
        return UNIT_CONTRACT_VERSION
    if selected == SPLIT_QA_COMMANDS:
        return 3
    return None
