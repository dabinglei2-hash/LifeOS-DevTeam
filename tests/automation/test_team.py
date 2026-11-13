"""Memory-only tests for the opt-in Stage 3 process adapter."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

from lads_automation import unit_qa
from lads_automation.state import MemoryJournal
from lads_automation.team import (
    BASE,
    Stage3Orchestrator,
    Stage3Runner,
    collect_process_failure,
    record_worker_completed,
)

TASK = "ac4f4d74-9aaf-43b2-b40a-e988c45e682f"


class FakeResult:
    def __init__(self, *, stdout="", stderr="", exit_code=1, timed_out=False, bounded=False):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.bounded_output = bounded


class ProcessEvidenceTests(unittest.TestCase):
    def test_true_tail_after_12k_prefix_is_retained_and_redacted(self):
        stderr = "harmless-prefix-" * 1000 + "\nLAST_REAL_ERROR Bearer " + "x" * 32 + "\n"
        result = FakeResult(stderr=stderr)
        original = (result.stdout, result.stderr, result.exit_code, result.timed_out)
        metadata = collect_process_failure(result)
        self.assertIn("LAST_REAL_ERROR", metadata["error_tail"])
        self.assertNotIn("x" * 32, metadata["error_tail"])
        self.assertEqual(original, (result.stdout, result.stderr, result.exit_code, result.timed_out))
        self.assertEqual("failed", metadata["status"])

    def test_structured_error_event_is_used_without_keyword_auth_guessing(self):
        result = FakeResult(
            stdout='{"type":"error","code":"rate_limited","message":"auth mentioned in synthetic text"}\n',
            exit_code=1,
        )
        metadata = collect_process_failure(result)
        self.assertEqual("rate_limited", metadata["error_events"][0]["code"])
        self.assertEqual("failed", metadata["status"])

    def test_worker_completed_is_controller_domain_and_not_native_subagent_stop(self):
        journal = MemoryJournal()
        journal.create_task(
            {
                "task_id": TASK,
                "status": "QA_REVIEW_VERIFIED",
                "ready": False,
                "session_id": "synthetic",
                "event_ids": [],
            }
        )
        row = record_worker_completed(
            journal,
            task_id=TASK,
            role="qa-tester",
            status="PASS",
            provider="lifeos_deepseek",
            model="deepseek-flash",
            result=FakeResult(stdout="", exit_code=0),
            source_head="a" * 40,
            candidate_sha256="b" * 64,
            stage_metadata={
                "role": "qa-tester",
                "provider": "lifeos_deepseek",
                "model": "deepseek-flash",
                "sandbox": "read-only",
                "start_time": "2026-11-04T00:00:00+00:00",
                "end_time": "2026-11-04T00:00:01+00:00",
                "input_tokens": 10,
                "output_tokens": 20,
                "config_snapshot_sha256": "c" * 64,
                "home_mode": "original",
                "home_sha256": "d" * 64,
                "authority_sha256": "e" * 64,
            },
            stage_ref={"path": "stages/qa.json", "sha256": "f" * 64},
        )
        self.assertEqual("worker_completed", row["event_type"])
        self.assertFalse(row["payload"]["native_subagent_stop"])
        self.assertEqual(
            [],
            journal.list_events(TASK, event_type="native_subagent_stop"),
        )
        self.assertEqual(10, row["payload"]["stage"]["input_tokens"])
        self.assertEqual("stages/qa.json", row["payload"]["stage_ref"]["path"])

    def test_stage3_adapter_keeps_legacy_browser_flag_explicit(self):
        self.assertTrue(issubclass(Stage3Orchestrator, BASE.Orchestrator))
        requirements = Stage3Orchestrator.stage3_requirements()
        self.assertFalse(requirements["base_browser_supported"])
        self.assertTrue(requirements["stage3_browser_supported"])
        self.assertEqual("NOT_IMPLEMENTED", requirements["real_authentication"])

    def test_stage3_credentials_use_canonical_qa_name_and_explicit_source(self):
        path = Path(__file__).resolve().parents[2] / "scripts/lads_stage3.py"
        spec = importlib.util.spec_from_file_location("stage3_cli_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIn("DEEPSEEK_QA_API_KEY", module._WORKER_KEYS)
        self.assertNotIn("DEEPSEEK_QA_TESTER_API_KEY", module._WORKER_KEYS)
        parsed = module.build_parser().parse_args(
            [
                "RUN",
                "--task-file",
                "C:/synthetic/task.json",
                "--work-root",
                "C:/synthetic/work",
                "--credential-source",
                "windows-user",
            ]
        )
        self.assertEqual("windows-user", parsed.credential_source)
        with mock.patch.dict(module.os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_QA_API_KEY"):
                module._load_worker_environment("env")

    def test_stage3_contract_contains_verifier_before_worker_call(self):
        with self.assertRaises(BASE.Blocked):
            Stage3Runner.expected_contract(
                (
                    "python -B -m unittest discover -s tests/automation -v",
                    "python -B -m unittest discover -s tests/knowledge -v",
                )
            )
        self.assertEqual(
            (
                *unit_qa.UNIT_VERIFY_COMMANDS,
                "python -B scripts/lads_stage3.py verify-broker "
                "../../qa-broker-current.json",
            ),
            Stage3Runner.expected_contract(
                unit_qa.STAGE4_QA_COMMANDS
            ),
        )
        manifest = BASE.Manifest(
            Path("C:/synthetic-repo"),
            "synthetic",
            ("app.js",),
            (
                "python -B -m unittest discover -s tests/automation -v",
                "python -B -m unittest discover -s tests/knowledge -v",
            ),
            {"browser": False, "multiuser": False},
            "a" * 64,
        )
        expanded = Stage3Runner.contract_manifest(manifest)
        self.assertEqual(
            (
                "python -B -m unittest discover -s tests/automation -v",
                "python -B -m unittest discover -s tests/knowledge -v",
            ),
            manifest.test_commands,
        )
        self.assertEqual(
            (
                *unit_qa.UNIT_VERIFY_COMMANDS,
                "python -B scripts/lads_stage3.py verify-broker "
                "../../qa-broker-current.json",
            ),
            expanded.test_commands,
        )
        captured = []
        runner = object.__new__(Stage3Runner)
        runner.orchestrator = mock.Mock()
        runner.orchestrator.run.side_effect = lambda value: captured.append(value) or {
            "task_status": "AWAITING_MAIN_ACCEPTANCE"
        }
        with self.assertRaises(BASE.Blocked):
            runner.run(manifest)
        runner.run(expanded)
        self.assertEqual(expanded.test_commands, captured[0].test_commands)

    def test_stage3_qa_does_not_expand_commands_inside_worker_call(self):
        class Broker:
            def run(self, **_kwargs):
                return {
                    "receipt_sha256": "a" * 64,
                    "source_head": "b" * 40,
                    "source_inventory_sha256": "c" * 64,
                    "ready": True,
                    "suites": [],
                    "checks": [],
                }

        class UnitBroker:
            def run(self, **_kwargs):
                return {
                    "receipt_sha256": "e" * 64,
                    "ready": True,
                    "suites": [],
                }

        adapter = Stage3Orchestrator(broker=Broker(), unit_broker=UnitBroker())
        adapter.run_root = Path(__file__).resolve().parents[2]
        adapter.state = {
            "task_id": TASK,
            "source_head": "b" * 40,
            "builds": 1,
        }
        with mock.patch.object(BASE.Orchestrator, "worker_call", return_value="ok") as parent:
            result = adapter.worker_call(
                "qa-tester",
                adapter.run_root / "tests",
                "QA prompt",
                "d" * 64,
                unit_qa.STAGE4_QA_COMMANDS,
            )
        self.assertEqual("ok", result)
        expanded = parent.call_args.args[4]
        self.assertEqual(
            unit_qa.STAGE4_QA_COMMANDS,
            expanded,
        )
        self.assertIn("base browser Boolean remains false", parent.call_args.args[2])
        self.assertIn("Controller-owned unit evidence", parent.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
