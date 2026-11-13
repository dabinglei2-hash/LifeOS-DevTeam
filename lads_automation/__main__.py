"""Minimal local CLI for the LADS engineering delivery gate."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lads_knowledge.database import FileStore, database_path, validate_home

from .engine import AutomationEngine, DocumentPublisher, KnowledgePublisher
from .evidence import TaskSpec
from .hooks import MAX_HOOK_INPUT_BYTES, handle_hook
from .state import FileJournal, JournalError


def _json_output(value: Any) -> None:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    print(text[:8000])


def _strict_payload(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or len(text.encode("utf-8")) > 16 * 1024:
        raise ValueError("Event payload is too large")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Event payload contains duplicate keys")
            result[key] = value
        return result

    parsed = json.loads(
        text,
        object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError("Event payload contains non-JSON numeric constants")
        ),
    )
    if not isinstance(parsed, dict):
        raise ValueError("Event payload must be a JSON object")
    return parsed


def _spec_from_state(state: dict[str, Any]) -> TaskSpec:
    return TaskSpec(
        task_id=state["task_id"],
        source_head=state["source_head"],
        candidate_sha256=state["candidate_sha256"],
        allowed_changes=tuple(state["allowed_changes"]),
        expected_qa_commands=tuple(state["expected_qa_commands"]),
    )


def _expected_hashes(values: list[str]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("Expected hash must use PATH=SHA or PATH=NONE")
        path, digest = value.split("=", 1)
        path = path.strip()
        if not path or path in result:
            raise ValueError("Expected hash path is empty or duplicated")
        result[path] = None if digest.strip().upper() == "NONE" else digest.strip().lower()
    return result


def _open_journal(*, create: bool = False) -> FileJournal:
    return FileJournal.create() if create else FileJournal.open()


def _knowledge(
    args: argparse.Namespace,
    spec: TaskSpec,
    repo_root: Path,
    requirement_summary: str,
) -> KnowledgePublisher | None:
    if args.knowledge_home is None and args.project_id is None:
        return None
    if args.knowledge_home is None or args.project_id is None:
        raise ValueError("Both --knowledge-home and --project-id are required")
    home = validate_home(args.knowledge_home)
    selected_db = database_path(home, "knowledge.sqlite3")
    if not selected_db.is_file():
        raise ValueError("Knowledge database is absent; CLI will not silently initialize it")
    store = FileStore.open(home, "knowledge.sqlite3")
    publisher = KnowledgePublisher(store, args.project_id, repo_root)
    publisher.ensure_task(spec.task_id, requirement_summary)
    return publisher


def _engine_for(
    journal: FileJournal,
    args: argparse.Namespace,
    *,
    require_publisher: bool,
) -> AutomationEngine:
    state = journal.get_task(args.task_id)
    spec = _spec_from_state(state)
    publisher = None
    repo_root = None
    knowledge = None
    if require_publisher or args.repo_root is not None:
        if args.repo_root is None or args.backup_root is None:
            raise ValueError("--repo-root and --backup-root are required for document sync")
        repo_root = Path(args.repo_root).resolve(strict=True)
        publisher = DocumentPublisher(
            repo_root,
            backup_root=args.backup_root,
            registered_repository_ref=state["repository_ref"],
        )
        knowledge = _knowledge(args, spec, repo_root, str(state["requirement_summary"]))
    return AutomationEngine(
        spec,
        journal,
        publisher=publisher,
        knowledge=knowledge,
        repo_root=repo_root,
        repository_ref_value=state.get("repository_ref"),
    )


def _add_knowledge_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--backup-root", required=True)
    parser.add_argument("--knowledge-home")
    parser.add_argument("--project-id")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lads_automation",
        description="Local LADS Stage 3 engineering delivery gate (no API keys, network, or native hook installation)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create-task", help="register one immutable TaskSpec")
    create.add_argument("--task-id", required=True)
    create.add_argument("--source-head", required=True)
    create.add_argument("--candidate-sha256", required=True)
    create.add_argument("--allowed-change", action="append", required=True)
    create.add_argument("--qa-command", action="append", required=True)
    create.add_argument("--summary", required=True)
    create.add_argument("--session-id")
    create.add_argument("--repository-ref")

    status = subparsers.add_parser("status", help="read one registered task")
    status.add_argument("--task-id", required=True)

    record = subparsers.add_parser("record-event", help="append one allowlisted domain event")
    record.add_argument("--task-id", required=True)
    record.add_argument("--event-type", required=True)
    record.add_argument("--event-id")
    record.add_argument("--payload-json", required=True)

    sync = subparsers.add_parser("sync", help="synchronize documents and, when configured, the index")
    sync.add_argument("--task-id", required=True)
    sync.add_argument("--expected-sha", action="append", default=[])
    sync.add_argument("--reason", default="CLI delivery synchronization")
    _add_knowledge_options(sync)

    retry = subparsers.add_parser("retry", help="bounded retry of a failed doc/index stage")
    retry.add_argument("--task-id", required=True)
    retry.add_argument("--reason", default="CLI bounded retry")
    _add_knowledge_options(retry)

    hook = subparsers.add_parser("hook", help="read one hook JSON payload from stdin")
    del hook
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "create-task":
            spec = TaskSpec(
                task_id=args.task_id,
                source_head=args.source_head,
                candidate_sha256=args.candidate_sha256,
                allowed_changes=tuple(args.allowed_change),
                expected_qa_commands=tuple(args.qa_command),
            )
            journal = _open_journal(create=True)
            engine = AutomationEngine(spec, journal, repository_ref_value=args.repository_ref)
            _json_output(engine.register(args.summary, session_id=args.session_id))
            return 0
        if args.command == "status":
            journal = _open_journal()
            state = journal.get_task(args.task_id)
            engine = AutomationEngine(_spec_from_state(state), journal)
            _json_output(engine.status(args.task_id))
            return 0
        if args.command == "record-event":
            journal = _open_journal()
            state = journal.get_task(args.task_id)
            engine = AutomationEngine(_spec_from_state(state), journal)
            row = engine.record_event(
                args.event_type,
                _strict_payload(args.payload_json),
                event_id=args.event_id,
            )
            _json_output({"id": row["id"], "event_type": row["event_type"], "idempotent": row["idempotent"]})
            return 0
        if args.command == "sync":
            journal = _open_journal()
            engine = _engine_for(journal, args, require_publisher=True)
            _json_output(
                engine.sync(
                    _expected_hashes(args.expected_sha),
                    reason=args.reason,
                )
            )
            return 0
        if args.command == "retry":
            journal = _open_journal()
            engine = _engine_for(journal, args, require_publisher=False)
            _json_output(engine.retry(reason=args.reason))
            return 0
        if args.command == "hook":
            raw = sys.stdin.buffer.read(MAX_HOOK_INPUT_BYTES + 1)
            if len(raw) > MAX_HOOK_INPUT_BYTES:
                raise ValueError("Hook input exceeds 64 KiB")
            try:
                payload = json.loads(
                    raw.decode("utf-8"),
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError("Hook input contains non-JSON numeric constants")
                    ),
                )
            except (UnicodeError, ValueError) as exc:
                raise ValueError("Hook input is not valid UTF-8 JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError("Hook input must be an object")
            try:
                journal = FileJournal.open()
            except JournalError:
                journal = None
            _json_output(handle_hook(payload, journal))
            return 0
        raise ValueError("Unknown command")
    except Exception as exc:
        message = str(exc).replace("\r", " ").replace("\n", " ")[:500]
        _json_output({"error": type(exc).__name__, "message": message})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
