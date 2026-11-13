"""Deterministic Stage 3 QA receipts and read-only report verification."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

QA_TEST_COMMANDS = (
    "python -B -m unittest discover -s tests/automation -v",
    "python -B -m unittest discover -s tests/knowledge -v",
)
BROKER_VERIFY_COMMAND = (
    "python -B scripts/lads_stage3.py verify-broker "
    "../../qa-broker-current.json"
)
STAGE3_QA_COMMANDS = (*QA_TEST_COMMANDS, BROKER_VERIFY_COMMAND)
TRUSTED_SUITES = (
    {
        "id": "node-syntax",
        "path": "app.js",
        "command": "node --check app.js",
        "required": True,
        "pin": False,
    },
    {
        "id": "pwa-build",
        "path": "scripts/build-pwa.mjs",
        "command": "node scripts/build-pwa.mjs",
        "required": True,
    },
    {
        "id": "pwa-check",
        "path": "scripts/check-pwa.mjs",
        "command": "node scripts/check-pwa.mjs",
        "required": True,
    },
    {
        "id": "files",
        "path": "tests/acceptance/lads-stage3-files.py",
        "command": "python -B tests/acceptance/lads-stage3-files.py",
        "required": True,
    },
    {
        "id": "browser",
        "path": "tests/browser/lads-regression.cjs",
        "command": "node tests/browser/lads-regression.cjs",
        "required": True,
    },
    {
        "id": "notes-contract",
        "path": "tests/browser/notes-contract.py",
        "command": "python -B tests/browser/notes-contract.py",
        "required": False,
    },
    {
        "id": "notes-api-contract",
        "path": "tests/acceptance/notes-api-contract.py",
        "command": "python -B tests/acceptance/notes-api-contract.py",
        "required": True,
    },
)
TRUSTED_SUITE_SHA256 = {
    "files": frozenset(
        {"2b33cef26365a5d127c482a2596346ca02676660a29b6e1915a12801525bbd7b"}
    ),
    "browser": frozenset(
        {"cdcf05753831ccd695743032c9c417b980f4f42630cb890f2057bf4133d32590"}
    ),
    "notes-contract": frozenset(
        {"6faa05726eee5532c47c676e8aeba44de2f09dd06b879cd51d5432676fa2a153"}
    ),
    "notes-api-contract": frozenset(
        {"3e42719d01f2ed3308b604012f24283548a9ef2d40d3643bab24436960991d6b"}
    ),
    "node-syntax": frozenset(
        {"bf691f731f25cb3bc7ce448a413a84f835fd541ee85f9c19771b841c6372c412"}
    ),
    "pwa-build": frozenset(
        {
            "7a1c3fe85dba73010b5b96f4b4d6c6c54ffcb844f939c980ed40ea9cc393d02e",
            "9b09b0b3bad16b2bfb38a22affb8e1cbea28360b1d4e050f69118173b14b473e",
        }
    ),
    "pwa-check": frozenset(
        {
            "7425128380a552106f35fad7ebc82908e6a515def8021fa18607996613170d46",
            "079ba4cfbacb9be252552b4f8cc0e66fa416a706d60f343a8b0d95d118c1561d",
        }
    ),
}
REQUIRED_CHECKS = frozenset(
    {
        "route-navigation",
        "schedule-crud",
        "learning-plan-actual",
        "finance-save-summary",
        "ideas-save-edit-detail",
        "json-v11-export-preview-restore",
        "restore-dedupe-invalid-preserve",
        "context-storage-isolation",
        "layout-390x500",
        "layout-414x896",
        "layout-1366x900",
        "bottom-navigation",
        "browser-console",
        "offline-pwa",
        "notes-api-contract",
    }
)
OPTIONAL_CHECKS = frozenset({"notes-home-card-contract"})
STAGE3_DEMO_REQUIRED_CHECKS = REQUIRED_CHECKS | OPTIONAL_CHECKS
RUNTIME_LIMITATIONS = frozenset({"real-authentication", "real-ai-tools"})
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_COMMAND = re.compile(r"[\r\n;&|><`]|\$\(")


class BrokerVerificationError(ValueError):
    """The sealed broker report cannot support a real QA PASS."""


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & marker)


def source_inventory(root: str | Path) -> dict[str, str]:
    """Return the complete regular-file inventory, excluding only VCS metadata."""
    selected = Path(root).resolve(strict=True)
    if not selected.is_dir() or _is_reparse(selected):
        raise BrokerVerificationError("Candidate source root is unsafe")
    files: dict[str, str] = {}
    for current, directories, names in os.walk(selected, topdown=True, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(directories):
            path = current_path / name
            if current_path == selected and name == ".git":
                continue
            if _is_reparse(path):
                raise BrokerVerificationError("Candidate inventory contains a reparse directory")
            kept.append(name)
        directories[:] = kept
        for name in sorted(names):
            path = current_path / name
            if current_path == selected and name == ".git":
                continue
            if _is_reparse(path) or not path.is_file():
                raise BrokerVerificationError("Candidate inventory contains a non-regular file")
            relative = path.relative_to(selected).as_posix()
            files[relative] = _sha_file(path)
    if not files:
        raise BrokerVerificationError("Candidate inventory is empty")
    folded = [name.casefold() for name in files]
    if len(folded) != len(set(folded)):
        raise BrokerVerificationError("Candidate inventory contains case-colliding paths")
    return dict(sorted(files.items()))


def inventory_digest(head: str, files: Mapping[str, str]) -> str:
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40,64}", head):
        raise BrokerVerificationError("Source HEAD is malformed")
    checked: dict[str, str] = {}
    for path, digest in files.items():
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or "\\" in path
            or ".." in path.split("/")
            or not isinstance(digest, str)
            or not _SHA_RE.fullmatch(digest)
        ):
            raise BrokerVerificationError("Candidate inventory entry is malformed")
        checked[path] = digest
    return sha256_bytes(canonical({"head": head, "files": checked}))


def trusted_suite_inventory(root: str | Path) -> list[dict[str, Any]]:
    selected = Path(root).resolve(strict=True)
    result: list[dict[str, Any]] = []
    for suite in TRUSTED_SUITES:
        path = selected / suite["path"]
        if not path.is_file() or _is_reparse(path):
            raise BrokerVerificationError(f"Trusted suite source is missing: {suite['id']}")
        digest = _sha_file(path)
        if (
            suite.get("pin", True)
            and digest not in TRUSTED_SUITE_SHA256[suite["id"]]
        ):
            raise BrokerVerificationError(f"Trusted suite source drifted: {suite['id']}")
        result.append({**suite, "sha256": digest})
    return result


def report_receipt(body: Mapping[str, Any]) -> str:
    clean = {key: value for key, value in body.items() if key != "receipt_sha256"}
    return sha256_bytes(canonical(clean))


def seal_broker_report(body: Mapping[str, Any]) -> dict[str, Any]:
    report = dict(body)
    report.pop("receipt_sha256", None)
    report["receipt_sha256"] = report_receipt(report)
    return report


def _process_ok(suite: Mapping[str, Any]) -> None:
    command = suite.get("command")
    if not isinstance(command, str) or not command or _FORBIDDEN_COMMAND.search(command):
        raise BrokerVerificationError("Broker suite command is unsafe")
    if command not in {item["command"] for item in TRUSTED_SUITES}:
        raise BrokerVerificationError("Broker suite command is not trusted")
    process = suite.get("process")
    if not isinstance(process, dict):
        raise BrokerVerificationError("Broker process receipt is missing")
    if process.get("status") != "completed":
        raise BrokerVerificationError("Broker process did not complete")
    if type(process.get("exit_code")) is not int or process["exit_code"] != 0:
        raise BrokerVerificationError("Broker process exit code is not integer zero")
    if process.get("timed_out") is not False:
        raise BrokerVerificationError("Timed-out broker process cannot pass")
    if process.get("output_overflow") is True:
        raise BrokerVerificationError("Broker output overflow makes the receipt incomplete")


def validate_broker_report(
    report: Mapping[str, Any],
    *,
    candidate_root: str | Path,
    task_id: str,
    source_head: str,
    expected_inventory_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate a sealed report against the immutable QA source inventory."""
    if not isinstance(report, dict):
        raise BrokerVerificationError("Broker report must be an object")
    if report.get("schema") != "lads-stage3-broker-v1":
        raise BrokerVerificationError("Broker report schema is unsupported")
    if report.get("simulation") is not False:
        raise BrokerVerificationError("Simulation cannot satisfy actual QA")
    if report.get("task_id") != task_id or report.get("source_head") != source_head:
        raise BrokerVerificationError("Broker task/source HEAD binding mismatch")
    if report.get("receipt_sha256") != report_receipt(report):
        raise BrokerVerificationError("Broker report receipt does not match its bytes")
    declared = report.get("required_checks")
    if (
        not isinstance(declared, list)
        or len(declared) != len(set(declared))
        or any(not isinstance(item, str) for item in declared)
    ):
        raise BrokerVerificationError("Broker required-check declaration is malformed")
    required = set(declared)
    if not REQUIRED_CHECKS <= required or not required <= REQUIRED_CHECKS | OPTIONAL_CHECKS:
        raise BrokerVerificationError("Broker required-check declaration changed the base contract")

    actual_files = source_inventory(candidate_root)
    actual_digest = inventory_digest(source_head, actual_files)
    given_digest = report.get("source_inventory_sha256")
    if given_digest != actual_digest:
        raise BrokerVerificationError("Broker source inventory digest does not match QA snapshot")
    if expected_inventory_sha256 is not None and given_digest != expected_inventory_sha256:
        raise BrokerVerificationError("Broker source inventory does not match the pinned candidate")
    if report.get("source_files") != actual_files:
        raise BrokerVerificationError("Broker source file receipts do not match QA snapshot")

    expected_suites = {
        item["id"]: item for item in trusted_suite_inventory(candidate_root)
    }
    if "notes-home-card-contract" not in required:
        expected_suites.pop("notes-contract", None)
    suites = report.get("suites")
    if not isinstance(suites, list):
        raise BrokerVerificationError("Broker suite receipts are missing")
    seen: set[str] = set()
    for suite in suites:
        if not isinstance(suite, dict):
            raise BrokerVerificationError("Broker suite receipt is malformed")
        identity = suite.get("id")
        if identity not in expected_suites or identity in seen:
            raise BrokerVerificationError("Broker suite is unknown or duplicated")
        expected = expected_suites[identity]
        if (
            suite.get("path") != expected["path"]
            or suite.get("sha256") != expected["sha256"]
            or suite.get("command") != expected["command"]
        ):
            raise BrokerVerificationError("Broker suite source digest or command changed")
        _process_ok(suite)
        seen.add(identity)
    if seen != set(expected_suites):
        raise BrokerVerificationError("Broker did not execute every required trusted suite")

    checks = report.get("checks")
    if not isinstance(checks, list):
        raise BrokerVerificationError("Broker check receipts are missing")
    by_id: dict[str, dict[str, Any]] = {}
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("id"), str):
            raise BrokerVerificationError("Broker check receipt is malformed")
        if check["id"] in by_id:
            raise BrokerVerificationError("Broker check ID is duplicated")
        if check.get("status") not in {"PASS", "FAIL", "BLOCKED", "NOT_IMPLEMENTED", "TIMEOUT", "ERROR"}:
            raise BrokerVerificationError("Broker check status is unsupported")
        if not check.get("evidence"):
            raise BrokerVerificationError("Broker check lacks concrete evidence")
        by_id[check["id"]] = check
    missing = required - set(by_id)
    if missing:
        raise BrokerVerificationError("Broker omitted required checks: " + ", ".join(sorted(missing)))
    failed = sorted(
        identity
        for identity in required
        if by_id[identity].get("status") != "PASS"
    )
    if failed:
        raise BrokerVerificationError("Broker required checks failed: " + ", ".join(failed))
    for identity in RUNTIME_LIMITATIONS:
        if identity not in by_id or by_id[identity].get("status") != "NOT_IMPLEMENTED":
            raise BrokerVerificationError(
                f"Product limitation must be explicit as NOT_IMPLEMENTED: {identity}"
            )
    if report.get("ready") is not True:
        raise BrokerVerificationError("Broker report is not marked ready")
    return {
        "status": "PASS",
        "task_id": task_id,
        "source_head": source_head,
        "source_inventory_sha256": actual_digest,
        "report_receipt_sha256": report["receipt_sha256"],
        "suite_count": len(suites),
        "check_count": len(checks),
    }


