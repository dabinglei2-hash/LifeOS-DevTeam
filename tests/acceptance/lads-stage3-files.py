#!/usr/bin/env python3
"""Actual private-file acceptance for Stage 3; never part of unittest discovery.

Run only through the writable isolated QA broker. All data is synthetic and
retained under one new LifeOS-LADS/acceptance namespace.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lads_automation.engine import DocumentPublisher, KnowledgePublisher
from lads_automation.delivery import _document_published_files, _publisher_events_match
from lads_automation.evidence import EvidenceProof
from lads_automation.hooks import handle_hook
from lads_automation.state import (
    FileJournal,
    JournalSessionAmbiguous,
    JournalSessionCorrupt,
    acceptance_root,
)
from lads_knowledge.database import FileStore
from lads_knowledge.indexer import repository_ref


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def payload_sha(payload: dict) -> str:
    return sha(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def task_state(task_id: str, status: str, session: str, *, ready: bool = False) -> dict:
    return {
        "task_id": task_id,
        "status": status,
        "ready": ready,
        "session_id": session,
        "event_ids": [],
    }


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError("Synthetic Git fixture failed: " + result.stderr[:400])
    return result.stdout.strip()


def main() -> int:
    namespace = "s3f-" + uuid.uuid4().hex[:12]
    base = acceptance_root(namespace)
    checks: list[dict] = []
    try:
        journal = FileJournal.create(acceptance_namespace=namespace)
        session = "synthetic-main-session"
        old = str(uuid.uuid4())
        current = str(uuid.uuid4())
        ambiguity_a = str(uuid.uuid4())
        ambiguity_b = str(uuid.uuid4())
        journal.create_task(task_state(old, "DELIVERY_READY", session, ready=True))
        journal.create_task(task_state(current, "TASK_CREATED", session))
        if journal.resolve_session(session) != current:
            raise AssertionError("A terminal task shadowed the current active task")
        reopened = FileJournal.open(acceptance_namespace=namespace)
        if reopened.resolve_session(session) != current:
            raise AssertionError("Reopened FileJournal changed session selection")
        checks.append(
            {
                "id": "file-journal-reopen-session",
                "status": "PASS",
                "evidence": "Reopened FileJournal ignored DELIVERY_READY and selected the sole active task.",
            }
        )

        ambiguous_session = "synthetic-ambiguous-session"
        journal.create_task(task_state(ambiguity_a, "TASK_CREATED", ambiguous_session))
        journal.create_task(task_state(ambiguity_b, "QA_REVIEW_VERIFIED", ambiguous_session))
        try:
            reopened.resolve_session(ambiguous_session)
        except JournalSessionAmbiguous:
            checks.append(
                {
                    "id": "file-journal-ambiguous-session",
                    "status": "PASS",
                    "evidence": "Two active tasks produced JournalSessionAmbiguous without selecting either.",
                }
            )
        else:
            raise AssertionError("Ambiguous session was resolved by insertion/order guessing")

        stop_task = str(uuid.uuid4())
        stop_session = "synthetic-stop-session"
        journal.create_task(task_state(stop_task, "TASK_CREATED", stop_session))
        results: list[dict] = []

        def stop_call() -> None:
            results.append(
                handle_hook(
                    {
                        "hook_event_name": "Stop",
                        "task_id": stop_task,
                        "session_id": stop_session,
                        "stop_hook_active": False,
                    },
                    reopened,
                )
            )

        threads = [threading.Thread(target=stop_call) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        continuations = [item for item in results if item.get("decision") == "block"]
        events = reopened.list_events(stop_task)
        if len(continuations) != 1 or len(
            [item for item in events if item["event_type"] == "stop_continuation_requested"]
        ) != 1:
            raise AssertionError("Concurrent Stop emitted more than one continuation")
        checks.append(
            {
                "id": "file-journal-stop-concurrency",
                "status": "PASS",
                "evidence": "Two concurrent Stop handlers produced exactly one durable continuation request.",
            }
        )

        corrupt_task = str(uuid.uuid4())
        corrupt_session = "synthetic-corrupt-session"
        reopened.create_task(task_state(corrupt_task, "TASK_CREATED", corrupt_session))
        corrupt_state = reopened.tasks_root / corrupt_task / "state.json"
        corrupt_state.write_text("{", encoding="utf-8")
        try:
            reopened.resolve_session(corrupt_session)
        except JournalSessionCorrupt as failure:
            warning = failure.warning()
            if (
                warning.get("unreadable_task_count") != 1
                or corrupt_task in repr(warning)
                or corrupt_session in repr(warning)
            ):
                raise AssertionError("Corrupt journal warning was unsafe or unbounded")
            hook_result = handle_hook(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": corrupt_session,
                    "tool_name": "exec_command",
                    "tool_input": {"command": "git status --short"},
                },
                reopened,
            )
            if hook_result.get("hookSpecificOutput", {}).get("permissionDecision") != "deny":
                raise AssertionError("Corrupt journal did not deny PreToolUse")
            checks.append(
                {
                    "id": "file-journal-corrupt-fail-closed",
                    "status": "PASS",
                    "evidence": "Actual malformed task state produced a bounded warning and PreToolUse deny without exposing task/session IDs.",
                }
            )
        else:
            raise AssertionError("Malformed task state was silently skipped during session resolution")

        repo = base / "synthetic-repo"
        docs = repo / "docs"
        docs.mkdir(parents=True)
        status_doc = docs / "STATUS.md"
        changelog_doc = docs / "CHANGELOG.md"
        status_doc.write_text("# Synthetic status\n\nOriginal sentinel.\n", encoding="utf-8")
        changelog_doc.write_text("# Synthetic changelog\n\nOriginal retry sentinel.\n", encoding="utf-8")
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.name", "LADS Synthetic Fixture")
        git(repo, "config", "user.email", "lads-fixture@invalid.example")
        git(repo, "add", "docs/STATUS.md", "docs/CHANGELOG.md")
        git(repo, "commit", "-m", "test: synthetic status")
        source_head = git(repo, "rev-parse", "HEAD")

        doc_task = str(uuid.uuid4())
        reopened.create_task(task_state(doc_task, "QA_REVIEW_VERIFIED", "synthetic-doc-session"))
        evidence = EvidenceProof(
            task_id=doc_task,
            candidate_sha256="a" * 64,
            controller_sha256="b" * 64,
            qa_sha256="c" * 64,
            reviewer_sha256="d" * 64,
            commands=("synthetic command receipt",),
            simulated=True,
        )
        entry = {
            "event_id": str(uuid.uuid4()),
            "event_type": "task_state_changed",
            "payload": {"summary": "Synthetic private-file acceptance"},
            "payload_sha256": payload_sha(
                {"summary": "Synthetic private-file acceptance"}
            ),
            "occurred_at": "2026-11-04T00:00:00+00:00",
        }
        publisher = DocumentPublisher(
            repo,
            backup_root=base / "document-backups",
            registered_repository_ref=repository_ref(repo),
        )
        original = status_doc.read_bytes()
        first = publisher.publish(
            task_id=doc_task,
            source_head=source_head,
            required_docs=("docs/STATUS.md",),
            entries={"docs/STATUS.md": [entry]},
            expected_old_shas={"docs/STATUS.md": sha(original)},
            journal=reopened,
            evidence=evidence,
        )
        document_receipts = {"initial": first["documents"]}
        backups = [
            item
            for item in (base / "document-backups").rglob("docs/STATUS.md")
            if item.is_file()
        ]
        if not backups or backups[0].read_bytes() != original or not first["documents"]["docs/STATUS.md"]:
            raise AssertionError("DocumentPublisher did not preserve a private old-byte backup")
        checks.append(
            {
                "id": "document-private-backup",
                "status": "PASS",
                "evidence": "Actual private backup contains the exact pre-write document bytes.",
            }
        )

        retry_task = str(uuid.uuid4())
        reopened.create_task(
            task_state(retry_task, "QA_REVIEW_VERIFIED", "synthetic-doc-retry-session")
        )
        retry_entry = {
            **entry,
            "event_id": str(uuid.uuid4()),
            "payload": {"summary": "Synthetic document retry"},
        }
        retry_entry["payload_sha256"] = payload_sha(retry_entry["payload"])
        retry_evidence = EvidenceProof(
            task_id=retry_task,
            candidate_sha256="f" * 64,
            controller_sha256="0" * 64,
            qa_sha256="1" * 64,
            reviewer_sha256="2" * 64,
            commands=("synthetic command receipt",),
            simulated=True,
        )
        retry_original = changelog_doc.read_bytes()
        recovery_backup = publisher._backup
        recovery_calls = {"count": 0}

        def fail_recovery_backup(*args, **kwargs):
            recovery_calls["count"] += 1
            if recovery_calls["count"] == 1:
                raise OSError("synthetic document failure")
            return recovery_backup(*args, **kwargs)

        publisher._backup = fail_recovery_backup
        try:
            publisher.publish(
                task_id=retry_task,
                source_head=source_head,
                required_docs=("docs/CHANGELOG.md",),
                entries={"docs/CHANGELOG.md": [retry_entry]},
                expected_old_shas={"docs/CHANGELOG.md": sha(retry_original)},
                journal=reopened,
                evidence=retry_evidence,
            )
        except OSError:
            pass
        else:
            raise AssertionError("Synthetic document failure did not interrupt the first attempt")
        retried_document = publisher.publish(
            task_id=retry_task,
            source_head=source_head,
            required_docs=("docs/CHANGELOG.md",),
            entries={"docs/CHANGELOG.md": [retry_entry]},
            expected_old_shas={"docs/CHANGELOG.md": sha(retry_original)},
            journal=reopened,
            evidence=retry_evidence,
        )
        publisher._backup = recovery_backup
        if not retried_document["documents"]["docs/CHANGELOG.md"].get("after_sha256"):
            raise AssertionError("Document retry lacks an actual after-SHA receipt")
        published_state = {"documents_receipt": retried_document}
        expected_published = _document_published_files(
            {"docs/CHANGELOG.md": sha(retry_original)},
            published_state,
            task_id=retry_task,
        )
        actual_published = {
            "docs/CHANGELOG.md": sha(changelog_doc.read_bytes())
        }
        if expected_published != actual_published:
            raise AssertionError("Verified publisher after-state did not match actual bytes")
        if not _publisher_events_match(
            published_state,
            reopened.list_events(retry_task),
            task_id=retry_task,
        ):
            raise AssertionError("Document state lacked exact publisher completion events")
        if _document_published_files(
            {"docs/CHANGELOG.md": "0" * 64},
            published_state,
            task_id=retry_task,
        ) is not None:
            raise AssertionError("Publisher state accepted an unrelated document baseline")
        altered_events = reopened.list_events(retry_task)
        for row in altered_events:
            if row.get("event_type") == "document_write_completed":
                row["payload"]["after_sha256"] = "f" * 64
                break
        if _publisher_events_match(
            published_state,
            altered_events,
            task_id=retry_task,
        ):
            raise AssertionError("Forged document completion event was accepted")
        document_receipts["recovery"] = retried_document["documents"]
        checks.append(
            {
                "id": "document-failure-retry-receipt",
                "status": "PASS",
                "evidence": "An interrupted document write retained its started receipt and the retry returned an actual after-SHA receipt.",
            }
        )

        after_first = status_doc.read_bytes()
        status_doc.write_bytes(after_first + b"\nConcurrent user edit.\n")
        concurrent = status_doc.read_bytes()
        try:
            publisher.publish(
                task_id=doc_task,
                source_head=source_head,
                required_docs=("docs/STATUS.md",),
                entries={"docs/STATUS.md": [entry]},
                expected_old_shas={"docs/STATUS.md": sha(original)},
                journal=reopened,
                evidence=evidence,
            )
        except Exception:
            pass
        else:
            raise AssertionError("Stale optimistic document SHA was accepted")
        if status_doc.read_bytes() != concurrent:
            raise AssertionError("Document conflict overwrote a concurrent user edit")
        checks.append(
            {
                "id": "document-old-sha-conflict",
                "status": "PASS",
                "evidence": "Stale old SHA was refused and the concurrent edit remained byte-identical.",
            }
        )

        interrupted_task = str(uuid.uuid4())
        reopened.create_task(
            task_state(interrupted_task, "QA_REVIEW_VERIFIED", "synthetic-interrupted-session")
        )
        interrupted_entry = {
            **entry,
            "event_id": str(uuid.uuid4()),
            "payload": {"summary": "Synthetic interrupted document"},
        }
        interrupted_entry["payload_sha256"] = payload_sha(
            interrupted_entry["payload"]
        )
        interrupted_evidence = EvidenceProof(
            task_id=interrupted_task,
            candidate_sha256="1" * 64,
            controller_sha256="2" * 64,
            qa_sha256="3" * 64,
            reviewer_sha256="4" * 64,
            commands=("synthetic command receipt",),
            simulated=True,
        )
        before_interrupt = status_doc.read_bytes()
        original_backup = publisher._backup
        calls = {"count": 0}

        def fail_once(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("synthetic interrupted backup")
            return original_backup(*args, **kwargs)

        publisher._backup = fail_once
        try:
            publisher.publish(
                task_id=interrupted_task,
                source_head=source_head,
                required_docs=("docs/STATUS.md",),
                entries={"docs/STATUS.md": [interrupted_entry]},
                expected_old_shas={"docs/STATUS.md": sha(before_interrupt)},
                journal=reopened,
                evidence=interrupted_evidence,
            )
        except OSError:
            pass
        status_doc.write_bytes(before_interrupt + b"\nEdit after interruption.\n")
        interrupted_concurrent = status_doc.read_bytes()
        try:
            publisher.publish(
                task_id=interrupted_task,
                source_head=source_head,
                required_docs=("docs/STATUS.md",),
                entries={"docs/STATUS.md": [interrupted_entry]},
                expected_old_shas={"docs/STATUS.md": sha(before_interrupt)},
                journal=reopened,
                evidence=interrupted_evidence,
            )
        except Exception:
            pass
        else:
            raise AssertionError("Interrupted retry overwrote concurrent edits")
        if status_doc.read_bytes() != interrupted_concurrent:
            raise AssertionError("Interrupted retry changed concurrent bytes")
        checks.append(
            {
                "id": "document-interrupted-retry",
                "status": "PASS",
                "evidence": "Interrupted started receipt plus concurrent edit remained preserved on retry.",
            }
        )
        document_receipts["interrupted"] = reopened.list_events(
            interrupted_task,
            event_type="document_write_started",
        )

        home = base / "knowledge"
        store = FileStore.open(home, "knowledge.sqlite3")
        try:
            project = store.register_project(
                "Synthetic Stage 3 rollback",
                root_ref=repository_ref(repo),
            )
            index_task = str(uuid.uuid4())
            store.create_task(
                project["id"],
                "Synthetic index rollback task",
                task_id=index_task,
            )
            store.record_event(
                project["id"],
                index_task,
                "baseline_preserved",
                {"summary": "preserve this synthetic row"},
            )
            knowledge = KnowledgePublisher(store, project["id"], repo)
            actual_sync = knowledge.indexer.sync_documents
            knowledge.indexer.sync_documents = lambda _root: (_ for _ in ()).throw(
                RuntimeError("synthetic index failure")
            )
            try:
                knowledge.sync(
                    task_id=index_task,
                    source_head=source_head,
                    reason="synthetic failure",
                    documents={},
                    expected_commands=(),
                    evidence=EvidenceProof(
                        task_id=index_task,
                        candidate_sha256="5" * 64,
                        controller_sha256="6" * 64,
                        qa_sha256="7" * 64,
                        reviewer_sha256="8" * 64,
                        commands=(),
                        simulated=True,
                    ),
                    index_event_id=str(uuid.uuid4()),
                )
            except RuntimeError:
                pass
            with store._engine.connect() as connection:
                preserved = connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM task_events WHERE task_id=? AND event_type=?",
                    (index_task, "baseline_preserved"),
                ).scalar_one()
                partial = connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM task_events WHERE task_id=? AND event_type=?",
                    (index_task, "index_synced"),
                ).scalar_one()
            if preserved != 1 or partial != 0:
                raise AssertionError("Failed SQLite index transaction did not roll back")
            knowledge.indexer.sync_documents = actual_sync
            retried = knowledge.sync(
                task_id=index_task,
                source_head=source_head,
                reason="synthetic retry",
                documents={},
                expected_commands=(),
                evidence=EvidenceProof(
                    task_id=index_task,
                    candidate_sha256="9" * 64,
                    controller_sha256="a" * 64,
                    qa_sha256="b" * 64,
                    reviewer_sha256="c" * 64,
                    commands=(),
                    simulated=True,
                ),
                index_event_id=str(uuid.uuid4()),
                entries=knowledge.indexer.policy.collect_documents(repo)["entries"],
            )
            if not retried.get("index_event_id"):
                raise AssertionError("SQLite index retry did not create a receipt")
            index_receipt = retried
            checks.append(
                {
                    "id": "sqlite-index-rollback-retry",
                    "status": "PASS",
                    "evidence": "Actual failed SQLite transaction preserved the baseline and a later retry succeeded.",
                }
            )
        finally:
            store.close()

        result = {
            "status": "PASS",
            "checks": checks,
            "namespace": namespace,
            "private_root": str(base),
            "synthetic_only": True,
            "simulation": "FILE_IO_FIXTURE_ONLY_NOT_MODEL_APPROVAL",
            "source_head": source_head,
            "document_receipts": document_receipts,
            "index_receipt": index_receipt,
        }
    except Exception as exc:
        result = {
            "status": "FAIL",
            "checks": checks,
            "namespace": namespace,
            "private_root": str(base),
            "synthetic_only": True,
            "error": type(exc).__name__ + ": " + str(exc)[:1200],
        }
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
