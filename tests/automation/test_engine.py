"""Memory-only tests for the Stage 3 delivery gate."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import unittest
import uuid
from unittest import mock

import lads_automation.__main__ as automation_cli
from lads_automation.engine import (
    AutomationEngine,
    DeliveryError,
    EVENT_DOCUMENTS,
    MemoryPublisher,
    WorkflowState,
)
from lads_automation.evidence import TaskSpec, receipt_digest, validate_stage_acceptance
from lads_automation.state import JournalConflict, MemoryJournal
from lads_knowledge import KnowledgeStore
from lads_knowledge.indexer import DEFAULT_DOCS, KnowledgeIndexer, SourcePolicy

TASK = "1c432bf8-ae19-4148-aef4-ed6f51ce7cc7"
PROJECT = "22222222-2222-4222-8222-222222222222"
SOURCE = "a" * 40
CANDIDATE = "c" * 64
ROOT_REF = "repositories/" + "d" * 64
QA = "python -B -m unittest discover -s tests/automation -v"
QA2 = "python -B -m unittest discover -s tests/knowledge -v"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def receipts(
    spec: TaskSpec,
    *,
    qa_changes: dict | None = None,
    reviewer_changes: dict | None = None,
    simulation: bool = True,
):
    qa = {
        "task_id": spec.task_id,
        "role": "qa-tester",
        "status": "PASS",
        "candidate_sha256": spec.candidate_sha256,
        "simulation": simulation,
        "command_executions": [
            {
                "execution_type": "command_execution",
                "command": command,
                "status": "completed",
                "exit_code": 0,
                "candidate_sha256": spec.candidate_sha256,
            }
            for command in spec.expected_qa_commands
        ],
    }
    reviewer = {
        "task_id": spec.task_id,
        "role": "reviewer",
        "status": "APPROVED",
        "candidate_sha256": spec.candidate_sha256,
        "independent": True,
        "review_type": "final",
        "simulation": simulation,
    }
    if qa_changes:
        qa.update(qa_changes)
    if reviewer_changes:
        reviewer.update(reviewer_changes)
    controller = {
        "task_id": spec.task_id,
        "candidate_sha256": spec.candidate_sha256,
        "status": "VALID",
        "simulation": simulation,
    }
    controller["stage_files"] = {
        "qa": {"path": "qa.json", "sha256": receipt_digest(qa)},
        "reviewer": {"path": "reviewer.json", "sha256": receipt_digest(reviewer)},
    }
    return qa, reviewer, controller


def spec() -> TaskSpec:
    return TaskSpec(
        task_id=TASK,
        source_head=SOURCE,
        candidate_sha256=CANDIDATE,
        allowed_changes=(
            "lads_automation/engine.py",
            "tests/automation/test_engine.py",
        ),
        expected_qa_commands=(QA, QA2),
    )


class MemoryKnowledge:
    """Real KnowledgeStore/indexer adapter for memory-only delivery tests."""

    def __init__(self, store: KnowledgeStore):
        self.store = store
        self.indexer = KnowledgeIndexer(store, PROJECT, documents=DEFAULT_DOCS)
        self.fail_next = False
        self.last_stats = None

    def ensure_task(self, task_id: str, summary: str):
        return self.store.create_task(PROJECT, summary, task_id=task_id, assigned_agent="builder")

    def sync(
        self,
        *,
        task_id,
        source_head,
        reason,
        documents,
        expected_commands,
        evidence,
        index_event_id,
        entries=None,
    ):
        with self.store.batch():
            if self.fail_next:
                self.store.record_event(
                    PROJECT,
                    task_id,
                    "probe_should_rollback",
                    {"reason": "synthetic rollback"},
                    event_id=str(uuid.uuid5(uuid.UUID(task_id), "probe")),
                )
                raise RuntimeError("synthetic index failure")
            snapshot = []
            for path in DEFAULT_DOCS:
                text = documents.get(path)
                if text is None:
                    snapshot.append({"path": path, "status": "missing"})
                else:
                    snapshot.append(
                        {
                            "path": path,
                            "status": "present",
                            "text": text,
                            "raw_sha": sha(text),
                            "committed_sha": None,
                            "dirty": True,
                        }
                    )
            self.last_stats = self.indexer.index_documents(
                root_ref=ROOT_REF,
                observed_head=SOURCE,
                entries=snapshot,
            )
            self.store.record_event(
                PROJECT,
                task_id,
                "index_synced",
                {
                    "task_id": task_id,
                    "source_head": source_head,
                    "reason": reason,
                    "reviewer_sha256": evidence.reviewer_sha256,
                    "qa_sha256": evidence.qa_sha256,
                },
                event_id=index_event_id,
            )
            for position, command in enumerate(expected_commands):
                self.store.record_test(
                    PROJECT,
                    task_id,
                    test_name="LADS prescribed QA command",
                    command=command,
                    status="passed",
                    summary=f"controller-bound QA receipt {evidence.qa_sha256}",
                    output_sha=evidence.qa_sha256,
                    test_id=str(uuid.uuid5(uuid.UUID(task_id), f"test-{position}")),
                )
        return {
            "index_event_id": index_event_id,
            "working_content_commit": None,
            "documents": list(documents),
            "observed_head": source_head,
            "stats": self.last_stats,
        }


class EngineCase(unittest.TestCase):
    def make_engine(self, *, with_knowledge=False):
        journal = MemoryJournal()
        publisher = MemoryPublisher({})
        knowledge = None
        store = None
        if with_knowledge:
            store = KnowledgeStore.memory()
            self.addCleanup(store.close)
            store.register_project("Synthetic Stage 3", root_ref=ROOT_REF, project_id=PROJECT)
            knowledge = MemoryKnowledge(store)
        engine = AutomationEngine(
            spec(),
            journal,
            publisher=publisher,
            knowledge=knowledge,
            repository_ref_value=ROOT_REF,
        )
        engine.register("Synthetic Stage 3 delivery")
        return engine, journal, publisher, knowledge, store

    def test_event_routing_covers_all_ten_documents(self):
        self.assertTrue(set(EVENT_DOCUMENTS.values()).issubset(DEFAULT_DOCS))
        self.assertIn("docs/LADS_STAGE3_ACCEPTANCE_REPORT.md", DEFAULT_DOCS)
        policy = SourcePolicy()
        self.assertEqual(
            set(policy.documents.values()),
            set(DEFAULT_DOCS),
        )
        for required in (
            "docs/KNOWN_ISSUES.md",
            "docs/TASK_HISTORY.md",
            "docs/LADS_AUTOMATION.md",
        ):
            self.assertIn(required, policy.documents.values())
        documents = {path: "approved baseline\n" for path in set(EVENT_DOCUMENTS.values())}
        journal = MemoryJournal()
        engine = AutomationEngine(
            spec(),
            journal,
            publisher=MemoryPublisher(documents),
            repository_ref_value=ROOT_REF,
        )
        engine.register("Route all fixed events")
        qa, reviewer, controller = receipts(spec())
        engine.verify((qa, reviewer, controller), simulation=True)
        for event_type, path in EVENT_DOCUMENTS.items():
            payload = {"summary": f"routed {event_type}"}
            if event_type in {
                "requirements_changed",
                "architecture_changed",
                "data_model_changed",
                "agent_rules_changed",
                "toolchain_changed",
                "workflow_changed",
            }:
                payload["doc_approval"] = {
                    "path": path,
                    "approved": True,
                    "document_sha256": sha(documents[path]),
                }
            event_id = str(uuid.uuid5(uuid.UUID(TASK), event_type))
            first = engine.record_event(event_type, payload, event_id=event_id)
            second = engine.record_event(event_type, payload, event_id=event_id)
            self.assertFalse(first["idempotent"])
            self.assertTrue(second["idempotent"])
        self.assertEqual(set(engine.required_documents()), set(EVENT_DOCUMENTS.values()))
        self.assertEqual(len(engine.required_documents()), 10)

    def test_builder_claims_missing_skipped_nonzero_and_digest_mismatch_are_blocked(self):
        task = spec()
        valid = receipts(task)
        builder_claim = receipts(
            task,
            qa_changes={"role": "builder", "status": "BUILT"},
        )
        with self.assertRaises(ValueError):
            validate_stage_acceptance(task, builder_claim, simulation=True)

        missing_qa = receipts(
            task,
            qa_changes={
                "command_executions": [valid[0]["command_executions"][0]],
            },
        )
        with self.assertRaises(ValueError):
            validate_stage_acceptance(task, missing_qa, simulation=True)

        skipped_qa = receipts(
            task,
            qa_changes={
                "command_executions": [
                    {
                        "execution_type": "command_execution",
                        "command": QA,
                        "status": "skipped",
                        "exit_code": 0,
                        "candidate_sha256": CANDIDATE,
                    },
                    valid[0]["command_executions"][1],
                ]
            },
        )
        with self.assertRaises(ValueError):
            validate_stage_acceptance(task, skipped_qa, simulation=True)

        nonzero_qa = receipts(
            task,
            qa_changes={
                "command_executions": [
                    {**valid[0]["command_executions"][0], "exit_code": 1},
                    valid[0]["command_executions"][1],
                ]
            },
        )
        with self.assertRaises(ValueError):
            validate_stage_acceptance(task, nonzero_qa, simulation=True)

        wrong_reviewer = receipts(
            task,
            reviewer_changes={"candidate_sha256": "e" * 64},
        )
        with self.assertRaises(ValueError):
            validate_stage_acceptance(task, wrong_reviewer, simulation=True)

    def test_failed_qa_gate_cannot_reach_documents(self):
        engine, journal, _, _, _ = self.make_engine()
        task = spec()
        failed = receipts(
            task,
            qa_changes={"role": "builder", "status": "BUILT"},
        )
        with self.assertRaises(ValueError):
            engine.verify(failed, simulation=True)
        self.assertEqual(journal.get_task(TASK)["status"], WorkflowState.QA_FAILED)
        with self.assertRaises(DeliveryError):
            engine.sync({"docs/KNOWN_ISSUES.md": None})
        self.assertFalse(engine.status()["ready"])

    def test_successful_qa_review_docs_index_reaches_delivery_ready(self):
        engine, journal, _, knowledge, store = self.make_engine(with_knowledge=True)
        qa, reviewer, controller = receipts(spec())
        state = engine.verify((qa, reviewer, controller), simulation=True)
        self.assertEqual(state["status"], WorkflowState.QA_REVIEW_VERIFIED)
        engine.record_event("issue_found", {"summary": "synthetic issue", "severity": "low"})
        engine.record_event("task_delivered", {"summary": "component delivery metadata"})
        result = engine.sync(
            {path: None for path in engine.required_documents()},
            reason="synthetic end-to-end delivery",
        )
        self.assertEqual(result["status"], WorkflowState.DELIVERY_READY)
        self.assertTrue(result["ready"])
        self.assertIn("docs/KNOWN_ISSUES.md", journal.get_task(TASK)["documents_receipt"]["documents"])
        with store.batch():
            event_count = store._local.connection.exec_driver_sql(
                "SELECT COUNT(*) FROM task_events WHERE task_id=? AND event_type='index_synced'",
                (TASK,),
            ).scalar_one()
            test_count = store._local.connection.exec_driver_sql(
                "SELECT COUNT(*) FROM test_runs WHERE task_id=?",
                (TASK,),
            ).scalar_one()
        self.assertEqual(event_count, 1)
        self.assertEqual(test_count, 2)
        self.assertEqual(knowledge.last_stats["documents"], len(DEFAULT_DOCS))

    def test_cli_status_uses_derived_delivery_gate(self):
        _, journal, _, _, _ = self.make_engine()
        state = journal.get_task(TASK)
        state["status"] = WorkflowState.DELIVERY_READY.value
        state["ready"] = True
        journal.save_task(state)

        output = io.StringIO()
        with (
            mock.patch.object(automation_cli, "_open_journal", return_value=journal),
            contextlib.redirect_stdout(output),
        ):
            exit_code = automation_cli.main(["status", "--task-id", TASK])

        self.assertEqual(exit_code, 0)
        self.assertFalse(json.loads(output.getvalue())["ready"])

    def test_document_failure_is_hard_gate(self):
        engine, journal, _, _, _ = self.make_engine()
        qa, reviewer, controller = receipts(spec())
        engine.verify((qa, reviewer, controller), simulation=True)
        engine.record_event("issue_found", {"summary": "force docs"})
        with self.assertRaises(DeliveryError):
            engine.sync({"docs/KNOWN_ISSUES.md": "f" * 64}, reason="bad baseline")
        state = journal.get_task(TASK)
        self.assertEqual(state["status"], WorkflowState.DOCS_FAILED)
        self.assertFalse(state["ready"])
        with self.assertRaises(DeliveryError):
            engine.sync_index()
        with self.assertRaises(DeliveryError):
            engine.record_event("issue_found", {"summary": "late event"})

    def test_document_retry_limit_applies_to_public_sync_entrypoint(self):
        engine, journal, _, _, _ = self.make_engine()
        qa, reviewer, controller = receipts(spec())
        engine.verify((qa, reviewer, controller), simulation=True)
        engine.record_event("issue_found", {"summary": "bounded document retries"})
        expected = {"docs/KNOWN_ISSUES.md": "f" * 64}
        for _ in range(engine.max_retries):
            with self.assertRaises(DeliveryError):
                engine.sync_documents(expected)
        state = journal.get_task(TASK)
        self.assertEqual(state["status"], WorkflowState.DOCS_FAILED)
        self.assertEqual(
            sum(row["stage"] == "DOCS" for row in state["failed_attempts"]),
            engine.max_retries,
        )
        with self.assertRaises(DeliveryError):
            engine.retry()
        with self.assertRaises(DeliveryError):
            engine.sync_documents(expected)
        with self.assertRaises(DeliveryError):
            engine.sync_index()
        with self.assertRaises(DeliveryError):
            engine.record_event("issue_found", {"summary": "blocked event"})
        self.assertEqual(journal.get_task(TASK)["status"], WorkflowState.BLOCKED)
        with self.assertRaises(DeliveryError):
            engine.sync_documents(expected)
        state = journal.get_task(TASK)
        self.assertEqual(state["status"], WorkflowState.BLOCKED)
        self.assertFalse(state["ready"])
        self.assertEqual(
            sum(row["stage"] == "DOCS" for row in state["failed_attempts"]),
            engine.max_retries,
        )

    def test_index_failure_keeps_retry_state_and_retry_is_idempotent(self):
        engine, journal, _, knowledge, store = self.make_engine(with_knowledge=True)
        qa, reviewer, controller = receipts(spec())
        engine.verify((qa, reviewer, controller), simulation=True)
        engine.record_event("issue_found", {"summary": "retry issue"})
        knowledge.fail_next = True
        with self.assertRaises(DeliveryError):
            engine.sync({"docs/KNOWN_ISSUES.md": None}, reason="first index")
        failed = journal.get_task(TASK)
        self.assertEqual(failed["status"], WorkflowState.INDEX_FAILED)
        self.assertFalse(failed["ready"])
        self.assertEqual(failed["index_attempts"], 1)
        self.assertIn("index_retry_payload", failed)
        with store.batch():
            probes = store._local.connection.exec_driver_sql(
                "SELECT COUNT(*) FROM task_events WHERE event_type='probe_should_rollback'"
            ).scalar_one()
        self.assertEqual(probes, 0)
        knowledge.fail_next = False
        result = engine.retry(reason="second index")
        self.assertTrue(result["ready"])
        with store.batch():
            documents = store._local.connection.exec_driver_sql(
                "SELECT COUNT(DISTINCT source_ref) FROM knowledge_items "
                "WHERE kind='document' AND status='active'"
            ).scalar_one()
            events = store._local.connection.exec_driver_sql(
                "SELECT COUNT(*) FROM task_events WHERE event_type='index_synced'"
            ).scalar_one()
        self.assertEqual(documents, 1)
        self.assertEqual(events, 1)

    def test_partial_document_retry_preserves_concurrent_user_edit(self):
        class FailAfterOne(MemoryPublisher):
            fail = True

            def publish(self, **kwargs):
                if self.fail:
                    first = kwargs["required_docs"][0]
                    partial = dict(kwargs)
                    partial["required_docs"] = [first]
                    partial["entries"] = {first: kwargs["entries"][first]}
                    partial["expected_old_shas"] = {
                        first: kwargs["expected_old_shas"][first]
                    }
                    super().publish(**partial)
                    raise RuntimeError("synthetic partial failure")
                return super().publish(**kwargs)

        journal = MemoryJournal()
        publisher = FailAfterOne({})
        engine = AutomationEngine(
            spec(),
            journal,
            publisher=publisher,
            repository_ref_value=ROOT_REF,
        )
        engine.register("Partial retry")
        qa, reviewer, controller = receipts(spec())
        engine.verify((qa, reviewer, controller), simulation=True)
        engine.record_event("issue_found", {"summary": "issue first"})
        engine.record_event("task_delivered", {"summary": "history second"})
        expected = {"docs/KNOWN_ISSUES.md": None, "docs/TASK_HISTORY.md": None}
        with self.assertRaises(DeliveryError):
            engine.sync(expected)
        first_text = publisher.documents["docs/KNOWN_ISSUES.md"]
        publisher.documents["docs/TASK_HISTORY.md"] = "USER EDIT MUST SURVIVE\n"
        publisher.fail = False
        with self.assertRaises(DeliveryError):
            engine.retry()
        self.assertEqual(
            publisher.documents["docs/TASK_HISTORY.md"],
            "USER EDIT MUST SURVIVE\n",
        )
        self.assertEqual(first_text.count("<!-- lads-automation:event"), 1)

    def test_partial_document_retry_is_idempotent_when_content_is_unchanged(self):
        class FailAfterOne(MemoryPublisher):
            fail = True

            def publish(self, **kwargs):
                if self.fail:
                    first = kwargs["required_docs"][0]
                    partial = dict(kwargs)
                    partial["required_docs"] = [first]
                    partial["entries"] = {first: kwargs["entries"][first]}
                    partial["expected_old_shas"] = {
                        first: kwargs["expected_old_shas"][first]
                    }
                    super().publish(**partial)
                    raise RuntimeError("synthetic partial failure")
                return super().publish(**kwargs)

        journal = MemoryJournal()
        publisher = FailAfterOne({})
        engine = AutomationEngine(
            spec(),
            journal,
            publisher=publisher,
            repository_ref_value=ROOT_REF,
        )
        engine.register("Partial idempotent retry")
        qa, reviewer, controller = receipts(spec())
        engine.verify((qa, reviewer, controller), simulation=True)
        engine.record_event("issue_found", {"summary": "issue first"})
        engine.record_event("task_delivered", {"summary": "history second"})
        expected = {"docs/KNOWN_ISSUES.md": None, "docs/TASK_HISTORY.md": None}
        with self.assertRaises(DeliveryError):
            engine.sync(expected)
        publisher.fail = False
        result = engine.retry(reason="resume partial write")
        self.assertEqual(result["status"], WorkflowState.DOCS_SYNCED)
        self.assertFalse(result["ready"])
        self.assertEqual(
            publisher.documents["docs/KNOWN_ISSUES.md"].count(
                "<!-- lads-automation:event"
            ),
            1,
        )
        self.assertEqual(
            len(journal.list_events(TASK, event_type="document_write_completed")),
            2,
        )

    def test_completed_document_retry_rejects_later_user_edit(self):
        class FailAfterOne(MemoryPublisher):
            fail = True

            def publish(self, **kwargs):
                if self.fail:
                    first = kwargs["required_docs"][0]
                    partial = dict(kwargs)
                    partial["required_docs"] = [first]
                    partial["entries"] = {first: kwargs["entries"][first]}
                    partial["expected_old_shas"] = {
                        first: kwargs["expected_old_shas"][first]
                    }
                    super().publish(**partial)
                    raise RuntimeError("synthetic partial failure")
                return super().publish(**kwargs)

        journal = MemoryJournal()
        publisher = FailAfterOne({})
        engine = AutomationEngine(
            spec(),
            journal,
            publisher=publisher,
            repository_ref_value=ROOT_REF,
        )
        engine.register("Completed edit conflict")
        qa, reviewer, controller = receipts(spec())
        engine.verify((qa, reviewer, controller), simulation=True)
        engine.record_event("issue_found", {"summary": "issue first"})
        engine.record_event("task_delivered", {"summary": "history second"})
        expected = {"docs/KNOWN_ISSUES.md": None, "docs/TASK_HISTORY.md": None}
        with self.assertRaises(DeliveryError):
            engine.sync(expected)
        edited = publisher.documents["docs/KNOWN_ISSUES.md"] + "USER EDIT\n"
        publisher.documents["docs/KNOWN_ISSUES.md"] = edited
        publisher.fail = False
        with self.assertRaises(DeliveryError):
            engine.retry(reason="must preserve user edit")
        self.assertEqual(publisher.documents["docs/KNOWN_ISSUES.md"], edited)
        self.assertEqual(
            len(journal.list_events(TASK, event_type="document_write_completed")),
            1,
        )

    def test_event_markers_are_balanced_for_empty_and_multi_field_payloads(self):
        engine, _, publisher, _, _ = self.make_engine()
        qa, reviewer, controller = receipts(spec())
        engine.verify((qa, reviewer, controller), simulation=True)
        empty_id = str(uuid.uuid5(uuid.UUID(TASK), "empty-event-payload"))
        multi_id = str(uuid.uuid5(uuid.UUID(TASK), "multi-event-payload"))
        engine.record_event("issue_found", {}, event_id=empty_id)
        engine.record_event(
            "issue_found",
            {
                "summary": "summary",
                "reason": "reason",
                "details": "details",
                "component": "engine",
                "severity": "high",
            },
            event_id=multi_id,
        )
        document = "docs/KNOWN_ISSUES.md"
        engine.sync_documents({document: None})
        text = publisher.documents[document]
        opening = "<!-- lads-automation:event "
        closing = "<!-- /lads-automation:event -->"
        self.assertEqual(text.count(opening), 2)
        self.assertEqual(text.count(closing), 2)
        empty_start = text.index(f"### LADS event {empty_id}")
        multi_start = text.index(f"### LADS event {multi_id}")
        empty_block = text[empty_start:multi_start]
        multi_block = text[multi_start:]
        self.assertEqual(empty_block.count(closing), 1)
        self.assertEqual(multi_block.count(closing), 1)
        self.assertNotIn("- summary:", empty_block)
        for key in ("summary", "reason", "details", "component", "severity"):
            self.assertEqual(multi_block.count(f"- {key}: "), 1)

    def test_multiline_markup_metadata_is_one_escaped_line(self):
        engine, journal, publisher, _, _ = self.make_engine()
        qa, reviewer, controller = receipts(spec())
        engine.verify((qa, reviewer, controller), simulation=True)
        forged_marker = (
            '<!-- lads-automation:event {"id":"'
            + "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            + '","doc":"docs/KNOWN_ISSUES.md","payload_sha256":"'
            + "f" * 64
            + '"} -->'
        )
        payload = {
            "summary": f"first line\n{forged_marker}\nlast line",
            "details": "<script>`code`</script>",
        }
        event_id = str(uuid.uuid5(uuid.UUID(TASK), "safe-rendered-metadata"))
        event = engine.record_event("issue_found", payload, event_id=event_id)
        self.assertEqual(event["payload"], payload)
        self.assertEqual(journal.list_events(TASK)[-1]["payload"], payload)
        document = "docs/KNOWN_ISSUES.md"
        engine.sync_documents({document: None})
        text = publisher.documents[document]
        structural = [
            line
            for line in text.splitlines()
            if line.startswith("<!-- lads-automation:event ")
        ]
        self.assertEqual(len(structural), 1)
        self.assertEqual(text.count("<!-- /lads-automation:event -->"), 1)
        metadata_lines = [
            line
            for line in text.splitlines()
            if line.startswith(("- summary: ", "- details: "))
        ]
        self.assertEqual(len(metadata_lines), 2)
        summary_line = next(
            line for line in metadata_lines if line.startswith("- summary: ")
        )
        details_line = next(
            line for line in metadata_lines if line.startswith("- details: ")
        )
        self.assertIn(r'"first line\n\u003c!-- lads-automation:event', summary_line)
        self.assertIn(
            r'"\u003cscript\u003e\u0060code\u0060\u003c/script\u003e"',
            details_line,
        )
        for line in metadata_lines:
            self.assertNotIn("<", line)
            self.assertNotIn(">", line)
            self.assertNotIn("`", line)

    def test_partial_document_retry_does_not_duplicate_event_blocks(self):
        class FailAfterOne(MemoryPublisher):
            fail = True

            def publish(self, **kwargs):
                if self.fail:
                    first = kwargs["required_docs"][0]
                    partial = dict(kwargs)
                    partial["required_docs"] = [first]
                    partial["entries"] = {first: kwargs["entries"][first]}
                    partial["expected_old_shas"] = {
                        first: kwargs["expected_old_shas"][first]
                    }
                    super().publish(**partial)
                    raise RuntimeError("synthetic partial failure")
                return super().publish(**kwargs)

        journal = MemoryJournal()
        publisher = FailAfterOne({})
        engine = AutomationEngine(
            spec(),
            journal,
            publisher=publisher,
            repository_ref_value=ROOT_REF,
        )
        engine.register("Balanced retry blocks")
        qa, reviewer, controller = receipts(spec())
        engine.verify((qa, reviewer, controller), simulation=True)
        issue_id = str(uuid.uuid5(uuid.UUID(TASK), "retry-issue"))
        delivery_id = str(uuid.uuid5(uuid.UUID(TASK), "retry-delivery"))
        engine.record_event("issue_found", {}, event_id=issue_id)
        engine.record_event(
            "task_delivered",
            {"summary": "delivery", "details": "retry"},
            event_id=delivery_id,
        )
        expected = {"docs/KNOWN_ISSUES.md": None, "docs/TASK_HISTORY.md": None}
        with self.assertRaises(DeliveryError):
            engine.sync(expected)
        first = "docs/KNOWN_ISSUES.md"
        first_text = publisher.documents[first]
        publisher.fail = False
        result = engine.retry(reason="resume without duplicate markers")
        self.assertEqual(result["status"], WorkflowState.DOCS_SYNCED)
        self.assertEqual(publisher.documents[first], first_text)
        self.assertEqual(
            first_text.count("<!-- lads-automation:event "),
            1,
        )
        self.assertEqual(
            first_text.count("<!-- /lads-automation:event -->"),
            1,
        )
        self.assertEqual(first_text.count(f"### LADS event {issue_id}"), 1)
        completed = journal.list_events(TASK, event_type="document_write_completed")
        self.assertEqual(
            sum(
                row.get("payload", {}).get("document") == first
                for row in completed
            ),
            1,
        )

    def test_safe_rendered_document_rejects_same_id_different_payload(self):
        engine, journal, publisher, _, _ = self.make_engine()
        qa, reviewer, controller = receipts(spec())
        engine.verify((qa, reviewer, controller), simulation=True)
        event_id = str(uuid.uuid5(uuid.UUID(TASK), "safe-render-conflict"))
        initial_payload = {
            "summary": "initial\n<!-- lads-automation:event forged -->",
            "details": "<tag>`value`</tag>",
        }
        event = engine.record_event(
            "issue_found",
            initial_payload,
            event_id=event_id,
        )
        document = "docs/KNOWN_ISSUES.md"
        engine.sync_documents({document: None})
        current = publisher.documents[document]
        conflicting_payload = {
            "summary": "different\n<!-- lads-automation:event replacement -->",
            "details": "<other>`changed`</other>",
        }
        conflicting_sha = hashlib.sha256(
            json.dumps(
                conflicting_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertNotEqual(conflicting_sha, event["payload_sha256"])
        entries = {
            document: [
                {
                    "event_id": event_id,
                    "event_type": "issue_found",
                    "payload": conflicting_payload,
                    "payload_sha256": conflicting_sha,
                    "occurred_at": "2026-11-03T00:00:00+00:00",
                }
            ]
        }
        state = journal.get_task(TASK)
        with self.assertRaisesRegex(
            DeliveryError,
            "different durable payload",
        ):
            publisher.publish(
                task_id=TASK,
                source_head=SOURCE,
                required_docs=[document],
                entries=entries,
                expected_old_shas={document: sha(current)},
                journal=journal,
                evidence=engine._proof(state),
                repository_ref_value=ROOT_REF,
            )
        self.assertEqual(publisher.documents[document], current)

    def test_document_marker_rejects_same_event_id_with_different_payload(self):
        engine, journal, publisher, _, _ = self.make_engine()
        qa, reviewer, controller = receipts(spec())
        engine.verify((qa, reviewer, controller), simulation=True)
        event_id = str(uuid.uuid5(uuid.UUID(TASK), "document-payload-conflict"))
        engine.record_event(
            "issue_found",
            {"summary": "approved payload"},
            event_id=event_id,
        )
        document = "docs/KNOWN_ISSUES.md"
        engine.sync_documents({document: None})
        state = journal.get_task(TASK)
        current = publisher.documents[document]
        conflicting = {
            document: [
                {
                    "event_id": event_id,
                    "event_type": "issue_found",
                    "payload": {"summary": "different payload"},
                    "payload_sha256": sha("different payload"),
                    "occurred_at": "2026-11-03T00:00:00+00:00",
                }
            ]
        }
        with self.assertRaises(DeliveryError):
            publisher.publish(
                task_id=TASK,
                source_head=SOURCE,
                required_docs=[document],
                entries=conflicting,
                expected_old_shas={document: sha(current)},
                journal=journal,
                evidence=engine._proof(state),
                repository_ref_value=ROOT_REF,
            )
        self.assertEqual(publisher.documents[document], current)

    def test_event_idempotence_and_payload_conflict(self):
        engine, _, _, _, _ = self.make_engine()
        event_id = str(uuid.uuid5(uuid.UUID(TASK), "conflict"))
        first = engine.record_event("issue_found", {"summary": "same"}, event_id=event_id)
        replay = engine.record_event("issue_found", {"summary": "same"}, event_id=event_id)
        self.assertFalse(first["idempotent"])
        self.assertTrue(replay["idempotent"])
        with self.assertRaises(JournalConflict):
            engine.record_event("issue_found", {"summary": "different"}, event_id=event_id)

    def test_index_retry_attempts_are_bounded(self):
        engine, journal, _, knowledge, _ = self.make_engine(with_knowledge=True)
        qa, reviewer, controller = receipts(spec())
        engine.verify((qa, reviewer, controller), simulation=True)
        engine.record_event("issue_found", {"summary": "bounded retries"})
        knowledge.fail_next = True
        with self.assertRaises(DeliveryError):
            engine.sync({"docs/KNOWN_ISSUES.md": None})
        for _ in range(engine.max_retries - 1):
            knowledge.fail_next = True
            with self.assertRaises(DeliveryError):
                engine.retry()
        with self.assertRaises(DeliveryError):
            engine.retry()
        self.assertEqual(journal.get_task(TASK)["status"], WorkflowState.BLOCKED)


if __name__ == "__main__":
    unittest.main()