def load_and_verify_broker_report(
    path: str | Path,
    *,
    candidate_root: str | Path,
    task_id: str,
    source_head: str,
    expected_inventory_sha256: str | None = None,
    maximum_bytes: int = 2 * 1024 * 1024,
) -> dict[str, Any]:
    selected = Path(path)
    if not selected.is_absolute():
        selected = Path(candidate_root).resolve(strict=True) / selected
    selected = selected.resolve(strict=True)
    if _is_reparse(selected) or not selected.is_file():
        raise BrokerVerificationError("Broker report is not a regular contained file")
    if selected.stat().st_size > maximum_bytes:
        raise BrokerVerificationError("Broker report exceeds its bounded size")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise BrokerVerificationError("Broker report contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        report = json.loads(
            selected.read_bytes().decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                BrokerVerificationError("Broker report contains non-JSON numbers")
            ),
        )
    except (UnicodeError, ValueError) as exc:
        raise BrokerVerificationError("Broker report is not strict UTF-8 JSON") from exc
    return validate_broker_report(
        report,
        candidate_root=candidate_root,
        task_id=task_id,
        source_head=source_head,
        expected_inventory_sha256=expected_inventory_sha256,
    )


def qa_status_from_verification(verification: Mapping[str, Any] | None) -> str:
    return "PASS" if isinstance(verification, Mapping) and verification.get("status") == "PASS" else "FAIL"


