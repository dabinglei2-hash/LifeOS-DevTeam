#!/usr/bin/env python3
"""Thin JSON CLI for the evidence-bound LADS GitHub release controller."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def _emit(value: dict[str, Any]) -> None:
    text = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(text.encode("utf-8")) > 32_000:
        print(
            json.dumps(
                {
                    "status": value.get("status", "FAIL"),
                    "truncated": True,
                    "result_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return
    print(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -B scripts/lads_github.py",
        description=(
            "Publish one controller-authorized LADS engineering change through "
            "an isolated branch and native auto-merge. This command never changes "
            "protections, merges immediately, deploys, or reads credentials."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish = subparsers.add_parser(
        "publish",
        help="validate Stage 3 DELIVERY_READY, commit, push, open PR, and enable native auto-merge",
    )
    publish.add_argument("--repo", required=True)
    publish.add_argument("--run-root", required=True)
    publish.add_argument("--authorization", required=True)
    publish.add_argument("--knowledge-home", required=True)
    publish.add_argument("--knowledge-db", default="knowledge.sqlite3")
    publish.add_argument("--project-id", required=True)
    publish.add_argument("--repository", choices=("dabinglei2-hash/LifeOS", "dabinglei2-hash/LifeOS-DevTeam"), default="dabinglei2-hash/LifeOS")
    publish.add_argument("--poll-attempts", type=int, default=12)
    publish.add_argument("--poll-interval", type=float, default=5.0)
    publish.add_argument(
        "--journal-namespace",
        help="read an isolated acceptance journal instead of the production default",
    )

    resume = subparsers.add_parser(
        "resume",
        help="resume the exact durable authorization and reconcile actual Git/GitHub state",
    )
    resume.add_argument("--repo", required=True)
    resume.add_argument("--run-root", required=True)
    resume.add_argument("--authorization", required=True)
    resume.add_argument("--knowledge-home", required=True)
    resume.add_argument("--knowledge-db", default="knowledge.sqlite3")
    resume.add_argument("--project-id", required=True)
    resume.add_argument("--repository", choices=("dabinglei2-hash/LifeOS", "dabinglei2-hash/LifeOS-DevTeam"), default="dabinglei2-hash/LifeOS")
    resume.add_argument("--poll-attempts", type=int, default=12)
    resume.add_argument("--poll-interval", type=float, default=5.0)
    resume.add_argument(
        "--journal-namespace",
        help="read an isolated acceptance journal instead of the production default",
    )

    status = subparsers.add_parser(
        "status",
        help="read one private release state without Git, network, or model calls",
    )
    status.add_argument("--task-id", required=True)
    return parser


def _publish(args: argparse.Namespace, *, resume: bool) -> int:
    from lads_automation.github_release import ReleaseController
    from lads_knowledge.database import FileStore

    home = Path(args.knowledge_home).resolve(strict=True)
    store = FileStore.open(home, args.knowledge_db)
    try:
        controller = ReleaseController(
            repo_root=Path(args.repo),
            run_root=Path(args.run_root),
            poll_attempts=args.poll_attempts,
            poll_interval=args.poll_interval,
            journal_namespace=args.journal_namespace,
            repository=args.repository,
        )
        result = controller.publish(
            args.authorization,
            knowledge_home=home,
            knowledge_db=args.knowledge_db,
            project_id=args.project_id,
            knowledge_store=store,
        )
        _emit(
            {
                "status": result.get("status"),
                "ready": result.get("ready") is True,
                "task_id": result.get("task_id"),
                "commit_sha": result.get("commit_sha"),
                "pr_number": result.get("pr_number"),
                "pr_url": result.get("pr_url"),
                "merge_commit_sha": result.get("merge_commit_sha"),
                "resume": resume,
                "automatic_integration": result.get("status") == "MERGED",
                "deployment": "NOT_PERFORMED",
            }
        )
        return 0 if result.get("status") == "MERGED" and result.get("ready") is True else 2
    finally:
        store.close()


def _status(args: argparse.Namespace) -> int:
    from lads_automation.github_release import ReleaseStore, read_state

    store = ReleaseStore()
    value = read_state(store, args.task_id)
    _emit(value)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "publish":
            return _publish(args, resume=False)
        if args.command == "resume":
            return _publish(args, resume=True)
        if args.command == "status":
            return _status(args)
        raise ValueError("Unknown command")
    except Exception as exc:
        try:
            from lads_automation.github_release import default_release_root

            release_root = default_release_root()
        except Exception:
            release_root = None
        _emit(
            {
                "status": "BLOCKED",
                "error": type(exc).__name__,
                "message": str(exc).replace("\r", " ").replace("\n", " ")[:800],
                "release_root": str(release_root) if release_root else None,
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
