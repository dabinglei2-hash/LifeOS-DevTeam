"""Synthetic, isolated telemetry tests; no model requests or product storage."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from control_center.store import MonitorStore, validate_event, utcnow
from control_center.monitor import MonitorService, agent_status, estimate
from control_center import bridge

ROOT = Path(__file__).resolve().parents[2]


def envelope(**values):
    return {"event_id": str(uuid.uuid4()), "task_id": str(uuid.uuid4()), "run_id": str(uuid.uuid4()),
        "project": "devteam", "agent_id": "builder", "event_type": "AGENT_STARTED", "timestamp": utcnow(),
        "source": "control-center-test", "provider": "deepseek", "model_name": "deepseek-flash", "payload": {}, **values}


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name) / "monitor"
        self.store = MonitorStore(self.home)

    def tearDown(self):
        self.store.engine.dispose()
        self.temporary.cleanup()

    def test_duplicate_event_id_is_idempotent(self):
        row = envelope()
        self.assertTrue(self.store.append(row))
        self.assertFalse(self.store.append(row))
        self.assertEqual(len(self.store.rows()), 1)

    def test_project_and_task_isolation(self):
        row = envelope()
        self.store.append(row)
        self.store.append(envelope(project="lifeos"))
        self.assertEqual(len(self.store.rows(project="devteam")), 1)
        self.assertEqual(len(self.store.rows(task_id=row["task_id"])), 1)

    def test_query_limits(self):
        for kwargs in ({"limit": 201}, {"limit": 0}, {"project": "private"}, {"after": -1}):
            with self.assertRaises(ValueError): self.store.rows(**kwargs)

    def test_unknown_fields_and_nested_payload_rejected(self):
        for row in (envelope(api_key="secret"), envelope(payload={"status": {"password": "secret"}}), envelope(payload={"prompt": "private"})):
            with self.assertRaises(ValueError): validate_event(row)

    def test_tokens_are_nullable_and_nonnegative(self):
        self.store.append(envelope(event_type="MODEL_USAGE", payload={"input_tokens": None, "output_tokens": 0}))
        self.assertIsNone(self.store.rows()[0]["payload"]["input_tokens"])
        for invalid in (-1, True, "12"):
            with self.assertRaises(ValueError): validate_event(envelope(payload={"input_tokens": invalid}))

    def test_sensitive_payload_is_redacted(self):
        self.store.append(envelope(payload={"status": "token=synthetic-private-value"}))
        self.assertNotIn("synthetic-private", json.dumps(self.store.rows()))

    def test_timezone_required(self):
        with self.assertRaises(ValueError): validate_event(envelope(timestamp="2026-11-23T10:00:00"))

    def test_restart_retains_rows(self):
        self.store.append(envelope())
        reopened = MonitorStore(self.home)
        self.assertEqual(len(reopened.rows()), 1)
        reopened.engine.dispose()

    def test_backup_restore_and_no_overwrite(self):
        self.store.append(envelope())
        backup = self.store.backup()
        restored = MonitorStore.restore(backup, Path(self.temporary.name) / "restored")
        self.assertEqual(len(restored.rows()), 1)
        restored.engine.dispose()
        with self.assertRaises(ValueError): MonitorStore.restore(backup, self.home)

    def test_foreign_or_future_database_refused(self):
        path = Path(self.temporary.name) / "foreign"
        path.mkdir()
        with closing(sqlite3.connect(path / "control-center.sqlite3")) as connection:
            connection.execute("PRAGMA user_version=99")
        with self.assertRaises(ValueError): MonitorStore(path)

    def test_spool_success_and_invalid_retention(self):
        spool = self.home / "spool"
        spool.mkdir()
        row = envelope()
        path = spool / (row["event_id"] + ".json")
        path.write_text(json.dumps(row), encoding="utf-8")
        self.assertEqual(self.store.ingest_spool(), 1)
        self.assertFalse(path.exists())
        path.write_text("{}", encoding="utf-8")
        self.assertEqual(self.store.ingest_spool(), 0)
        self.assertTrue(path.with_suffix(".rejected").exists())
        self.assertEqual(self.store.errors()[0]["code"], "SPOOL_ValueError")

    def test_transient_database_write_keeps_outbox(self):
        spool = self.home / "spool"
        spool.mkdir()
        row = envelope()
        path = spool / (row["event_id"] + ".json")
        path.write_text(json.dumps(row), encoding="utf-8")
        with patch.object(self.store, "append", side_effect=RuntimeError("synthetic database failure")):
            with self.assertRaises(RuntimeError): self.store.ingest_spool()
        self.assertTrue(path.exists())
        self.assertEqual(self.store.ingest_spool(), 1)

    def test_status_expiry_crash_and_completion(self):
        self.assertEqual(agent_status(None), "UNKNOWN")
        row = envelope(process_id=9999, process_created="123")
        with patch("control_center.monitor.process_alive", return_value=True):
            self.assertEqual(agent_status(row), "BUSY")
            self.assertEqual(agent_status(row, datetime.now(timezone.utc) + timedelta(seconds=21)), "OFFLINE")
        with patch("control_center.monitor.process_alive", return_value=False): self.assertEqual(agent_status(row), "OFFLINE")
        self.assertEqual(agent_status(envelope(event_type="AGENT_TASK_FAILED")), "ERROR")
        self.assertEqual(agent_status(envelope(event_type="AGENT_TASK_COMPLETED")), "OFFLINE")

    def test_latest_agent_sample_is_per_run(self):
        row = envelope()
        self.store.append(row)
        self.store.append(envelope(run_id=row["run_id"], event_type="AGENT_HEARTBEAT"))
        self.assertEqual(len(self.store.latest_agents()), 1)
        self.assertEqual(self.store.latest_agents()[0]["event_type"], "AGENT_HEARTBEAT")

    def test_cache_last_snapshot_persists_on_error(self):
        self.store.put_cache("github:devteam", {"name": "synthetic"})
        self.store.put_cache("github-health", {"status": "error"})
        self.assertEqual(self.store.get_cache("github:devteam")["data"]["name"], "synthetic")

    def test_bridge_opt_in_and_secret_free(self):
        with patch.object(bridge, "HOME", self.home), bridge.reporting():
            self.assertFalse(bridge.send(task_id=str(uuid.uuid4()), role="builder", event_type="AGENT_STARTED"))
            (self.home / "telemetry.enabled.json").write_text('{"schema":1}', encoding="utf-8")
            self.assertTrue(bridge.send(task_id=str(uuid.uuid4()), role="builder", event_type="AGENT_STARTED"))
            self.assertEqual(self.store.ingest_spool(), 1)

    def test_four_roles_actual_host_process_monitoring(self):
        spec = importlib.util.spec_from_file_location("monitor_runtime_test", ROOT / "scripts/lads_runtime.py")
        runtime = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runtime
        spec.loader.exec_module(runtime)
        (self.home / "telemetry.enabled.json").write_text('{"schema":1}', encoding="utf-8")
        with patch.object(bridge, "HOME", self.home), bridge.reporting():
            for role in ("scanner", "reviewer", "builder", "qa-tester"):
                monitor = bridge.WorkerMonitor(str(uuid.uuid4()), role, "synthetic-process-not-model")
                result = runtime.run_process([sys.executable, "-B", "-c", "print('synthetic')"], ROOT, "", 15, observer=monitor.observe)
                self.assertEqual(result.exit_code, 0)
            failed = bridge.WorkerMonitor(str(uuid.uuid4()), "builder", "synthetic-process-not-model")
            runtime.run_process([sys.executable, "-B", "-c", "raise SystemExit(7)"], ROOT, "", 15, observer=failed.observe)
        self.assertGreaterEqual(self.store.ingest_spool(), 15)
        rows = self.store.rows()
        self.assertIn("AGENT_TASK_FAILED", {row["event_type"] for row in rows})
        self.assertEqual({row["agent_id"] for row in rows}, {"scanner", "reviewer", "builder", "qa-tester"})

    def test_price_estimate_effective_date_and_unknown_cache(self):
        price = {"provider": "deepseek", "model": "synthetic", "effective_from": "2026-01-01T00:00:00+00:00",
                 "currency": "USD", "input_per_million": "1", "cached_per_million": "0.5", "output_per_million": "2", "source": "https://example.invalid/synthetic"}
        row = {"provider": "deepseek", "model_name": "synthetic", "input_tokens": 1000, "output_tokens": 500,
               "cached_tokens": None, "created_at": "2026-11-01T00:00:00Z"}
        self.assertIsNone(estimate(row, [price]))
        row["cached_tokens"] = 200
        self.assertEqual(estimate(row, [price])["amount"], "0.0019")
        self.assertEqual(estimate(row, [price])["status"], "ESTIMATED")
        self.assertEqual(row["input_tokens"], 1000)
        row["created_at"] = "2025-01-01T00:00:00Z"
        self.assertIsNone(estimate(row, [price]))

    def test_continuous_event_cursor_and_project_filter(self):
        for project in ("lifeos", "devteam", "lifeos", "devteam"):
            self.store.append(envelope(project=project))
        rows = self.store.rows(project="devteam", limit=1, descending=False)
        following = self.store.rows(project="devteam", after=rows[0]["sequence"], descending=False)
        self.assertEqual(len(following), 1)
        self.assertGreater(following[0]["sequence"], rows[0]["sequence"])

    def test_default_test_process_cannot_report_to_enabled_home(self):
        (self.home / "telemetry.enabled.json").write_text('{"schema":1}', encoding="utf-8")
        with patch.object(bridge, "HOME", self.home):
            monitor = bridge.WorkerMonitor(str(uuid.uuid4()), "builder", "synthetic")
            self.assertFalse(monitor.emit("AGENT_STARTED"))
        self.assertEqual(self.store.rows(), [])

    def test_legacy_untrusted_events_are_retained_but_not_live(self):
        self.store.append(envelope(source="lads-worker-host"))
        self.assertEqual(len(self.store.rows()), 1)
        self.assertEqual(self.store.rows(trusted_only=True), [])
        self.assertEqual(self.store.latest_agents(trusted_only=True), [])


if __name__ == "__main__":
    unittest.main()
