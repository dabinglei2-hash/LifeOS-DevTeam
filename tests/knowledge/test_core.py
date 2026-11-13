"""Phase A core tests. All cases use in-memory stores and static data."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import unittest

from sqlalchemy.exc import IntegrityError

from lads_knowledge import (
    ConflictError,
    KnowledgeError,
    KnowledgeStore,
    NotFoundError,
    SensitiveReferenceError,
    normalize_source_ref,
    sanitize_metadata,
)
from lads_knowledge.backup import MemoryBackup
from lads_knowledge.database import (
    HEAD_REVISION,
    SchemaValidationError,
    downgrade_connection,
    upgrade_connection,
    validate_schema,
)
from lads_knowledge.safety import canonical_json


PROJECT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER_PROJECT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
TASK_ONE = "11111111-1111-4111-8111-111111111111"
TASK_TWO = "22222222-2222-4222-8222-222222222222"
TASK_THREE = "33333333-3333-4333-8333-333333333333"
OTHER_TASK = "44444444-4444-4444-8444-444444444444"
RUN_ONE = "55555555-5555-4555-8555-555555555555"
EVENT_ONE = "66666666-6666-4666-8666-666666666666"
TEST_ONE = "77777777-7777-4777-8777-777777777777"
CHANGE_ONE = "88888888-8888-4888-8888-888888888888"
USAGE_ONE = "99999999-9999-4999-8999-999999999999"
OTHER_RUN = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"


class KnowledgeCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = KnowledgeStore.memory()
        self.project = self.store.register_project("Life OS", project_id=PROJECT_ID)
        for task_id in (TASK_ONE, TASK_TWO, TASK_THREE):
            self.store.create_task(
                PROJECT_ID,
                f"Requirement for {task_id}",
                task_id=task_id,
            )
            self.store.transition_task(PROJECT_ID, task_id, "active")

    def tearDown(self) -> None:
        self.store.close()

    def _row(self, sql: str, params: dict[str, object] | None = None) -> dict:
        with self.store._engine.connect() as connection:
            result = connection.exec_driver_sql(sql, params or ())
            if result.returns_rows:
                return dict(result.mappings().first())
            raise AssertionError("Query returned no rows")

    def _count(self, table: str) -> int:
        with self.store._engine.connect() as connection:
            return int(
                connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar_one()
            )

    def test_ten_table_relationships_and_foreign_keys(self) -> None:
        self.store.record_event(
            PROJECT_ID,
            TASK_ONE,
            "note",
            {"phase": "build"},
            event_id=EVENT_ONE,
        )
        self.store.record_run(
            PROJECT_ID,
            TASK_ONE,
            role="builder",
            model_name="deepseek-flash",
            build_number=1,
            attempt=1,
            status="completed",
            run_id=RUN_ONE,
        )
        self.store.record_test(
            PROJECT_ID,
            TASK_ONE,
            run_id=RUN_ONE,
            test_name="core",
            command="python -B -m unittest",
            status="passed",
        )
        self.store.record_change(
            PROJECT_ID,
            TASK_ONE,
            run_id=RUN_ONE,
            file_path="lads_knowledge/core.py",
            change_type="modified",
        )
        self.store.upsert_issue(
            PROJECT_ID,
            source_ref="docs/issue.md",
            title="Known limitation",
            details="File API is covered separately.",
            severity="low",
        )
        self.store.upsert_decision(
            PROJECT_ID,
            source_ref="docs/adr.md",
            title="Use SQLite",
            decision_text="Store engineering metadata in an isolated database.",
            status="accepted",
        )
        self.store.upsert_knowledge(
            PROJECT_ID,
            kind="doc",
            source_ref="docs/core.md",
            title="Core",
            body="alpha knowledge",
        )
        self.store.record_usage(
            PROJECT_ID,
            TASK_ONE,
            RUN_ONE,
            model_name="deepseek-flash",
        )

        for table in (
            "projects",
            "dev_tasks",
            "task_events",
            "agent_runs",
            "code_changes",
            "test_runs",
            "architecture_decisions",
            "known_issues",
            "knowledge_items",
            "model_usage",
        ):
            self.assertGreater(self._count(table), 0, table)
        with self.store._engine.connect() as connection:
            self.assertEqual(validate_schema(connection), HEAD_REVISION)
            self.assertEqual(
                list(connection.exec_driver_sql("PRAGMA foreign_key_check")), []
            )

    def test_cross_project_children_and_reader_are_refused(self) -> None:
        self.store.register_project("Other", project_id=OTHER_PROJECT_ID)
        self.store.create_task(
            OTHER_PROJECT_ID, "Other task", task_id=OTHER_TASK
        )
        self.store.record_run(
            PROJECT_ID,
            TASK_ONE,
            role="builder",
            model_name="deepseek-flash",
            build_number=1,
            attempt=1,
            status="completed",
            run_id=RUN_ONE,
        )
        self.store.record_test(
            PROJECT_ID,
            TASK_ONE,
            test_name="original",
            command="python -B -m unittest",
            status="passed",
            run_id=RUN_ONE,
            test_id=TEST_ONE,
        )
        self.store.record_change(
            PROJECT_ID,
            TASK_ONE,
            file_path="docs/original.md",
            change_type="modified",
            run_id=RUN_ONE,
            change_id=CHANGE_ONE,
        )
        self.store.record_usage(
            PROJECT_ID,
            TASK_ONE,
            RUN_ONE,
            model_name="deepseek-flash",
            usage_id=USAGE_ONE,
        )
        self.store.record_run(
            OTHER_PROJECT_ID,
            OTHER_TASK,
            role="builder",
            model_name="deepseek-flash",
            build_number=1,
            attempt=1,
            status="completed",
            run_id=OTHER_RUN,
        )
        with self.assertRaises(NotFoundError):
            self.store.record_event(
                PROJECT_ID, OTHER_TASK, "note", {"value": "cross-project"}
            )
        with self.assertRaises(NotFoundError):
            self.store.upsert_knowledge(
                PROJECT_ID,
                kind="doc",
                source_ref="docs/cross.md",
                title="Cross",
                body="Cross project task should fail.",
                task_id=OTHER_TASK,
            )
        reader = self.store.reader(PROJECT_ID)
        with self.assertRaises(NotFoundError):
            reader.get_task_history(OTHER_TASK)
        with self.assertRaises(NotFoundError):
            self.store.reader(OTHER_PROJECT_ID).get_task_history(TASK_ONE)
        with self.assertRaises(ConflictError):
            self.store.record_test(
                OTHER_PROJECT_ID,
                OTHER_TASK,
                test_name="conflicting",
                command="other command",
                status="failed",
                run_id=OTHER_RUN,
                test_id=TEST_ONE,
            )
        with self.assertRaises(ConflictError):
            self.store.record_change(
                OTHER_PROJECT_ID,
                OTHER_TASK,
                file_path="docs/conflicting.md",
                change_type="added",
                run_id=OTHER_RUN,
                change_id=CHANGE_ONE,
            )
        with self.assertRaises(ConflictError):
            self.store.record_usage(
                OTHER_PROJECT_ID,
                OTHER_TASK,
                OTHER_RUN,
                model_name="conflicting-model",
                usage_id=USAGE_ONE,
            )
        self.assertEqual(self._count("test_runs"), 1)
        self.assertEqual(self._count("code_changes"), 1)
        self.assertEqual(self._count("model_usage"), 1)

    def test_event_idempotency_conflict_and_rollback(self) -> None:
        first = self.store.record_event(
            PROJECT_ID,
            TASK_ONE,
            "build_note",
            {"step": 1, "ok": True},
            event_id=EVENT_ONE,
        )
        second = self.store.record_event(
            PROJECT_ID,
            TASK_ONE,
            "build_note",
            {"ok": True, "step": 1},
            event_id=EVENT_ONE,
        )
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(self._count("task_events"), 4)
        with self.assertRaises(ConflictError):
            self.store.record_event(
                PROJECT_ID,
                TASK_ONE,
                "build_note",
                {"step": 2},
                event_id=EVENT_ONE,
            )
        self.assertEqual(self._count("task_events"), 4)

    def test_state_requires_valid_explicit_transition(self) -> None:
        with self.assertRaises(ConflictError):
            self.store.transition_task(PROJECT_ID, TASK_ONE, "planned")
        task = self._row(
            "SELECT * FROM dev_tasks WHERE project_id=? AND id=?",
            (PROJECT_ID, TASK_ONE),
        )
        self.assertEqual(task["state"], "active")
        updated = self.store.update_task(
            PROJECT_ID, TASK_ONE, assigned_agent="builder"
        )
        self.assertEqual(updated["state"], "active")
        transitioned = self.store.transition_task(
            PROJECT_ID, TASK_ONE, "testing", event_id=EVENT_ONE
        )
        self.assertEqual(transitioned["state"], "testing")
        replayed = self.store.transition_task(
            PROJECT_ID, TASK_ONE, "testing", event_id=EVENT_ONE
        )
        self.assertEqual(replayed["state"], "testing")
        self.assertEqual(self._count("task_events"), 4)
        with self.assertRaises(ConflictError):
            self.store.transition_task(
                PROJECT_ID,
                TASK_ONE,
                "cancelled",
                event_id=EVENT_ONE,
                event_payload={"to_state": "cancelled"},
            )
        self.assertEqual(
            self._row(
                "SELECT state FROM dev_tasks WHERE project_id=? AND id=?",
                (PROJECT_ID, TASK_ONE),
            )["state"],
            "testing",
        )

    def test_transition_custom_payload_uses_canonical_envelope_identity(self) -> None:
        custom_payload = {"reason": "review requested"}
        first = self.store.transition_task(
            PROJECT_ID,
            TASK_ONE,
            "testing",
            event_id=EVENT_ONE,
            event_payload=custom_payload,
        )
        self.assertEqual(first["state"], "testing")
        event = self._row(
            "SELECT payload_json FROM task_events WHERE id=?", (EVENT_ONE,)
        )
        self.assertEqual(
            event["payload_json"],
            canonical_json({"to_state": "testing", "details": custom_payload}),
        )
        replayed = self.store.transition_task(
            PROJECT_ID,
            TASK_ONE,
            "testing",
            event_id=EVENT_ONE,
            event_payload=custom_payload,
        )
        self.assertEqual(replayed["state"], "testing")
        self.assertEqual(self._count("task_events"), 4)
        with self.assertRaises(ConflictError):
            self.store.transition_task(
                PROJECT_ID,
                TASK_ONE,
                "testing",
                event_id=EVENT_ONE,
                event_payload={"reason": "different payload"},
            )
        with self.assertRaises(ConflictError):
            self.store.transition_task(
                PROJECT_ID,
                TASK_ONE,
                "cancelled",
                event_id=EVENT_ONE,
                event_payload=custom_payload,
            )
        self.assertEqual(self._count("task_events"), 4)
        self.assertEqual(
            self._row(
                "SELECT state FROM dev_tasks WHERE project_id=? AND id=?",
                (PROJECT_ID, TASK_ONE),
            )["state"],
            "testing",
        )

    def test_run_unique_idempotency_and_usage_with_unknown_tokens(self) -> None:
        first = self.store.record_run(
            PROJECT_ID,
            TASK_ONE,
            role="builder",
            model_name="deepseek-flash",
            build_number=1,
            attempt=1,
            status="completed",
            run_id=RUN_ONE,
        )
        second = self.store.record_run(
            PROJECT_ID,
            TASK_ONE,
            role="builder",
            model_name="deepseek-flash",
            build_number=1,
            attempt=1,
            status="completed",
        )
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        with self.assertRaises(ConflictError):
            self.store.record_run(
                PROJECT_ID,
                TASK_ONE,
                role="builder",
                model_name="different-model",
                build_number=1,
                attempt=1,
                status="completed",
            )
        usage = self.store.record_usage(
            PROJECT_ID,
            TASK_ONE,
            RUN_ONE,
            model_name="deepseek-flash",
        )
        self.assertIsNone(usage["input_tokens"])
        self.assertIsNone(usage["output_tokens"])
        self.assertIsNone(usage["cost_usd"])
        with self.assertRaises(IntegrityError):
            with self.store._engine.connect() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO model_usage "
                    "(id, project_id, task_id, run_id, model_name, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "77777777-7777-4777-8777-777777777777",
                        PROJECT_ID,
                        TASK_ONE,
                        RUN_ONE,
                        "deepseek-flash",
                        "2026-11-03T00:00:00.000000Z",
                    ),
                )
                connection.commit()

    def test_source_path_variants_share_one_stable_identity(self) -> None:
        first = self.store.upsert_knowledge(
            PROJECT_ID,
            kind="doc",
            source_ref="Docs\\Guide.md",
            title="Guide",
            body="alpha first",
        )
        second = self.store.upsert_knowledge(
            PROJECT_ID,
            kind="doc",
            source_ref="./docs/GUIDE.md",
            title="Guide updated",
            body="alpha second",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["source_ref"], "docs/guide.md")
        with self.assertRaises(KnowledgeError):
            self.store.upsert_knowledge(
                PROJECT_ID,
                kind="doc",
                source_ref="docs/%47UIDE.md",
                title="Guide encoded",
                body="alpha third",
            )
        self.assertEqual(self._count("knowledge_items"), 1)
        self.assertEqual(normalize_source_ref("path", "Docs\\B.md"), "docs/b.md")
        with self.assertRaises(KnowledgeError):
            normalize_source_ref("path", "../private.md")
        with self.assertRaises(KnowledgeError):
            normalize_source_ref("path", "C:\\private.md")
        self.assertEqual(
            normalize_source_ref(
                "sha", "ABCDEF0123456789ABCDEF0123456789ABCDEF01"
            ),
            "abcdef0123456789abcdef0123456789abcdef01",
        )
        self.assertEqual(
            normalize_source_ref("url", "HTTPS://Example.COM:443/guide"),
            "https://example.com/guide",
        )
        self.assertEqual(
            normalize_source_ref("url", "https://example.com/%7Eguide"),
            "https://example.com/~guide",
        )
        with self.assertRaises(KnowledgeError):
            normalize_source_ref("url", "https://example.com/docs/../guide")
        with self.assertRaises(KnowledgeError):
            normalize_source_ref("url", "https://example.com/%2e%2e/guide")
        for value in (
            "https://example.com/%FF",
            "https://example.com/%FE",
            "https://example.com/%ZZ",
            "https://example.com/%25ZZ",
            "https://example.com/encoded%2Fseparator",
            "https://example.com/encoded%5Cseparator",
            "https://user%40example.com/private",
        ):
            with self.subTest(value=value), self.assertRaises(KnowledgeError):
                normalize_source_ref("url", value)
        with self.assertRaises(KnowledgeError):
            normalize_source_ref("url", "ssh://example.com/repo")
        with self.assertRaises(SensitiveReferenceError):
            normalize_source_ref("url", "https://user:pass@example.com/repo")
        with self.assertRaises(SensitiveReferenceError):
            normalize_source_ref("url", "ssh://user:pass@localhost/repo")
        with self.assertRaises(KnowledgeError):
            normalize_source_ref("path", "git+ssh://example.com/repo")
        with self.assertRaises(KnowledgeError):
            normalize_source_ref("path", "docs/control\x00name.md")
        for value in (
            "docs%2Fsecret.md",
            "docs%5Csecret.md",
            "docs/%252Fsecret.md",
            "docs/%255Csecret.md",
            "docs/%2e%2e/secret.md",
            "docs/%2E%2E/secret.md",
            "docs/%2e%2e%2fsecret.md",
            "docs/%252e%252e/secret.md",
            "docs/%5c..%5csecret.md",
            "docs/%00hidden.md",
            "docs/%FF.md",
            "docs/%FE.md",
            "docs/%ZZ.md",
            "docs/%25ZZ.md",
        ):
            with self.subTest(value=value), self.assertRaises(KnowledgeError):
                normalize_source_ref("path", value)

    def test_source_ref_privacy_is_rejected_not_redacted(self) -> None:
        for value in (
            "docs/alice@example.com.md",
            "docs/+1 202 555 0100.md",
            "docs/202-555-0100.md",
            "docs/token=abcdef123456.md",
            "https://alice@example.com/private",
            "https://example.com/doc?token=abc123456",
        ):
            with self.subTest(value=value), self.assertRaises(SensitiveReferenceError):
                normalize_source_ref(
                    "url" if value.startswith(("http://", "https://")) else "path",
                    value,
                )
        with self.assertRaises(KnowledgeError):
            normalize_source_ref("path", "docs/alice%40example.com.md")

    def test_display_text_sanitized_but_identity_remains_source_ref(self) -> None:
        item = self.store.upsert_knowledge(
            PROJECT_ID,
            kind="doc",
            source_ref="docs/privacy.md",
            title="Contact alice@example.com",
            body=(
                "Bearer abcdefghijklmnop and +1 202 555 0100 "
                "and api_key=supersecret123"
            ),
        )
        self.assertNotIn("alice@example.com", item["body"])
        self.assertNotIn("abcdefghijklmnop", item["body"])
        self.assertNotIn("+1 202 555 0100", item["body"])
        self.assertTrue(item["sanitized_text_sha"])
        self.assertEqual(item["source_ref"], "docs/privacy.md")
        with self.assertRaises(KnowledgeError):
            sanitize_metadata({"messages": [{"content": "raw dialogue"}]})

    def test_knowledge_update_tombstone_reactivate_and_fts(self) -> None:
        item = self.store.upsert_knowledge(
            PROJECT_ID,
            kind="doc",
            source_ref="docs/search.md",
            title="Engineering",
            body="alpha trilateral search",
            task_id=TASK_THREE,
        )
        updated = self.store.upsert_knowledge(
            PROJECT_ID,
            kind="doc",
            source_ref="docs\\search.md",
            title="Engineering updated",
            body="alpha updated payload",
            task_id=TASK_THREE,
        )
        self.assertEqual(item["id"], updated["id"])
        reader = self.store.reader(PROJECT_ID)
        self.assertEqual(reader.search_project_knowledge("alpha")["count"], 1)
        tombstoned = self.store.tombstone_knowledge(
            PROJECT_ID, kind="doc", source_ref="./docs/SEARCH.md"
        )
        self.assertEqual(tombstoned["id"], item["id"])
        self.assertEqual(reader.search_project_knowledge("alpha")["count"], 0)
        reactivated = self.store.upsert_knowledge(
            PROJECT_ID,
            kind="doc",
            source_ref="docs/search.md",
            title="Engineering restored",
            body="alpha restored payload",
            task_id=TASK_THREE,
        )
        self.assertEqual(reactivated["id"], item["id"])
        self.assertEqual(reader.search_project_knowledge("alpha")["count"], 1)

    def test_three_tasks_cover_history_failed_test_and_search_update(self) -> None:
        self.store.record_event(
            PROJECT_ID, TASK_ONE, "started", {"task": "history"}
        )
        self.store.record_run(
            PROJECT_ID,
            TASK_ONE,
            role="builder",
            model_name="deepseek-flash",
            build_number=1,
            attempt=1,
            status="completed",
            run_id=RUN_ONE,
        )
        self.store.record_test(
            PROJECT_ID,
            TASK_TWO,
            test_name="integration",
            command="python -B -m unittest",
            status="failed",
            summary="Synthetic assertion failed",
        )
        self.store.upsert_knowledge(
            PROJECT_ID,
            kind="doc",
            source_ref="docs/phase-a.md",
            title="Phase A",
            body="triblock searchable knowledge",
            task_id=TASK_THREE,
        )
        reader = self.store.reader(PROJECT_ID)
        history = reader.get_task_history(TASK_ONE, limit=3)
        self.assertEqual(history["items"][0]["task_id"], TASK_ONE)
        self.assertGreaterEqual(len(history["items"][0]["events"]), 1)
        self.assertGreaterEqual(len(history["items"][0]["runs"]), 1)
        failed = reader.get_task_history(TASK_TWO, limit=2)
        self.assertEqual(failed["items"][0]["tests"][0]["status"], "failed")
        search = reader.search_project_knowledge("triblock", limit=5)
        self.assertEqual(search["count"], 1)
        self.assertEqual(search["items"][0]["task_id"], TASK_THREE)

    def test_fts_like_wildcards_injection_and_snippets(self) -> None:
        self.store.upsert_knowledge(
            PROJECT_ID,
            kind="doc",
            source_ref="docs/fts.md",
            title="工程说明",
            body="工程 trigram content " + ("x" * 900),
        )
        reader = self.store.reader(PROJECT_ID)
        fts = reader.search_project_knowledge("tri")
        self.assertEqual(fts["mode"], "fts5-trigram")
        self.assertEqual(fts["count"], 1)
        self.assertLessEqual(len(fts["items"][0]["snippet"]), 600)
        chinese = reader.search_project_knowledge("工程")
        self.assertEqual(chinese["mode"], "like-fallback")
        self.assertEqual(chinese["count"], 1)
        wildcard = reader.search_project_knowledge("%_%")
        self.assertEqual(wildcard["count"], 0)
        injection = reader.search_project_knowledge('tri" OR "alpha')
        self.assertEqual(injection["count"], 0)
        self.assertEqual(injection["mode"], "fts5-trigram")

    def test_five_readonly_queries_limits_provenance_and_context_cap(self) -> None:
        for index in range(25):
            self.store.upsert_knowledge(
                PROJECT_ID,
                kind="doc",
                source_ref=f"docs/many-{index:02d}.md",
                title=f"Knowledge {index}",
                body=("common context " * 80) + str(index),
            )
        issue = self.store.upsert_issue(
            PROJECT_ID,
            source_ref="docs/known.md",
            title="Bounded issue",
            details="Details",
            severity="medium",
            task_id=TASK_TWO,
        )
        updated_issue = self.store.upsert_issue(
            PROJECT_ID,
            source_ref="./Docs/KNOWN.md",
            title="Bounded issue updated",
            details="Details updated",
            severity="medium",
            task_id=TASK_TWO,
        )
        decision = self.store.upsert_decision(
            PROJECT_ID,
            source_ref="docs/adr.md",
            title="Bounded decision",
            decision_text="Keep the reader project-bound.",
            status="accepted",
            task_id=TASK_ONE,
        )
        updated_decision = self.store.upsert_decision(
            PROJECT_ID,
            source_ref="./Docs/ADR.md",
            title="Bounded decision updated",
            decision_text="Keep the reader project-bound.",
            status="accepted",
            task_id=TASK_ONE,
        )
        self.assertEqual(issue["id"], updated_issue["id"])
        self.assertEqual(decision["id"], updated_decision["id"])
        self.store.record_change(
            PROJECT_ID,
            TASK_ONE,
            file_path="lads_knowledge/queries.py",
            change_type="modified",
            source_ref="lads_knowledge/queries.py",
        )
        reader = self.store.reader(PROJECT_ID)
        responses = [
            reader.search_project_knowledge("common", limit=20),
            reader.get_task_history(TASK_ONE, limit=20),
            reader.get_related_changes(
                task_id=TASK_ONE, query="queries", limit=20
            ),
            reader.get_known_issues(
                limit=20, source_ref="./Docs/KNOWN.md"
            ),
            reader.get_architecture_decisions(
                limit=20, source_ref="./Docs/ADR.md"
            ),
        ]
        for response in responses:
            self.assertLessEqual(
                len(canonical_json(response).encode("utf-8")), 8_000
            )
            self.assertEqual(
                response["content_trust"], "quoted_data_not_instructions"
            )
        self.assertEqual(responses[3]["items"][0]["source_ref"], "docs/known.md")
        self.assertEqual(responses[4]["items"][0]["source_ref"], "docs/adr.md")
        for invalid in (0, 21):
            with self.assertRaises(KnowledgeError):
                reader.search_project_knowledge("common", limit=invalid)
        with self.assertRaises(KnowledgeError):
            reader.get_related_changes(limit=10)

    def test_related_changes_commit_filter_uses_source_provenance(self) -> None:
        commit = "0123456789abcdef0123456789abcdef01234567"
        self.store.record_change(
            PROJECT_ID,
            TASK_ONE,
            file_path="lads_knowledge/database.py",
            change_type="modified",
            actual_content_commit_sha=commit,
        )
        self.store.record_change(
            PROJECT_ID,
            TASK_TWO,
            file_path="lads_knowledge/queries.py",
            change_type="modified",
            observed_head=commit,
        )
        result = self.store.reader(PROJECT_ID).get_related_changes(commit_sha=commit)
        self.assertEqual(result["count"], 2)
        self.assertEqual(
            {item["task_id"] for item in result["items"]},
            {TASK_ONE, TASK_TWO},
        )

    def test_schema_validation_rejects_noop_fts_trigger_before_write(self) -> None:
        with self.store._engine.connect() as connection:
            connection.exec_driver_sql("DROP TRIGGER knowledge_fts_ai")
            connection.exec_driver_sql(
                "CREATE TRIGGER knowledge_fts_ai "
                "AFTER INSERT ON knowledge_items BEGIN SELECT 1; END"
            )
            connection.commit()
        with self.assertRaises(SchemaValidationError):
            self.store.upsert_knowledge(
                PROJECT_ID,
                kind="doc",
                source_ref="docs/blocked.md",
                title="Blocked",
                body="must not reach fts",
            )
        self.assertEqual(self._count("knowledge_items"), 0)

    def test_schema_rejects_same_name_altered_check_before_write_backup_restore(
        self,
    ) -> None:
        valid_backup = self.store.backup()

        def altered_check_sql(original: str) -> str:
            altered = original.replace(
                "'cancelled'))",
                "'cancelled','same_name_altered'))",
                1,
            )
            self.assertNotEqual(altered, original)
            self.assertIn("ck_dev_tasks_state", altered)
            return altered

        with self.store._engine.connect() as connection:
            original = str(
                connection.exec_driver_sql(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='dev_tasks'"
                ).scalar_one()
            )
            connection.exec_driver_sql("PRAGMA writable_schema=ON")
            connection.exec_driver_sql(
                "UPDATE sqlite_master SET sql=? "
                "WHERE type='table' AND name='dev_tasks'",
                (altered_check_sql(original),),
            )
            schema_version = connection.exec_driver_sql(
                "PRAGMA schema_version"
            ).scalar_one()
            connection.exec_driver_sql("PRAGMA writable_schema=OFF")
            connection.exec_driver_sql(f"PRAGMA schema_version={schema_version + 1}")
            connection.commit()

        with self.store._engine.connect() as connection:
            with self.assertRaises(SchemaValidationError):
                validate_schema(connection)
        with self.assertRaises(SchemaValidationError):
            self.store.register_project(
                "Must not be written",
                project_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            )
        self.assertEqual(self._count("projects"), 1)
        with self.assertRaises(SchemaValidationError):
            self.store.backup()

        tampered = sqlite3.connect(":memory:")
        try:
            tampered.deserialize(valid_backup.payload)
            original = str(
                tampered.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='dev_tasks'"
                ).fetchone()[0]
            )
            tampered.execute("PRAGMA writable_schema=ON")
            tampered.execute(
                "UPDATE sqlite_master SET sql=? "
                "WHERE type='table' AND name='dev_tasks'",
                (altered_check_sql(original),),
            )
            schema_version = tampered.execute("PRAGMA schema_version").fetchone()[0]
            tampered.execute("PRAGMA writable_schema=OFF")
            tampered.execute(f"PRAGMA schema_version={schema_version + 1}")
            tampered.commit()
            tampered_payload = tampered.serialize()
        finally:
            tampered.close()
        manifest = {
            **valid_backup.manifest,
            "sha256": hashlib.sha256(tampered_payload).hexdigest(),
            "size": len(tampered_payload),
        }
        corrupt_backup = MemoryBackup(tampered_payload, manifest)
        with self.assertRaises(SchemaValidationError):
            corrupt_backup.validate()
        with self.assertRaises(SchemaValidationError):
            corrupt_backup.restore()

    def test_fts_multiset_detects_same_count_id_body_and_duplicate_tamper(
        self,
    ) -> None:
        first = self.store.upsert_knowledge(
            PROJECT_ID,
            kind="doc",
            source_ref="docs/one.md",
            title="One",
            body="first indexed body",
        )
        second = self.store.upsert_knowledge(
            PROJECT_ID,
            kind="doc",
            source_ref="docs/two.md",
            title="Two",
            body="second indexed body",
        )
        self.assertNotEqual(first["id"], second["id"])

        def assert_tamper_is_rejected(
            sql: str, params: tuple[object, ...]
        ) -> None:
            with self.store._engine.connect() as connection:
                transaction = connection.begin()
                connection.exec_driver_sql(sql, params)
                with self.assertRaises(SchemaValidationError):
                    validate_schema(connection)
                transaction.rollback()

        assert_tamper_is_rejected(
            "UPDATE knowledge_fts SET knowledge_id=? WHERE knowledge_id=?",
            (OTHER_TASK, first["id"]),
        )
        assert_tamper_is_rejected(
            "UPDATE knowledge_fts SET body=? WHERE knowledge_id=?",
            ("stale indexed body", second["id"]),
        )
        assert_tamper_is_rejected(
            "INSERT INTO knowledge_fts "
            "(knowledge_id, project_id, title, body, source_ref) "
            "SELECT knowledge_id, project_id, title, body, source_ref "
            "FROM knowledge_fts WHERE knowledge_id=?",
            (first["id"],),
        )
        with self.store._engine.connect() as connection:
            self.assertEqual(validate_schema(connection), HEAD_REVISION)
            connection.exec_driver_sql("PRAGMA query_only=ON")
            self.assertEqual(validate_schema(connection), HEAD_REVISION)

    def test_memory_backup_restore_and_corruption_errors(self) -> None:
        self.store.upsert_knowledge(
            PROJECT_ID,
            kind="doc",
            source_ref="docs/snapshot.md",
            title="Snapshot",
            body="verified snapshot content",
        )
        backup = self.store.backup()
        self.assertIsInstance(backup, MemoryBackup)
        self.assertEqual(backup.validate()["schema_revision"], HEAD_REVISION)
        self.store.upsert_knowledge(
            PROJECT_ID,
            kind="doc",
            source_ref="docs/after.md",
            title="After",
            body="post snapshot",
        )
        restored = backup.restore()
        try:
            reader = restored.reader(PROJECT_ID)
            self.assertEqual(reader.search_project_knowledge("snapshot")["count"], 1)
            self.assertEqual(reader.search_project_knowledge("post")["count"], 0)
        finally:
            restored.close()
        corrupt = MemoryBackup(backup.payload[:-1], backup.manifest)
        with self.assertRaises(KnowledgeError):
            corrupt.validate()

    def test_migration_2_to_1_to_2_preserves_rows_and_rebuilds_fts(self) -> None:
        self.store.upsert_knowledge(
            PROJECT_ID,
            kind="doc",
            source_ref="docs/migrate.md",
            title="Migration",
            body="migratingsearch token",
        )
        with self.store._engine.connect() as connection:
            downgrade_connection(connection, "0001")
            connection.commit()
        with self.store._engine.connect() as connection:
            self.assertEqual(validate_schema(connection), "0001")
            self.assertEqual(
                connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM knowledge_items"
                ).scalar_one(),
                1,
            )
        with self.store._engine.connect() as connection:
            upgrade_connection(connection, "head")
            connection.commit()
        with self.store._engine.connect() as connection:
            self.assertEqual(validate_schema(connection), HEAD_REVISION)
        self.assertEqual(
            self.store.reader(PROJECT_ID).search_project_knowledge("migrating")[
                "count"
            ],
            1,
        )

    def test_concurrent_memory_writers_are_serialized(self) -> None:
        errors: list[BaseException] = []

        def write(index: int) -> None:
            try:
                self.store.record_event(
                    PROJECT_ID,
                    TASK_ONE,
                    "parallel",
                    {"index": index},
                )
            except BaseException as exc:  # Test captures every thread failure.
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(
            self._count("task_events"),
            3 + 8,
        )

    def test_database_constraints_are_named_and_enforced(self) -> None:
        with self.store._engine.connect() as connection:
            names = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertTrue(
            {
                "projects",
                "dev_tasks",
                "task_events",
                "agent_runs",
                "code_changes",
                "test_runs",
                "architecture_decisions",
                "known_issues",
                "knowledge_items",
                "model_usage",
            }.issubset(names)
        )

    def test_explicit_index_definition_change_blocks_next_write(self) -> None:
        with self.store._engine.connect() as connection:
            connection.exec_driver_sql("DROP INDEX ix_dev_tasks_project_state")
            connection.exec_driver_sql(
                "CREATE INDEX ix_dev_tasks_project_state "
                "ON dev_tasks(state, project_id)"
            )
            connection.commit()
        with self.store._engine.connect() as connection:
            with self.assertRaises(SchemaValidationError):
                validate_schema(connection)
        with self.assertRaises(SchemaValidationError):
            self.store.register_project(
                "Must not be written",
                project_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            )
        self.assertEqual(self._count("projects"), 1)
