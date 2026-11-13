#!/usr/bin/env python3
"""Opt-in Stage 3 live UI workflow; no live execution occurs on import."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lads_automation import qa
from lads_automation.delivery import (
    accept_handoff,
    load_handoff_bundle,
    register_delivery_engine,
    standard_event_plan,
    write_main_ack,
)
from lads_automation.engine import KnowledgePublisher
from lads_automation.rpc import CodexJsonRpcExecutor
from lads_automation.state import FileJournal, acceptance_root, default_automation_root
from lads_automation.team import BASE, Stage3Runner, prepare_python_runtime
from lads_knowledge.database import FileStore
from lads_knowledge.indexer import repository_ref

_WORKER_KEYS = (
    "DEEPSEEK_SCANNER_API_KEY",
    "DEEPSEEK_REVIEWER_API_KEY",
    "DEEPSEEK_BUILDER_API_KEY",
    "DEEPSEEK_QA_API_KEY",
)
_CARD = 'data-home-card="notes"'
_LABEL = "\u6253\u5f00\u7b14\u8bb0\u5de5\u4f5c\u53f0"
_BAD_LABEL = "notes"


def _load_worker_environment(source: str) -> None:
    if source == "env":
        absent = [name for name in _WORKER_KEYS if not os.environ.get(name)]
        if absent:
            raise RuntimeError(
                "Dedicated worker environment is incomplete: " + ", ".join(absent)
            )
        return
    if source != "windows-user" or os.name != "nt":
        raise RuntimeError("windows-user credential source is unavailable")
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
    absent = [name for name in _WORKER_KEYS if not os.environ.get(name)]
    if absent:
        raise RuntimeError("Dedicated worker environment is incomplete: " + ", ".join(absent))


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError("Synthetic Git fixture failed: " + result.stderr[:500])
    return result.stdout.strip()


def _copy_source_tree(destination: Path) -> None:
    paths = BASE.source_paths(ROOT)
    for relative in paths:
        source = ROOT / Path(relative)
        if not source.is_file() or BASE.is_reparse(source):
            continue
        target = destination / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _set_notes_aria(index: Path, value: str) -> None:
    text = index.read_text(encoding="utf-8")
    tag_start = text.find(_CARD)
    if tag_start < 0:
        raise RuntimeError("Synthetic source lacks the notes home card")
    anchor_start = text.rfind("<a", 0, tag_start)
    anchor_end = text.find(">", tag_start)
    if anchor_start < 0 or anchor_end < 0:
        raise RuntimeError("Synthetic notes card anchor is malformed")
    anchor = text[anchor_start : anchor_end + 1]
    if 'aria-label="' in anchor:
        before, rest = anchor.split('aria-label="', 1)
        _old, after = rest.split('"', 1)
        updated = before + 'aria-label="' + value + '"' + after
    else:
        updated = anchor[:-1] + ' aria-label="' + value + '">'
    index.write_text(
        text[:anchor_start] + updated + text[anchor_end + 1 :],
        encoding="utf-8",
        newline="\n",
    )


def _make_fixture(work_root: Path) -> tuple[Path, dict]:
    base = work_root / ("live-ui-" + str(uuid.uuid4()))
    repo = base / "repo"
    repo.mkdir(parents=True, exist_ok=False)
    _copy_source_tree(repo)
    _set_notes_aria(repo / "index.html", _BAD_LABEL)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "core.longpaths", "true")
    _git(repo, "config", "user.name", "LADS Stage3 Synthetic")
    _git(repo, "config", "user.email", "lads-stage3@invalid.example")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "test: synthetic Stage3 live baseline")
    manifest = {
        "repo": str(repo),
        "task": (
            "Stage 3 synthetic UI QA. Edit index.html only. Build 1 is a transparent "
            f"negative drill: retain the current wrong aria-label={_BAD_LABEL!r} on the "
            f"a[data-home-card=notes] anchor and report BUILT. QA must fail. Build 2 and "
            f"later must set exactly aria-label={_LABEL!r} on that anchor and otherwise "
            "preserve href and all product behaviour. Run only the two prescribed Python "
            "-B unittest commands. This is an isolated private Git fixture with no remotes "
            "and no real browser profile or user data."
        ),
        "allowed_paths": ["index.html"],
        "test_commands": list(qa.QA_TEST_COMMANDS),
        "applicability": {"browser": False, "multiuser": False},
    }
    manifest_path = base / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return base, manifest


def _phase_evidence(run_root: Path) -> list[dict]:
    state = json.loads((run_root / "state.json").read_text(encoding="utf-8"))
    rows = []
    for ref in state.get("stage_reports", []):
        path = run_root / ref["path"]
        stage = json.loads(path.read_text(encoding="utf-8"))
        rows.append(
            {
                "role": stage.get("role"),
                "status": stage.get("status"),
                "build": stage.get("build"),
                "sha256": ref.get("sha256"),
            }
        )
    return rows


def execute(work_root: Path, timeout: int, credential_source: str = "env") -> dict:
    _load_worker_environment(credential_source)
    # The immutable QA commands deliberately use "python". When this explicit
    # entrypoint runs in the existing engineering venv, keep its interpreter
    # available to worker shells without dropping the original provisioning PATH.
    python_runtime = prepare_python_runtime()
    base, manifest_value = _make_fixture(work_root)
    manifest_path = base / "manifest.json"
    manifest = Stage3Runner.contract_manifest(BASE.load_manifest(manifest_path))
    executor = CodexJsonRpcExecutor(
        work_root=work_root,
        allowed_commands=tuple(suite["command"] for suite in qa.TRUSTED_SUITES),
        maximum_timeout=min(float(timeout), 300.0),
        runtime_executables={"python": python_runtime},
    )
    broker = qa.QABroker(
        executor=executor,
        work_root=work_root,
        timeout=min(float(timeout), 300.0),
        required_checks=qa.STAGE3_DEMO_REQUIRED_CHECKS,
    )
    runner = Stage3Runner(
        worker=BASE.RealWorker(),
        broker=broker,
        timeout=timeout,
    )
    try:
        handoff = runner.run(manifest)
    finally:
        executor.terminate_all()
    if handoff.get("task_status") != "AWAITING_MAIN_ACCEPTANCE":
        raise RuntimeError("Live Stage 3 workflow did not reach AWAITING_MAIN_ACCEPTANCE")
    run_root = runner.orchestrator.run_root
    phases = _phase_evidence(run_root)
    if not any(
        row["role"] == "qa-tester" and row["status"] == "FAIL" and row["build"] == 1
        for row in phases
    ):
        raise RuntimeError("Build 1 did not produce an actual QA FAIL")
    if not any(
        row["role"] == "reviewer"
        and row["status"] == "CHANGES_REQUESTED"
        and row["build"] == 1
        for row in phases
    ):
        raise RuntimeError("Build 1 did not produce Reviewer CHANGES_REQUESTED")
    final_build = handoff["builds"]
    if final_build < 2:
        raise RuntimeError("Live workflow did not exercise bounded rework")
    bundle = load_handoff_bundle(run_root=run_root, repo_root=Path(manifest_value["repo"]))
    if (
        len(bundle.changed_files) != 1
        or bundle.changed_files[0].get("path") != "index.html"
        or bundle.changed_files[0].get("kind") != "modified"
    ):
        raise RuntimeError("Live candidate was not the exact index.html-only change")

    namespace = "stage3-live-" + str(uuid.uuid4())
    journal = FileJournal.create(acceptance_namespace=namespace)
    knowledge_home = base / "knowledge"
    store = FileStore.open(knowledge_home, "knowledge.sqlite3")
    try:
        project = store.register_project(
            "Stage 3 live synthetic UI",
            root_ref=repository_ref(Path(manifest_value["repo"])),
        )
        knowledge = KnowledgePublisher(
            store,
            project["id"],
            Path(manifest_value["repo"]),
        )
        ack = write_main_ack(bundle)
        engine = register_delivery_engine(
            bundle,
            journal=journal,
            knowledge=knowledge,
            backup_root=base / "document-backups",
        )
        delivered = accept_handoff(
            bundle,
            engine,
            reason="Stage3 live synthetic MAIN-ACK",
            event_plan=standard_event_plan(
                bundle,
                reason="Stage3 live synthetic MAIN-ACK",
            ),
        )
    finally:
        store.close()
    if delivered.get("status") != "DELIVERY_READY" or delivered.get("ready") is not True:
        raise RuntimeError("MAIN-ACK finalizer did not reach DELIVERY_READY")
    return {
        "status": "PASS",
        "task_id": handoff["task_id"],
        "run_root": str(run_root),
        "candidate_sha256": handoff["candidate_sha256"],
        "builds": final_build,
        "phases": phases,
        "main_ack": ack,
        "delivery_status": delivered["status"],
        "simulation": False,
        "native_hooks_trusted": False,
        "integration_or_deploy": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-namespace")
    parser.add_argument("--work-root")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--credential-source",
        choices=("env", "windows-user"),
        default="env",
    )
    parser.add_argument("--execute-live", action="store_true")
    args = parser.parse_args()
    if not 30 <= args.timeout <= 3600:
        parser.error("timeout must be between 30 and 3600")
    if args.work_namespace:
        work_root = acceptance_root(args.work_namespace)
    elif args.work_root:
        work_root = Path(args.work_root).resolve(strict=False)
        private = (default_automation_root().parent / "acceptance").resolve(strict=False)
        if private not in work_root.parents:
            parser.error("--work-root must be under the owned LADS acceptance namespace")
    else:
        parser.error("provide --work-root or --work-namespace")
    work_root.mkdir(parents=True, exist_ok=True)
    if not args.execute_live:
        print(
            json.dumps(
                {
                    "status": "PENDING",
                    "live_executed": False,
                    "work_root": str(work_root),
                    "message": "Pass --execute-live to run the four-role UI workflow.",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 0
    try:
        result = execute(work_root, args.timeout, args.credential_source)
    except Exception as exc:
        result = {
            "status": "FAIL",
            "live_executed": True,
            "error_type": type(exc).__name__,
            "error": str(exc)[:1200],
            "artifact_root": str(work_root),
            "retained": True,
        }
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
