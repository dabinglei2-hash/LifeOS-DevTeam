#!/usr/bin/env python3
"""Opt-in Stage 3 controller/verifier commands.

The base coordinator remains protected. This entrypoint adds explicit broker
and install-plan controls; it never dispatches a model by itself and never
installs candidates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lads_automation.install import load_install_plan, summarize_skills_listing
from lads_automation.engine import EVENT_DOCUMENTS
from lads_automation.delivery import (
    accept_handoff,
    delivery_fingerprint,
    load_handoff_bundle,
    register_delivery_engine,
    retry_handoff,
    standard_event_plan,
    write_main_ack,
)
from lads_automation import qa, unit_qa
from lads_automation.qa import (
    BROKER_VERIFY_COMMAND,
    QA_TEST_COMMANDS,
    TRUSTED_SUITES,
    BrokerVerificationError,
    QABroker,
    load_and_verify_broker_report,
)
from lads_automation.rpc import CodexJsonRpcExecutor
from lads_automation.state import acceptance_root, default_automation_root
from lads_automation.team import (
    BASE,
    Stage3Orchestrator,
    Stage3Runner,
    collect_process_failure,
    prepare_python_runtime,
)
from lads_automation.unit_qa import UnitQABroker

_WORKER_KEYS = (
    "DEEPSEEK_SCANNER_API_KEY",
    "DEEPSEEK_REVIEWER_API_KEY",
    "DEEPSEEK_BUILDER_API_KEY",
    "DEEPSEEK_QA_API_KEY",
)


def _emit(value: dict[str, Any]) -> None:
    text = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(text.encode("utf-8")) > 24000:
        text = json.dumps(
            {
                "status": value.get("status", "FAIL"),
                "truncated": True,
                "result_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    print(text)


def _strict_json(path: Path, maximum: int = 2 * 1024 * 1024) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("JSON input is missing or exceeds its bound")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("JSON input contains duplicate keys")
            result[key] = value
        return result

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError("JSON input root must be an object")
    return value


def _verify_broker(args: argparse.Namespace) -> int:
    report_value = args.report or args.report_path
    if not report_value:
        raise ValueError("A broker report path is required")
    report_path = Path(report_value)
    candidate = Path(args.candidate_root).resolve(strict=True)
    if not report_path.is_absolute():
        report_path = candidate / report_path
    report_path = report_path.resolve(strict=True)
    context_path = report_path.parent / "qa-context.json"
    context = _strict_json(context_path, 32 * 1024)
    if (
        context.get("schema") != "lads-stage3-qa-context-v1"
        or context.get("report_sha256") is None
    ):
        raise BrokerVerificationError("QA context is malformed")
    if context["report_sha256"] != hashlib.sha256(report_path.read_bytes()).hexdigest():
        raise BrokerVerificationError("Broker report does not match the QA context receipt")
    verified = load_and_verify_broker_report(
        report_path,
        candidate_root=candidate,
        task_id=context.get("task_id"),
        source_head=context.get("source_head"),
        expected_inventory_sha256=context.get("source_inventory_sha256"),
    )
    _emit({"status": "PASS", "verification": verified})
    return 0


def _verify_unit(args: argparse.Namespace) -> int:
    verified = unit_qa.verify_unit_for_run(
        args.suite,
        args.report,
        candidate_root=args.candidate_root,
    )
    _emit({"status": "PASS", "verification": verified})
    return 0


def _install_plan(args: argparse.Namespace) -> int:
    plan = load_install_plan(args.map, root=ROOT, expected_head=args.expected_head)
    _emit(
        {
            "status": "VALIDATED",
            "source_head": plan.source_head,
            "source_snapshot_sha256": plan.source_snapshot_sha256,
            "map_sha256": plan.map_sha256,
            "target_allowlist": list(plan.target_allowlist),
            "targets": [target.__dict__ for target in plan.targets],
            "installation": "CONTROLLER_ONLY_AFTER_FINAL_REVIEW",
        }
    )
    return 0


def _failure(args: argparse.Namespace) -> int:
    value = _strict_json(Path(args.receipt), 2 * 1024 * 1024)

    class Result:
        stdout = str(value.get("stdout", ""))
        stderr = str(value.get("stderr", ""))
        exit_code = value.get("exit_code")
        timed_out = value.get("timed_out") is True
        bounded_output = value.get("bounded_output") is True

    _emit({"status": "OK", "metadata": collect_process_failure(Result())})
    return 0


def _skills_summary(args: argparse.Namespace) -> int:
    response = _strict_json(Path(args.receipt), 2 * 1024 * 1024)
    _emit(
        {
            "status": "OK",
            "skills": summarize_skills_listing(response),
            "source": "appserver skills/list forceReload receipt",
        }
    )
    return 0


def _load_worker_environment(source: str) -> None:
    """Apply the selected credential source without exposing secret values."""
    if source == "env":
        missing = [name for name in _WORKER_KEYS if not os.environ.get(name)]
        if missing:
            raise RuntimeError(
                "Dedicated worker environment variables are missing: "
                + ", ".join(missing)
            )
        return
    if source != "windows-user":
        raise ValueError("Unsupported credential source")
    if os.name != "nt":
        raise RuntimeError("windows-user credential source is available only on Windows")
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            for name in _WORKER_KEYS:
                try:
                    value, kind = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                if (
                    kind in {winreg.REG_SZ, winreg.REG_EXPAND_SZ}
                    and isinstance(value, str)
                    and value
                ):
                    os.environ[name] = value
    except OSError as exc:
        raise RuntimeError(
            "Windows user environment could not be inspected: " + type(exc).__name__
        ) from exc
    remaining = [name for name in _WORKER_KEYS if not os.environ.get(name)]
    if remaining:
        raise RuntimeError(
            "Dedicated worker environment variables are missing: " + ", ".join(remaining)
        )


def _selected_work_root(args: argparse.Namespace) -> Path:
    if bool(args.work_root) == bool(args.work_namespace):
        raise ValueError("Provide exactly one of --work-root or --work-namespace")
    if args.work_namespace:
        return acceptance_root(args.work_namespace)
    selected = Path(args.work_root)
    if not selected.is_absolute():
        raise ValueError("--work-root must be absolute")
    private = (default_automation_root().parent / "acceptance").resolve(strict=False)
    resolved = selected.resolve(strict=False)
    if resolved == private or private not in resolved.parents:
        raise ValueError("--work-root must be an owned acceptance child")
    return resolved


def _run(args: argparse.Namespace) -> int:
    _load_worker_environment(args.credential_source)
    manifest = BASE.load_manifest(Path(args.task_file))
    if tuple(manifest.test_commands) != QA_TEST_COMMANDS:
        raise ValueError("RUN requires the two immutable Stage 3 QA commands")
    manifest = Stage3Runner.contract_manifest(manifest)
    if tuple(manifest.test_commands) != unit_qa.STAGE4_QA_COMMANDS:
        raise RuntimeError("Stage 4 contract materialization failed")
    work_root = _selected_work_root(args)
    work_root.mkdir(parents=True, exist_ok=True)
    if work_root.is_symlink():
        raise ValueError("Acceptance work root cannot be a symlink")
    python_runtime = prepare_python_runtime()
    executor = CodexJsonRpcExecutor(
        work_root=work_root,
        allowed_commands=(
            *qa.QA_TEST_COMMANDS,
            *(suite["command"] for suite in TRUSTED_SUITES),
        ),
        maximum_timeout=min(float(args.timeout), 300.0),
        runtime_executables={"python": python_runtime},
    )
    broker = QABroker(
        executor=executor,
        work_root=work_root,
        timeout=min(float(args.timeout), 300.0),
    )
    unit_broker = UnitQABroker(
        executor=executor,
        work_root=work_root,
        timeout=min(float(args.timeout), 300.0),
    )
    runner = Stage3Runner(
        worker=BASE.RealWorker(),
        broker=broker,
        unit_broker=unit_broker,
        timeout=args.timeout,
        log=(lambda _message: None) if args.quiet else (lambda message: print(message, file=sys.stderr)),
    )
    try:
        handoff = runner.run(manifest)
    finally:
        executor.terminate_all()
    _emit(
        {
            "status": handoff.get("task_status"),
            "task_id": handoff.get("task_id"),
            "task_status": handoff.get("task_status"),
            "source_head": handoff.get("source_head"),
            "source_snapshot_sha256": handoff.get("source_snapshot_sha256"),
            "candidate_sha256": handoff.get("candidate_sha256"),
            "run_root": str(runner.orchestrator.run_root),
            "main_ack_required": True,
            "automatic_integration": False,
        }
    )
    return 0 if handoff.get("task_status") == "AWAITING_MAIN_ACCEPTANCE" else 2


def _delivery_bundle(args: argparse.Namespace):
    return load_handoff_bundle(
        run_root=Path(args.run_root),
        repo_root=Path(args.repo),
        require_main_ack=bool(getattr(args, "require_main_ack", False)),
    )


def _main_ack(args: argparse.Namespace) -> int:
    bundle = _delivery_bundle(args)
    acknowledgement = write_main_ack(bundle)
    _emit(
        {
            "status": "ACKNOWLEDGED",
            "acknowledgement": acknowledgement,
            "fingerprint": delivery_fingerprint(bundle),
        }
    )
    return 0


def _knowledge_binding(args: argparse.Namespace):
    from lads_knowledge.database import FileStore
    from lads_automation.engine import KnowledgePublisher

    home = Path(args.knowledge_home).resolve(strict=True)
    store = FileStore.open(home, args.knowledge_db)
    bundle_repo = Path(args.repo).resolve(strict=True)
    return store, KnowledgePublisher(store, args.project_id, bundle_repo)


def _accept(args: argparse.Namespace) -> int:
    bundle = _delivery_bundle(args)
    store, knowledge = _knowledge_binding(args)
    try:
        engine = register_delivery_engine(bundle, knowledge=knowledge)
        selection = getattr(args, "event", None)
        options = {"event_types": tuple(selection)} if selection else {}
        event_plan = standard_event_plan(bundle, reason=args.reason, **options)
        result = accept_handoff(
            bundle,
            engine,
            reason=args.reason,
            event_plan=event_plan,
        )
        _emit(
            {
                "status": result.get("status"),
                "ready": result.get("ready"),
                "task_id": bundle.task_id,
                "candidate_sha256": bundle.candidate_sha256,
                "main_ack": "VALID",
                "integration_or_deploy": "NOT_AUTHORIZED_BY_ACCEPT",
            }
        )
        return 0 if result.get("status") == "DELIVERY_READY" and result.get("ready") is True else 1
    finally:
        store.close()


def _retry(args: argparse.Namespace) -> int:
    bundle = _delivery_bundle(args)
    store, knowledge = _knowledge_binding(args)
    try:
        engine = register_delivery_engine(bundle, knowledge=knowledge)
        result = retry_handoff(engine, reason=args.reason)
        _emit(
            {
                "status": result.get("status"),
                "ready": result.get("ready"),
                "task_id": bundle.task_id,
                "retry_reason": args.reason,
            }
        )
        return 0 if result.get("status") == "DELIVERY_READY" and result.get("ready") is True else 1
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -B scripts/lads_stage3.py",
        description=(
            "Opt-in Stage 3 adapter. Commands here do not install files, start "
            "model children, or turn N/A into PASS."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "requirements",
        help="print the Stage 3 broker/verifier contract",
    )

    run = subparsers.add_parser(
        "RUN",
        help="execute the independent four-role Stage 3 workflow and stop at MAIN-ACK",
    )
    run.add_argument("--task-file", required=True)
    root_choice = run.add_mutually_exclusive_group(required=True)
    root_choice.add_argument("--work-root")
    root_choice.add_argument("--work-namespace")
    run.add_argument("--timeout", type=int, default=900)
    run.add_argument(
        "--credential-source",
        choices=("env", "windows-user"),
        default="env",
        help="read the four dedicated worker keys from the current process or Windows User environment",
    )
    run.add_argument("--quiet", action="store_true")

    main_ack = subparsers.add_parser(
        "MAIN-ACK",
        help="explicitly acknowledge a verified handoff without integrating it",
    )
    main_ack.add_argument("--run-root", required=True)
    main_ack.add_argument("--repo", required=True)

    accept = subparsers.add_parser(
        "ACCEPT",
        help="after MAIN-ACK, publish evidence-bound documentation and index",
    )
    accept.add_argument("--run-root", required=True)
    accept.add_argument("--repo", required=True)
    accept.add_argument("--knowledge-home", required=True)
    accept.add_argument("--knowledge-db", default="knowledge.sqlite3")
    accept.add_argument("--project-id", required=True)
    accept.add_argument("--reason", default="Stage3 MAIN-ACK delivery")
    accept.add_argument("--event", action="append", choices=tuple(EVENT_DOCUMENTS),
                        help="Explicit events supported by this change; default is task status and delivery only")
    accept.set_defaults(require_main_ack=True)

    retry = subparsers.add_parser(
        "RETRY",
        help="retry only a persisted DOCS or INDEX delivery failure",
    )
    retry.add_argument("--run-root", required=True)
    retry.add_argument("--repo", required=True)
    retry.add_argument("--knowledge-home", required=True)
    retry.add_argument("--knowledge-db", default="knowledge.sqlite3")
    retry.add_argument("--project-id", required=True)
    retry.add_argument("--reason", required=True)
    retry.set_defaults(require_main_ack=True)

    verify = subparsers.add_parser(
        "verify-broker",
        help="read-only mandatory QA verifier for the sealed broker report",
    )
    verify.add_argument("report", nargs="?")
    verify.add_argument("--report", dest="report_path")
    verify.add_argument("--candidate-root", default=".")

    unit_verify = subparsers.add_parser(
        "verify-unit",
        help="read-only mandatory v2 verifier for controller unit receipts",
    )
    unit_verify.add_argument("suite", choices=tuple(item[0] for item in unit_qa.UNIT_SUITES))
    unit_verify.add_argument("report")
    unit_verify.add_argument("--candidate-root", default=".")

    plan = subparsers.add_parser(
        "install-plan",
        help="validate the exact reviewed twelve-target install plan without installing",
    )
    plan.add_argument(
        "--map",
        default="engineering/lads-stage3-install/install-map.json",
    )
    plan.add_argument("--expected-head")

    failure = subparsers.add_parser(
        "inspect-failure",
        help="extract bounded redacted metadata before legacy tail truncation",
    )
    failure.add_argument("--receipt", required=True)

    skills = subparsers.add_parser(
        "skills-summary",
        help="reduce an appserver skills/list forceReload receipt to name/path/errors",
    )
    skills.add_argument("--receipt", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "requirements":
            _emit(
                {
                    "status": "OK",
                    "stage3": Stage3Orchestrator.stage3_requirements(),
                    "automatic_invocation": [],
                    "controller_commands": [
                        "RUN",
                        "MAIN-ACK",
                        "ACCEPT",
                        "RETRY",
                        "verify-unit (mandatory during Stage4 QA)",
                        "verify-broker (mandatory during QA)",
                        "install-plan (after final review)",
                    ],
                }
            )
            return 0
        if args.command == "verify-broker":
            return _verify_broker(args)
        if args.command == "verify-unit":
            return _verify_unit(args)
        if args.command == "RUN":
            return _run(args)
        if args.command == "MAIN-ACK":
            return _main_ack(args)
        if args.command == "ACCEPT":
            return _accept(args)
        if args.command == "RETRY":
            return _retry(args)
        if args.command == "install-plan":
            return _install_plan(args)
        if args.command == "inspect-failure":
            return _failure(args)
        if args.command == "skills-summary":
            return _skills_summary(args)
        raise ValueError("Unknown command")
    except Exception as exc:
        _emit(
            {
                "status": "FAIL",
                "error": type(exc).__name__,
                "message": str(exc).replace("\r", " ").replace("\n", " ")[:800],
                "instructions": {
                    "qa_contract_version": unit_qa.UNIT_CONTRACT_VERSION,
                    "input_test_commands": list(QA_TEST_COMMANDS),
                    "qa_test_commands": list(unit_qa.STAGE4_QA_COMMANDS),
                    "mandatory_verifier_command": BROKER_VERIFY_COMMAND,
                    "unit_verifier_commands": list(unit_qa.UNIT_VERIFY_COMMANDS),
                },
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
