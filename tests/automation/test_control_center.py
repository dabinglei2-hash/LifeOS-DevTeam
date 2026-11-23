"""Synthetic Stage 1 telemetry tests with the existing LADS Python runtime."""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from control_center.telemetry import SnapshotService, build_snapshot
from lads_knowledge.queries import KnowledgeReader
from lads_knowledge.service import KnowledgeStore

LIFEOS = "b64ec23c-dd38-528c-9827-d72127513caa"
DEVTEAM = "ebc8cefb-1f79-5193-bbfb-0ac069f9ee71"
SHANGHAI = timezone(timedelta(hours=8))


def identifier(index: int) -> str:
    return str(uuid.UUID(int=index))


class BadReader:
    _project_id = DEVTEAM

    def _rows(self, _sql: str, _params: dict | None = None) -> list[dict]:
        raise RuntimeError("PRIVATE-MARKER-DO-NOT-EXPOSE")


class ControlCenterTelemetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = KnowledgeStore.memory()
        self.addCleanup(self.store.close)
        self.store.register_project("synthetic:lifeos", project_id=LIFEOS)
        self.store.register_project("synthetic:devteam", project_id=DEVTEAM)

    def reader(self, project_id: str) -> KnowledgeReader:
        return KnowledgeReader.from_store(self.store, project_id)

    def sql(self, statement: str, params: dict | None = None) -> None:
        with self.store._write() as connection:
            connection.execute(text(statement), params or {})

    def insert_tasks(self, rows: list[dict]) -> None:
        with self.store._write() as connection:
            for row in rows:
                connection.execute(
                    text(
                        "INSERT INTO dev_tasks "
                        "(project_id,id,state,requirement_summary,assigned_agent,commit_sha,"
                        "pr_ref,test_result,review_result,error_reason,completed_time,"
                        "created_at,updated_at) "
                        "VALUES (:project_id,:id,:state,:summary,:assigned_agent,NULL,NULL,"
                        "NULL,NULL,NULL,:completed_time,:created_at,:updated_at)"
                    ),
                    row,
                )

    def table_counts(self) -> dict[str, int]:
        tables = (
            "projects",
            "dev_tasks",
            "agent_runs",
            "model_usage",
            "test_runs",
            "knowledge_items",
        )
        with self.store._engine.connect() as connection:
            return {
                table: int(connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar_one())
                for table in tables
            }

    def test_real_aggregation_project_isolation_usage_once_and_no_writes(self) -> None:
        now = datetime.now(UTC)
        lifeos_reader = self.reader(LIFEOS)
        devteam_reader = self.reader(DEVTEAM)
        lifeos_task = self.store.create_task(
            LIFEOS,
            "Ship synthetic Stage 1 overview",
            task_id=identifier(1),
            assigned_agent="builder",
        )
        self.store.transition_task(LIFEOS, lifeos_task["id"], "active")
        self.store.transition_task(LIFEOS, lifeos_task["id"], "completed")
        devtask = self.store.create_task(
            DEVTEAM,
            "Keep engineering baseline isolated",
            task_id=identifier(2),
            assigned_agent="reviewer",
        )
        self.store.transition_task(DEVTEAM, devtask["id"], "active")
        actor = self.store.record_run(
            LIFEOS,
            lifeos_task["id"],
            role="builder",
            model_name="deepseek-chat",
            build_number=1,
            attempt=1,
            status="completed",
            started_at=now.isoformat(),
            completed_at=(now + timedelta(seconds=2)).isoformat(),
            input_tokens=999,
            output_tokens=999,
            run_id=identifier(10),
        )
        self.store.record_usage(
            LIFEOS,
            lifeos_task["id"],
            actor["id"],
            model_name="deepseek-chat",
            input_tokens=120,
            output_tokens=30,
            cost_usd=9.99,
            usage_id=identifier(11),
        )
        reviewer = self.store.record_run(
            LIFEOS,
            lifeos_task["id"],
            role="reviewer",
            model_name="gpt-5-synthetic",
            build_number=1,
            attempt=1,
            status="completed",
            started_at=(now + timedelta(minutes=1)).isoformat(),
            run_id=identifier(12),
        )
        self.store.record_usage(
            LIFEOS,
            lifeos_task["id"],
            reviewer["id"],
            model_name="gpt-5-synthetic",
            input_tokens=None,
            output_tokens=8,
            usage_id=identifier(13),
        )
        for index, status in ((20, "passed"), (21, "failed"), (22, "skipped")):
            self.store.record_test(
                LIFEOS,
                lifeos_task["id"],
                test_name=f"synthetic-{status}",
                command="python -B synthetic.py",
                status=status,
                run_id=actor["id"],
                test_id=identifier(index),
                started_at=now.isoformat(),
            )
        self.store.record_test(
            DEVTEAM,
            devtask["id"],
            test_name="devteam-only",
            command="python -B devteam-synthetic.py",
            status="passed",
            test_id=identifier(23),
            started_at=now.isoformat(),
        )
        self.store.upsert_knowledge(
            LIFEOS,
            kind="document",
            source_ref="docs/synthetic.md",
            title="Synthetic document",
            body="Engineering-only fixture",
        )
        before = self.table_counts()

        combined = build_snapshot(
            {"lifeos": lifeos_reader, "devteam": devteam_reader},
            now,
        )
        lifeos_only = build_snapshot({"lifeos": lifeos_reader}, now)

        self.assertEqual("known", combined["data_status"])
        self.assertEqual(1, combined["metrics"]["active_tasks"])
        self.assertEqual(1, combined["metrics"]["completed_today"])
        self.assertEqual(2, combined["tokens"]["total_rows"])
        self.assertEqual(1, combined["tokens"]["input_known_rows"])
        self.assertEqual(2, combined["tokens"]["output_known_rows"])
        self.assertEqual(120, combined["tokens"]["input_tokens"])
        self.assertEqual(38, combined["tokens"]["output_tokens"])
        self.assertEqual("unknown", combined["tokens"]["cost"])
        self.assertEqual((2, 1, 1), (
            combined["qa"]["passed"],
            combined["qa"]["failed"],
            combined["qa"]["excluded"],
        ))
        self.assertEqual(66.7, combined["qa"]["pass_rate_percent"])
        self.assertEqual(1, combined["knowledge"]["active_count"])
        self.assertEqual("deepseek", next(a for a in combined["agents"] if a["id"] == "builder")["historical_provider"])
        self.assertEqual("gpt", next(a for a in combined["agents"] if a["id"] == "reviewer")["historical_provider"])

        self.assertEqual("lifeos", lifeos_only["project_filter"])
        self.assertEqual(120, lifeos_only["tokens"]["input_tokens"])
        self.assertEqual(38, lifeos_only["tokens"]["output_tokens"])
        self.assertEqual(1, lifeos_only["qa"]["passed"])
        self.assertEqual(1, lifeos_only["qa"]["failed"])
        self.assertEqual("lifeos", lifeos_only["recent_tasks"][0]["project"])
        self.assertNotIn("9.99", json.dumps(combined))
        self.assertEqual(before, self.table_counts())

    def test_null_usage_empty_qa_and_unknown_provider_are_not_zero(self) -> None:
        now = datetime.now(UTC)
        task = self.store.create_task(LIFEOS, "Null usage synthetic", task_id=identifier(30))
        for index, role, model, input_tokens, output_tokens in (
            (40, "scanner", "unknown-model", None, 2),
            (41, "reviewer", "deepseek-reasoner", None, None),
        ):
            run = self.store.record_run(
                LIFEOS,
                task["id"],
                role=role,
                model_name=model,
                build_number=1,
                attempt=1,
                status="completed",
                started_at=now.isoformat(),
                run_id=identifier(index + 100),
            )
            self.store.record_usage(
                LIFEOS,
                task["id"],
                run["id"],
                model_name=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                usage_id=identifier(index),
            )
        snapshot = build_snapshot({"lifeos": self.reader(LIFEOS)}, now)
        self.assertIsNone(snapshot["tokens"]["input_tokens"])
        self.assertEqual(2, snapshot["tokens"]["input_unknown_rows"])
        self.assertEqual(2, snapshot["tokens"]["output_tokens"])
        self.assertEqual(1, snapshot["tokens"]["output_unknown_rows"])
        self.assertIsNone(snapshot["qa"]["pass_rate_percent"])
        scanner = next(agent for agent in snapshot["agents"] if agent["id"] == "scanner")
        self.assertEqual("unknown", scanner["historical_provider"])

    def test_empty_database_is_known_zero_but_tokens_remain_unknown(self) -> None:
        snapshot = build_snapshot(
            {"lifeos": self.reader(LIFEOS)},
            datetime.now(UTC),
        )
        self.assertEqual("known", snapshot["data_status"])
        self.assertEqual(0, snapshot["metrics"]["active_tasks"])
        self.assertEqual(0, snapshot["metrics"]["completed_today"])
        self.assertIsNone(snapshot["tokens"]["input_tokens"])
        self.assertIsNone(snapshot["tokens"]["output_tokens"])
        self.assertEqual(0, snapshot["tokens"]["total_rows"])
        self.assertIsNone(snapshot["qa"]["pass_rate_percent"])
        self.assertEqual(0, snapshot["knowledge"]["active_count"])
        self.assertEqual("unknown", snapshot["agents"][0]["current_status"])

    def test_project_error_is_generic_partial_and_never_private(self) -> None:
        snapshot = build_snapshot(
            {"lifeos": self.reader(LIFEOS), "devteam": BadReader()},
            datetime.now(UTC),
        )
        rendered = json.dumps(snapshot)
        self.assertEqual("partial", snapshot["data_status"])
        self.assertIsNone(snapshot["metrics"]["active_tasks"])
        self.assertIsNone(snapshot["tokens"]["input_tokens"])
        self.assertEqual("CONTROL_DATA_UNAVAILABLE", snapshot["errors"][0]["code"])
        self.assertNotIn("PRIVATE-MARKER", rendered)
        self.assertNotIn("RuntimeError", rendered)
        self.assertTrue(all(agent["current_status"] == "unknown" for agent in snapshot["agents"]))

    def test_today_bounds_follow_explicit_shanghai_offset(self) -> None:
        completed_today = "2026-11-22T16:05:00.000000Z"
        completed_previous = "2026-11-22T15:55:00.000000Z"
        self.insert_tasks(
            [
                {
                    "project_id": LIFEOS,
                    "id": identifier(50),
                    "state": "completed",
                    "summary": "Shanghai crossing completed",
                    "assigned_agent": None,
                    "completed_time": completed_today,
                    "created_at": completed_previous,
                    "updated_at": completed_today,
                },
                {
                    "project_id": LIFEOS,
                    "id": identifier(51),
                    "state": "completed",
                    "summary": "Previous local day",
                    "assigned_agent": None,
                    "completed_time": completed_previous,
                    "created_at": completed_previous,
                    "updated_at": completed_previous,
                },
                {
                    "project_id": LIFEOS,
                    "id": identifier(52),
                    "state": "blocked",
                    "summary": "Blocked after local midnight",
                    "assigned_agent": None,
                    "completed_time": None,
                    "created_at": completed_previous,
                    "updated_at": "2026-11-22T16:10:00.000000Z",
                },
                {
                    "project_id": LIFEOS,
                    "id": identifier(53),
                    "state": "failed",
                    "summary": "Failed before local midnight",
                    "assigned_agent": None,
                    "completed_time": None,
                    "created_at": completed_previous,
                    "updated_at": completed_previous,
                },
            ]
        )
        reader = self.reader(LIFEOS)
        first = build_snapshot(
            {"lifeos": reader},
            datetime(2026, 11, 23, 0, 30, tzinfo=SHANGHAI),
        )
        second = build_snapshot(
            {"lifeos": reader},
            datetime(2026, 11, 24, 0, 0, tzinfo=SHANGHAI),
        )
        self.assertEqual(1, first["metrics"]["completed_today"])
        self.assertEqual(1, first["metrics"]["failed_blocked_today"])
        self.assertEqual(0, second["metrics"]["completed_today"])
        self.assertEqual(0, second["metrics"]["failed_blocked_today"])

    def test_browser_boundary_masks_secrets_again(self) -> None:
        now = datetime.now(UTC)
        task = self.store.create_task(LIFEOS, "Synthetic sanitization", task_id=identifier(60))
        run = self.store.record_run(
            LIFEOS,
            task["id"],
            role="builder",
            model_name="placeholder",
            build_number=1,
            attempt=1,
            status="completed",
            started_at=now.isoformat(),
            run_id=identifier(61),
        )
        self.sql(
            "UPDATE dev_tasks SET requirement_summary=:summary, assigned_agent=:agent "
            "WHERE project_id=:project_id AND id=:task_id",
            {
                "summary": "Contact owner@example.com api_key=supersecret123",
                "agent": "bearer abcdefghijklmnop",
                "project_id": LIFEOS,
                "task_id": task["id"],
            },
        )
        self.sql(
            "UPDATE agent_runs SET model_name=:model "
            "WHERE project_id=:project_id AND id=:run_id",
            {
                "model": "deepseek sk-abcdefgh12345678",
                "project_id": LIFEOS,
                "run_id": run["id"],
            },
        )
        rendered = json.dumps(build_snapshot({"lifeos": self.reader(LIFEOS)}, now))
        for secret in (
            "owner@example.com",
            "supersecret123",
            "abcdefghijklmnop",
            "sk-abcdefgh12345678",
        ):
            self.assertNotIn(secret, rendered)
        self.assertIn("[REDACTED_EMAIL]", rendered)

    def test_recent_task_list_is_bounded_to_twenty(self) -> None:
        base = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)
        rows = []
        for index in range(25):
            when = (base + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
            rows.append(
                {
                    "project_id": LIFEOS,
                    "id": identifier(1000 + index),
                    "state": "active",
                    "summary": f"Synthetic recent task {index}",
                    "assigned_agent": "scanner",
                    "completed_time": None,
                    "created_at": when,
                    "updated_at": when,
                }
            )
        self.insert_tasks(rows)
        snapshot = build_snapshot({"lifeos": self.reader(LIFEOS)}, base)
        self.assertEqual(20, len(snapshot["recent_tasks"]))
        self.assertEqual("Synthetic recent task 24", snapshot["recent_tasks"][0]["summary"])

    def test_snapshot_service_accepts_synthetic_injection(self) -> None:
        class Project:
            project_id = LIFEOS
            repository = "dabinglei2-hash/LifeOS"
            root = Path("synthetic")

        class Registry:
            def project(self, _key: str) -> Project:
                return Project()

        fixed = datetime(2026, 11, 23, 12, 0, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as directory:
            service = SnapshotService(
                registry=Registry(),  # type: ignore[arg-type]
                readers={"lifeos": self.reader(LIFEOS)},
                root=Path(directory),
                now_provider=lambda: fixed,
                include_git=False,
            )
            snapshot = service.snapshot("lifeos")
        self.assertEqual("lifeos", snapshot["project_filter"])
        self.assertEqual("2026-11-23T12:00:00+08:00", snapshot["sampled_at"])
        self.assertEqual("unknown", snapshot["repositories"][0]["remote_status"])


class ControlCenterOwnershipTests(unittest.TestCase):
    def test_verified_windows_redirector_child_is_owned(self):
        from unittest import mock
        from control_center import manager as m
        health = {"service":m.SERVICE_ID,"schema":"stage1","read_only":True,"instance_id":"owned","pid":202}
        with mock.patch.object(m,"_parent_process_id",return_value=101), mock.patch.object(m,"_process_creation_epoch",return_value=11.0):
            self.assertEqual((202,11.0),m._owned_server_identity(health,"owned",101,10.0))
    def test_unrelated_process_and_reused_pid_are_rejected(self):
        from unittest import mock
        from control_center import manager as m
        health = {"service":m.SERVICE_ID,"schema":"stage1","read_only":True,"instance_id":"owned","pid":202}
        with mock.patch.object(m,"_parent_process_id",return_value=303), mock.patch.object(m,"_process_creation_epoch",return_value=11.0):
            self.assertIsNone(m._owned_server_identity(health,"owned",101,10.0))
        with mock.patch.object(m,"_parent_process_id",return_value=101), mock.patch.object(m,"_process_creation_epoch",return_value=1.0):
            self.assertIsNone(m._owned_server_identity(health,"owned",101,10.0))
    def test_wrong_instance_is_rejected_before_process_lookup(self):
        from unittest import mock
        from control_center import manager as m
        with mock.patch.object(m,"_parent_process_id") as probe:
            self.assertIsNone(m._owned_server_identity({"pid":202},"owned",101,10.0));probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
