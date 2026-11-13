"""Opt-in Stage 3 adapter over the protected Stage 1 coordinator."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

from . import qa, unit_qa

ROOT = Path(__file__).resolve().parents[1]
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"(?i)\b(DEEPSEEK_[A-Z0-9_]*KEY\s*[=:]\s*)\S+"),
    re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
)
_WORKER_ROLES = frozenset({"scanner", "reviewer", "builder", "qa-tester"})
_BUILTIN_PROVIDERS = frozenset({"lifeos_deepseek", "deepseek"})


def load_orchestrator(path: str | Path | None = None) -> ModuleType:
    selected = Path(path) if path is not None else ROOT / "scripts" / "lads_orchestrator.py"
    name = "lads_stage1_orchestrator_for_stage3"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, selected)
    if spec is None or spec.loader is None:
        raise RuntimeError("Stage 1 coordinator could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_orchestrator()


def prepare_python_runtime() -> str:
    """Keep immutable 'python' commands on the explicitly selected interpreter.

    Only this controller process PATH changes. Original entries, credentials,
    model/provider settings and the resolved Codex executable remain intact.
    """
    runtime = Path(sys.executable).absolute()
    if not runtime.is_file():
        raise RuntimeError("Selected engineering Python is unavailable")
    original = os.environ.get("PATH", "")
    runtime_bin = str(runtime.parent)
    entries = original.split(os.pathsep)
    prepared = original if entries and os.path.normcase(entries[0]) == os.path.normcase(runtime_bin) else runtime_bin + os.pathsep + original
    before = shutil.which("codex", path=original)
    after = shutil.which("codex", path=prepared)
    if before != after:
        raise RuntimeError("Python runtime setup would change the Codex executable")
    os.environ["PATH"] = prepared
    return str(runtime)


def _redact_complete(text: str) -> str:
    value = text
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(
            r"\1[REDACTED]" if pattern.groups else "[REDACTED]",
            value,
        )
    return value


def _structured_error_events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip() or len(line.encode("utf-8")) > BASE.MAX_LINE:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, UnicodeError):
            continue
        if not isinstance(event, dict) or event.get("type") not in {"error", "turn.failed"}:
            continue
        metadata: dict[str, Any] = {"type": event["type"]}
        for key in ("code", "status", "message"):
            value = event.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                metadata[key] = _redact_complete(str(value))[:500]
        events.append(metadata)
        if len(events) == 20:
            break
    return events


def collect_process_failure(result: Any, *, tail_limit: int = 4000) -> dict[str, Any]:
    """Share the actual execution diagnostics; never truncate a prefix first."""
    return BASE.RUNTIME.failure_diagnostics(result, _SECRET_PATTERNS, tail_limit)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def worker_completed_payload(
    *,
    role: str,
    status: str,
    provider: str,
    model: str,
    result: Any,
    source_head: str,
    candidate_sha256: str | None = None,
    stage_metadata: Mapping[str, Any] | None = None,
    stage_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a controller-domain event; this is never SubagentStop."""
    if role not in _WORKER_ROLES:
        raise ValueError("Unknown independent worker role")
    if provider not in _BUILTIN_PROVIDERS:
        raise ValueError("worker_completed is limited to the assigned DeepSeek provider")
    if not isinstance(model, str) or not model.startswith("deepseek-"):
        raise ValueError("worker_completed requires a DeepSeek model")
    if not isinstance(status, str) or not status:
        raise ValueError("Worker status is missing")
    failure = collect_process_failure(result)
    payload = {
        "event_source": "controller",
        "worker_type": "independent-cli",
        "native_subagent_stop": False,
        "role": role,
        "status": status[:100],
        "provider": provider,
        "model": model[:100],
        "source_head": source_head,
        "candidate_sha256": candidate_sha256,
        "process": failure,
    }
    if candidate_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", candidate_sha256):
        raise ValueError("Candidate digest is malformed")
    if stage_metadata is not None:
        if not isinstance(stage_metadata, Mapping):
            raise ValueError("Worker stage metadata must be a mapping")
        allowed = {
            "role",
            "provider",
            "model",
            "sandbox",
            "start_time",
            "end_time",
            "input_tokens",
            "output_tokens",
            "config_snapshot_sha256",
            "home_mode",
            "home_sha256",
            "authority_sha256",
        }
        if set(stage_metadata) != allowed:
            raise ValueError("Worker stage metadata fields are incomplete or unexpected")
        clean_stage: dict[str, Any] = {}
        for name in allowed:
            value = stage_metadata[name]
            if name in {"input_tokens", "output_tokens"}:
                if value is not None and (type(value) is not int or value < 0):
                    raise ValueError(
                        "Worker token metadata must be a non-negative integer or null"
                    )
                clean_stage[name] = value
                continue
            if not isinstance(value, str) or not value or len(value) > 200:
                raise ValueError("Worker stage metadata text is malformed")
            clean_stage[name] = value
        if (
            clean_stage["role"] != role
            or clean_stage["provider"] != provider
            or clean_stage["model"] != model
        ):
            raise ValueError("Worker stage identity does not match the controller receipt")
        payload["stage"] = clean_stage
    if stage_ref is not None:
        if (
            not isinstance(stage_ref, Mapping)
            or set(stage_ref) != {"path", "sha256"}
            or not isinstance(stage_ref.get("path"), str)
            or not stage_ref["path"]
            or len(stage_ref["path"]) > 500
            or "\\" in stage_ref["path"]
            or stage_ref["path"].startswith("/")
            or any(part in {"", ".", ".."} for part in stage_ref["path"].split("/"))
            or not isinstance(stage_ref.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", stage_ref["sha256"])
        ):
            raise ValueError("Worker stage reference is malformed")
        payload["stage_ref"] = {
            "path": stage_ref["path"],
            "sha256": stage_ref["sha256"],
        }
    return payload


def record_worker_completed(
    journal: Any,
    *,
    task_id: str,
    role: str,
    status: str,
    provider: str,
    model: str,
    result: Any,
    source_head: str,
    candidate_sha256: str | None = None,
    stage_metadata: Mapping[str, Any] | None = None,
    stage_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = worker_completed_payload(
        role=role,
        status=status,
        provider=provider,
        model=model,
        result=result,
        source_head=source_head,
        candidate_sha256=candidate_sha256,
        stage_metadata=stage_metadata,
        stage_ref=stage_ref,
    )
    identity = str(
        uuid.uuid5(
            uuid.UUID(task_id),
            "worker_completed|"
            + hashlib.sha256(_canonical(payload)).hexdigest(),
        )
    )
    return journal.append_event(task_id, identity, "worker_completed", payload)


class Stage3Orchestrator(BASE.Orchestrator):
    """Adds actual broker browser/QA receipts without changing the base class.

    ``manifest.applicability["browser"]`` remains the legacy base-protocol
    field. It is still false because the Stage 1 coordinator has no native
    browser executor. Stage 3 applicability is represented separately by the
    mandatory broker and verifier below.
    """

    def __init__(
        self,
        *args: Any,
        broker: Any | None = None,
        unit_broker: Any | None = None,
        qa_commands: Any = unit_qa.STAGE4_QA_COMMANDS,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.broker = broker
        self.unit_broker = unit_broker
        self.qa_commands = tuple(qa_commands)
        if unit_qa.contract_version(self.qa_commands) not in {2, 3}:
            raise ValueError("Unknown mandatory QA command contract")
        self._broker_evidence: dict[str, Any] | None = None
        self._unit_evidence: dict[str, Any] | None = None

    @staticmethod
    def stage3_requirements() -> dict[str, Any]:
        return {
            "base_browser_supported": False,
            "stage3_browser_supported": True,
            "stage4_unit_supported": True,
            "qa_test_commands": list(unit_qa.STAGE4_QA_COMMANDS),
            "mandatory_verifier_command": qa.BROKER_VERIFY_COMMAND,
            "unit_verifier_commands": list(unit_qa.UNIT_VERIFY_COMMANDS),
            "real_authentication": "NOT_IMPLEMENTED",
            "real_ai_tools": "NOT_IMPLEMENTED",
        }

    def prepare_broker(
        self,
        *,
        task_id: str,
        source_head: str,
        candidate_dir: str | Path,
        qa_dir: str | Path,
        build_number: int,
    ) -> dict[str, Any]:
        if self.run_root is None:
            raise BASE.Blocked("Stage3 broker requires a bound coordinator run root")
        if self.broker is None or not callable(getattr(self.broker, "run", None)):
            raise BASE.Blocked("Stage3 requires an independent credential-free broker")
        qa_root = Path(qa_dir).resolve(strict=True)
        run_root = Path(self.run_root).resolve(strict=True)
        if run_root not in qa_root.parents or qa_root == run_root:
            raise BASE.Blocked("QA directory escapes the managed run root")
        report_path = run_root / "qa-broker-current.json"
        broker_error = None
        try:
            result = self.broker.run(
                task_id=task_id,
                source_head=source_head,
                candidate_root=Path(candidate_dir),
                build_number=build_number,
                report_path=report_path,
            )
        except Exception as exc:
            result = {}
            broker_error = type(exc).__name__ + ": " + str(exc)[:800]
        report = result if isinstance(result, dict) else {}
        evidence = {
            "report_path": str(report_path),
            "report_relative": "../../qa-broker-current.json",
            "report": report,
            "commands": list(self.qa_commands),
            "broker_error": broker_error,
            "report_present": report_path.is_file(),
        }
        self._broker_evidence = evidence
        return evidence

    def prepare_unit(
        self,
        *,
        task_id: str,
        source_head: str,
        candidate_dir: str | Path,
        qa_dir: str | Path,
    ) -> dict[str, Any]:
        if self.run_root is None:
            raise BASE.Blocked("Unit QA requires a bound coordinator run root")
        if self.unit_broker is None or not callable(getattr(self.unit_broker, "run", None)):
            raise BASE.Blocked("Stage4 requires an independent controller unit broker")
        qa_root = Path(qa_dir).resolve(strict=True)
        run_root = Path(self.run_root).resolve(strict=True)
        if run_root not in qa_root.parents or qa_root == run_root:
            raise BASE.Blocked("Unit QA directory escapes the managed run root")
        report_path = run_root / unit_qa.UNIT_REPORT_NAME
        unit_error = None
        try:
            result = self.unit_broker.run(
                task_id=task_id,
                source_head=source_head,
                candidate_root=Path(candidate_dir),
                report_path=report_path,
            )
        except Exception as exc:
            result = {}
            unit_error = type(exc).__name__ + ": " + str(exc)[:800]
        report = result if isinstance(result, dict) else {}
        context_path = run_root / ("split-context.json" if self.qa_commands == unit_qa.SPLIT_QA_COMMANDS else unit_qa.UNIT_CONTEXT_NAME)
        evidence = {
            "report_path": str(report_path),
            "report_relative": f"../../{unit_qa.UNIT_REPORT_NAME}",
            "context_present": context_path.is_file(),
            "report_present": report_path.is_file(),
            "unit_error": unit_error,
            "ready": report.get("ready") is True,
            "receipt_sha256": report.get("receipt_sha256"),
            "suites": [
                {
                    "id": row.get("id"),
                    "status": (row.get("process") or {}).get("status"),
                    "exit_code": (row.get("process") or {}).get("exit_code"),
                    "test_count": row.get("test_count"),
                    "failure_count": row.get("failure_count"),
                    "error_count": row.get("error_count"),
                    "skip_count": row.get("skip_count"),
                }
                for row in report.get("suites", [])
                if isinstance(row, dict)
            ],
        }
        self._unit_evidence = evidence
        return evidence

    def worker_call(self, role, workspace, prompt, source_digest=None, test_commands=()):
        prepare = getattr(self.broker, "prepare_run", None)
        if callable(prepare):
            prepare(self.run_root, self.state)
        if role == "qa-tester":
            if self.broker is None:
                raise BASE.Blocked("Stage3 QA requires the explicit credential-free broker")
            if self.unit_broker is None:
                raise BASE.Blocked("Stage4 QA requires the controller unit broker")
            build = int(self.state.get("builds", 0))
            if build < 1:
                raise BASE.Blocked("Stage3 broker QA has no immutable build candidate")
            candidate_dir = Path(self.run_root) / "candidates" / f"build-{build}"
            unit_evidence = self.prepare_unit(
                task_id=self.state["task_id"],
                source_head=self.state["source_head"],
                candidate_dir=candidate_dir,
                qa_dir=workspace,
            )
            if self.qa_commands == unit_qa.SPLIT_QA_COMMANDS and unit_evidence.get("unit_error"):
                error = BASE.Blocked("Split controller QA could not produce valid process evidence; no model QA or code rework dispatched", failure_kind="execution")
                self.record_failure_kind(error)
                raise error
            evidence = self.prepare_broker(
                task_id=self.state["task_id"],
                source_head=self.state["source_head"],
                candidate_dir=candidate_dir,
                qa_dir=workspace,
                build_number=build,
            )
            supplied = tuple(test_commands)
            if supplied != self.qa_commands:
                raise BASE.Blocked("Stage4 QA verifier command contract changed")
            broker_prompt = (
                prompt
                + "\nSTAGE3 QA APPLICABILITY: the base browser Boolean remains false "
                  "because the protected coordinator has no native browser executor. "
                  "This does not cancel the actual broker suite.\n"
                + "Controller-owned unit evidence (relative): "
                + unit_evidence["report_relative"]
                + "\nRun the two mandatory read-only unit verifier commands exactly once, "
                  "then run the sealed browser verifier exactly once.\n"
                + "Unit report outside the QA snapshot (relative): "
                + unit_evidence["report_relative"]
                + "\nBroker report outside the QA snapshot (relative): "
                + evidence["report_relative"]
                + "\nRun the additional mandatory browser verifier command exactly once: "
                + qa.BROKER_VERIFY_COMMAND
                + "\nIf the verifier exits nonzero, report QA FAIL. Never convert failure, "
                  "timeout, source drift, or a missing report to N/A or PASS. "
                  "Include the two exact unit verifiers and browser verifier "
                  "command in the evidence list."
            )
            if self.qa_commands == unit_qa.SPLIT_QA_COMMANDS:
                broker_prompt = (
                    prompt
                    + "\nSPLIT QA v3: actual controller tests include automation, knowledge, LADS and all product browser/API/PWA checks. "
                    + "Run ONLY these four read-only commands exactly once each, in order: "
                    + json.dumps(list(self.qa_commands))
                    + "\nDo not run the legacy v2 script path or any extra command. A missing/failed verifier requires FAIL, never N/A. Include all exact commands in evidence."
                )
            return super().worker_call(
                role,
                workspace,
                broker_prompt,
                source_digest,
                supplied,
            )
        if role == "reviewer" and "Strict final phase:" in prompt:
            evidence = self._broker_evidence
            if evidence is None:
                raise BASE.Blocked("Final review is missing Stage3 broker evidence")
            unit_evidence = self._unit_evidence
            if unit_evidence is None:
                raise BASE.Blocked("Final review is missing controller unit evidence")
            report = evidence.get("report")
            bounded = {
                "report_present": evidence.get("report_present"),
                "report_relative": evidence.get("report_relative"),
                "broker_error": evidence.get("broker_error"),
                "receipt_sha256": report.get("receipt_sha256") if isinstance(report, dict) else None,
                "source_head": report.get("source_head") if isinstance(report, dict) else None,
                "source_inventory_sha256": (
                    report.get("source_inventory_sha256") if isinstance(report, dict) else None
                ),
                "ready": report.get("ready") if isinstance(report, dict) else False,
                "suites": (
                    [
                        {
                            "id": row.get("id"),
                            "status": (row.get("process") or {}).get("status"),
                            "exit_code": (row.get("process") or {}).get("exit_code"),
                            "timed_out": (row.get("process") or {}).get("timed_out"),
                        }
                        for row in report.get("suites", [])
                        if isinstance(row, dict)
                    ]
                    if isinstance(report, dict)
                    else []
                ),
                "checks": (
                    [
                        {
                            "id": row.get("id"),
                            "status": row.get("status"),
                            "evidence": str(row.get("evidence", ""))[:300],
                        }
                        for row in report.get("checks", [])
                        if isinstance(row, dict)
                    ]
                    if isinstance(report, dict)
                    else []
                ),
                "unit": {
                    "report_present": unit_evidence.get("report_present"),
                    "context_present": unit_evidence.get("context_present"),
                    "report_relative": unit_evidence.get("report_relative"),
                    "unit_error": unit_evidence.get("unit_error"),
                    "receipt_sha256": unit_evidence.get("receipt_sha256"),
                    "ready": unit_evidence.get("ready"),
                    "suites": unit_evidence.get("suites"),
                },
            }
            prompt += (
                "\nActual Stage4 unit and Stage3 browser evidence (not a Builder claim): "
                + json.dumps(bounded, ensure_ascii=True, sort_keys=True)
                + "\nA broker failure, timeout, source mismatch, missing required "
                  "check, or verifier failure requires concrete Reviewer "
                  "CHANGES_REQUESTED; N/A is not allowed."
            )
        return super().worker_call(
            role,
            workspace,
            prompt,
            source_digest,
            test_commands,
        )


class Stage3Runner:
    """Run the protected coordinator through the exact v2 QA contract."""

    def __init__(
        self,
        *,
        worker: Any,
        broker: Any,
        unit_broker: Any,
        timeout: int = 900,
        log: Any = None,
    ) -> None:
        if timeout < 30 or timeout > 3600:
            raise ValueError("Stage3Runner timeout must be between 30 and 3600 seconds")
        self.orchestrator = Stage3Orchestrator(
            worker=worker,
            broker=broker,
            unit_broker=unit_broker,
            timeout=timeout,
            log=log,
        )

    @staticmethod
    def expected_contract(manifest_commands: Any) -> tuple[str, ...]:
        if tuple(manifest_commands) != unit_qa.STAGE4_QA_COMMANDS:
            raise BASE.Blocked("Stage4Runner requires the exact v2 QA commands")
        return tuple(manifest_commands)

    @staticmethod
    def contract_manifest(manifest: Any) -> Any:
        """Materialize the fixed verifier before any coordinator phase starts."""
        if tuple(manifest.test_commands) != qa.QA_TEST_COMMANDS:
            raise BASE.Blocked("Stage3 requires the two immutable base QA commands")
        return replace(manifest, test_commands=unit_qa.STAGE4_QA_COMMANDS)

    def run(self, manifest: Any) -> dict[str, Any]:
        self.expected_contract(manifest.test_commands)
        return self.orchestrator.run(manifest)