def _json_receipt_from_stdout(text: Any) -> dict[str, Any]:
    if not isinstance(text, str) or len(text) > 1024 * 1024:
        return {}
    candidates = [text.strip()]
    candidates.extend(reversed([line.strip() for line in text.splitlines() if line.strip()]))
    for candidate in candidates:
        if not candidate or not candidate.startswith("{"):
            continue
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, UnicodeError):
            continue
        if isinstance(value, dict):
            return value
    return {}


class QABroker:
    """Credential-free broker whose executor is supplied by the controller."""

    def __init__(
        self,
        *,
        executor: Any,
        work_root: str | Path,
        timeout: float = 50.0,
        required_checks: Any = REQUIRED_CHECKS,
    ) -> None:
        if not callable(getattr(executor, "run", None)):
            raise BrokerVerificationError("Broker requires the verified sandbox executor")
        if not 1 <= timeout <= 300:
            raise BrokerVerificationError("Broker timeout is outside its supported bound")
        self.executor = executor
        self.work_root = Path(work_root).resolve(strict=True)
        if self.work_root == self.work_root.parent:
            raise BrokerVerificationError("Broker work root cannot be a filesystem root")
        self.timeout = timeout
        self.required_checks = frozenset(required_checks)
        if (
            not REQUIRED_CHECKS <= self.required_checks
            or not self.required_checks <= REQUIRED_CHECKS | OPTIONAL_CHECKS
        ):
            raise BrokerVerificationError("Broker required checks weaken the base contract")

    def _copy(self, source: Path, destination: Path) -> dict[str, str]:
        source = source.resolve(strict=True)
        before = source_inventory(source)
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git"))
        after = source_inventory(destination)
        if before != after:
            raise BrokerVerificationError("Broker isolated copy changed candidate bytes")
        return after

    def run(
        self,
        *,
        task_id: str,
        source_head: str,
        candidate_root: str | Path,
        build_number: int,
        report_path: str | Path,
    ) -> dict[str, Any]:
        if not re.fullmatch(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            task_id,
        ):
            raise BrokerVerificationError("Broker task ID is malformed")
        if type(build_number) is not int or not 1 <= build_number <= 4:
            raise BrokerVerificationError("Broker build number is outside the four-build limit")
        candidate = Path(candidate_root).resolve(strict=True)
        files = source_inventory(candidate)
        digest = inventory_digest(source_head, files)
        root = Path(
            tempfile.mkdtemp(
                prefix="qb-",
                dir=self.work_root,
            )
        )
        if root.is_symlink() or not root.is_dir():
            raise BrokerVerificationError("Broker private root is unsafe")
        source_copy = root / "source"
        build_copy = root / "build"
        (root / "tmp").mkdir()
        private_local_app_data = root / "localappdata"
        private_local_app_data.mkdir()
        copied = self._copy(candidate, source_copy)
        copied_digest = inventory_digest(source_head, copied)
        if copied != files or copied_digest != digest:
            raise BrokerVerificationError("Broker source copy digest mismatch")
        built = self._copy(source_copy, build_copy)
        built_digest = inventory_digest(source_head, built)
        if built != copied or built_digest != digest:
            raise BrokerVerificationError("Broker build copy digest mismatch")

        class QuietHandler(SimpleHTTPRequestHandler):
            # Windows registry MIME associations can label .js as text/plain.
            # Service Worker loading requires an actual JavaScript MIME type.
            extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                              '.js': 'text/javascript', '.mjs': 'text/javascript',
                              '.css': 'text/css', '.wasm': 'application/wasm',
                              '.json': 'application/json',
                              '.webmanifest': 'application/manifest+json'}
            def log_message(self, _format: str, *_args: Any) -> None:
                return

        handler = partial(QuietHandler, directory=str(build_copy))
        local_server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        local_thread = threading.Thread(
            target=local_server.serve_forever,
            name="lads-stage3-broker-http",
            daemon=True,
        )
        local_thread.start()
        local_base = f"http://127.0.0.1:{local_server.server_address[1]}"
        artifact_root = root / "artifacts"
        artifact_root.mkdir()

        suites: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []
        cleanup_error = None
        trusted_baseline = {
            item["id"]: item["sha256"]
            for item in trusted_suite_inventory(build_copy)
        }
        suite_tampered = False
        try:
            for suite in trusted_suite_inventory(build_copy):
                if (
                    suite["id"] == "notes-contract"
                    and "notes-home-card-contract" not in self.required_checks
                ):
                    continue
                if suite_tampered:
                    suites.append(
                        {
                            "id": suite["id"],
                            "path": suite["path"],
                            "sha256": suite["sha256"],
                            "command": suite["command"],
                            "process": {
                                "status": "error",
                                "exit_code": None,
                                "timed_out": False,
                                "output_overflow": False,
                                "output_sha256": None,
                                "error_type": "BrokerVerificationError",
                                "error": "A prior suite changed trusted harness bytes.",
                            },
                        }
                    )
                    continue
                try:
                    before_suite = {
                        item["id"]: item["sha256"]
                        for item in trusted_suite_inventory(build_copy)
                    }
                    if before_suite != trusted_baseline:
                        raise BrokerVerificationError(
                            "A prior suite changed trusted harness bytes."
                        )
                    receipt = self.executor.run(
                        command=suite["command"],
                        cwd=str(build_copy),
                        timeout=self.timeout,
                        network_access=False,
                        writable_roots=[
                            str(build_copy),
                            str(root / "tmp"),
                            str(artifact_root),
                            str(private_local_app_data),
                        ],
                        environment={
                            "LIFEOS_BASE_URL": local_base,
                            "LADS_ARTIFACT_DIR": str(artifact_root),
                            "LOCALAPPDATA": str(private_local_app_data),
                            "TEMP": str(root / "tmp"),
                            "TMP": str(root / "tmp"),
                        },
                    )
                    if not isinstance(receipt, dict):
                        raise BrokerVerificationError("Executor returned a non-object receipt")
                    stdout = receipt.get("stdout", "")
                    emitted = _json_receipt_from_stdout(stdout)
                    exit_code = receipt.get("exit_code")
                    timed_out = receipt.get("timed_out") is True
                    if timed_out:
                        process_status = "timeout"
                    elif type(exit_code) is int:
                        process_status = "completed" if exit_code == 0 else "failed"
                    else:
                        process_status = str(receipt.get("status", "error"))
                    process = {
                        "status": process_status,
                        "exit_code": exit_code if type(exit_code) is int else None,
                        "timed_out": timed_out,
                        "output_overflow": receipt.get("output_overflow") is True,
                        "output_sha256": (
                            receipt.get("output_sha256")
                            if isinstance(receipt.get("output_sha256"), str)
                            else sha256_bytes(str(stdout).encode("utf-8"))
                        ),
                    }
                    emitted_checks = receipt.get("checks", emitted.get("checks", []))
                    if not isinstance(emitted_checks, list):
                        emitted_checks = []
                    for check in emitted_checks:
                        if isinstance(check, dict) and isinstance(check.get("id"), str):
                            checks.append(
                                {
                                    "id": check["id"][:100],
                                    "status": str(check.get("status", "FAIL"))[:30],
                                    "evidence": str(check.get("evidence", "suite emitted no evidence"))[:1000],
                                }
                            )
                    after_suite = {
                        item["id"]: item["sha256"]
                        for item in trusted_suite_inventory(build_copy)
                    }
                    if after_suite != trusted_baseline:
                        suite_tampered = True
                        raise BrokerVerificationError(
                            "Trusted harness bytes changed during suite execution."
                        )
                except TimeoutError:
                    process = {
                        "status": "timeout",
                        "exit_code": None,
                        "timed_out": True,
                        "output_overflow": False,
                        "output_sha256": None,
                        "error_type": "TimeoutError",
                        "error": "Broker suite timed out.",
                    }
                except Exception as exc:
                    if isinstance(exc, BrokerVerificationError) and "trusted" in str(exc).casefold():
                        suite_tampered = True
                    process = {
                        "status": "error",
                        "exit_code": None,
                        "timed_out": False,
                        "output_overflow": False,
                        "output_sha256": None,
                        "error_type": type(exc).__name__,
                        "error": str(exc).replace("\r", " ").replace("\n", " ")[:500],
                    }
                suites.append(
                    {
                        "id": suite["id"],
                        "path": suite["path"],
                        "sha256": suite["sha256"],
                        "command": suite["command"],
                        "process": process,
                    }
                )
        finally:
            terminate_all = getattr(self.executor, "terminate_all", None)
            if callable(terminate_all):
                try:
                    terminate_all()
                except Exception as exc:
                    cleanup_error = type(exc).__name__ + ": " + str(exc)[:500]
            local_server.shutdown()
            local_server.server_close()
            local_thread.join(timeout=10)
        if cleanup_error and suites:
            suites[-1]["process"] = {
                **suites[-1]["process"], "status": "error",
                "cleanup_error": cleanup_error,
            }

        normalized: list[dict[str, Any]] = []
        duplicate_or_bad = False
        seen: set[str] = set()
        for check in checks:
            if check["id"] in seen:
                duplicate_or_bad = True
                continue
            seen.add(check["id"])
            normalized.append(check)
        for identity in sorted(REQUIRED_CHECKS - seen):
            normalized.append(
                {
                    "id": identity,
                    "status": "FAIL",
                    "evidence": "Required check was not emitted by a trusted suite.",
                }
            )
        normalized.append(
            {
                "id": "real-authentication",
                "status": "NOT_IMPLEMENTED",
                "evidence": "Product authentication is absent; no multiuser claim is made.",
            }
        )
        normalized.append(
            {
                "id": "real-ai-tools",
                "status": "NOT_IMPLEMENTED",
                "evidence": "Product AI/tool runtime is absent; this infrastructure test does not invoke it.",
            }
        )
        if duplicate_or_bad:
            normalized.append(
                {
                    "id": "duplicate-check-receipt",
                    "status": "FAIL",
                    "evidence": "A suite emitted duplicate check identities.",
                }
            )
        if suite_tampered:
            normalized.append(
                {
                    "id": "trusted-suite-tamper",
                    "status": "FAIL",
                    "evidence": "A suite changed pinned harness bytes; later suites were not executed as trusted code.",
                }
            )
        suites_ok = all(
            item["process"]["status"] == "completed"
            and type(item["process"]["exit_code"]) is int
            and item["process"]["exit_code"] == 0
            and item["process"]["timed_out"] is False
            and item["process"]["output_overflow"] is False
            for item in suites
            if next(
                suite["required"]
                for suite in TRUSTED_SUITES
                if suite["id"] == item["id"]
            )
        )
        checks_ok = all(
            item["status"] == "PASS"
            for item in normalized
            if item["id"] in self.required_checks
        )
        ready = suites_ok and checks_ok and not duplicate_or_bad
        body = {
            "schema": "lads-stage3-broker-v1",
            "task_id": task_id,
            "source_head": source_head,
            "candidate_sha256": digest,
            "source_inventory_sha256": digest,
            "source_files": copied,
            "build_number": build_number,
            "created_at": datetime.now(UTC).isoformat(),
            "simulation": False,
            "private_root": str(root),
            "suites": suites,
            "checks": normalized,
            "required_checks": sorted(self.required_checks),
            "ready": ready,
        }
        report = seal_broker_report(body)
        destination = _broker_report_destination(
            report_path,
            candidate=candidate,
            source_copy=source_copy,
            build_copy=build_copy,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        context = {
            "schema": "lads-stage3-qa-context-v1",
            "task_id": task_id,
            "source_head": source_head,
            "source_inventory_sha256": digest,
            "report_sha256": sha256_bytes(destination.read_bytes()),
        }
        (destination.parent / "qa-context.json").write_text(
            json.dumps(context, ensure_ascii=True, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return report


def _broker_report_destination(
    report_path: str | Path,
    *,
    candidate: Path,
    source_copy: Path,
    build_copy: Path,
) -> Path:
    destination = Path(report_path).resolve(strict=False)
    protected = (candidate, source_copy, build_copy)
    if any(destination == root or root in destination.parents for root in protected):
        raise BrokerVerificationError("Broker report must remain outside the QA source snapshot")
    return destination
