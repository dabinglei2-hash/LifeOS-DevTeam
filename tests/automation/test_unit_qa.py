"""Synthetic tests for the controller-owned v2 unit QA contract."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lads_automation import qa, unit_qa

TASK = "ac4f4d74-9aaf-43b2-b40a-e988c45e682f"
HEAD = "a" * 40
OUTPUT_SHA = "b" * 64


def report_body(files_digest: str) -> dict:
    return {
        "schema": unit_qa.UNIT_REPORT_SCHEMA,
        "qa_contract_version": unit_qa.UNIT_CONTRACT_VERSION,
        "task_id": TASK,
        "source_head": HEAD,
        "source_inventory_sha256": files_digest,
        "simulation": False,
        "network_access": False,
        "candidate_unchanged": True,
        "working_copy_unchanged": True,
        "ready": True,
        "suites": [
            {
                "id": suite,
                "command": command,
                "process": {
                    "status": "completed",
                    "exit_code": 0,
                    "timed_out": False,
                    "output_overflow": False,
                    "output_sha256": OUTPUT_SHA,
                },
                "test_count": 2,
                "failure_count": 0,
                "error_count": 0,
                "skip_count": 0,
            }
            for suite, command in unit_qa.UNIT_SUITES
        ],
    }


class UnitOutputTests(unittest.TestCase):
    def test_full_success_failure_and_skip_outputs_are_counted(self):
        success = "test_ok ... ok\n" + "-" * 70 + "\nRan 3 tests in 0.001s\n\nOK\n"
        parsed = unit_qa.parse_unittest_output(success)
        self.assertEqual(
            {
                "successful": True,
                "test_count": 3,
                "failure_count": 0,
                "error_count": 0,
                "skip_count": 0,
            },
            parsed,
        )
        failed = (
            "test_bad ... FAIL\n"
            + "-" * 70
            + "\nRan 4 tests in 0.002s\n\n"
            "FAILED (failures=1, errors=2, skipped=1)\n"
        )
        parsed = unit_qa.parse_unittest_output(failed)
        self.assertFalse(parsed["successful"])
        self.assertEqual((4, 1, 2, 1), (
            parsed["test_count"],
            parsed["failure_count"],
            parsed["error_count"],
            parsed["skip_count"],
        ))
        skipped = "-" * 70 + "\nRan 1 test in 0.001s\n\nOK (skipped=1)\n"
        parsed = unit_qa.parse_unittest_output(skipped)
        self.assertTrue(parsed["successful"])
        self.assertEqual(1, parsed["skip_count"])

    def test_truncated_or_malformed_output_is_rejected(self):
        with self.assertRaises(unit_qa.UnitQAVerificationError):
            unit_qa.parse_unittest_output("Ran 2 tests in 0.1s")
        with self.assertRaises(unit_qa.UnitQAVerificationError):
            unit_qa.parse_unittest_output("Ran 0 tests in 0.1s\n\nOK\n")
        with self.assertRaises(unit_qa.UnitQAVerificationError):
            unit_qa.parse_unittest_output("Ran 1 test in 0.1s\n\nOK\nFAILED (errors=1)\n")


class VerifiedRunFixture:
    def __init__(self, root: Path):
        self.root = root
        self.run_root = root / "run"
        self.candidate = self.run_root / "qa" / "build-1"
        self.candidate.mkdir(parents=True)
        (self.candidate / "app.js").write_bytes(b"const unit = true;\n")
        self.files = qa.source_inventory(self.candidate)
        self.digest = qa.inventory_digest(HEAD, self.files)
        self.state = {
            "task_id": TASK,
            "source_head": HEAD,
            "builds": 1,
        }
        (self.run_root / "state.json").write_text(
            json.dumps(self.state),
            encoding="utf-8",
        )
        self.write(report_body(self.digest))

    def write(self, body):
        report = qa.seal_broker_report(body)
        report_path = self.run_root / unit_qa.UNIT_REPORT_NAME
        report_path.write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        context = {
            "schema": unit_qa.UNIT_CONTEXT_SCHEMA,
            "qa_contract_version": unit_qa.UNIT_CONTRACT_VERSION,
            "task_id": body["task_id"],
            "source_head": body["source_head"],
            "source_inventory_sha256": body["source_inventory_sha256"],
            "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "report_receipt_sha256": report["receipt_sha256"],
        }
        (self.run_root / unit_qa.UNIT_CONTEXT_NAME).write_text(
            json.dumps(context, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return report_path


class UnitVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = VerifiedRunFixture(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def verify(self, suite="automation"):
        return unit_qa.verify_unit_for_run(
            suite,
            self.fixture.run_root / unit_qa.UNIT_REPORT_NAME,
            candidate_root=self.fixture.candidate,
        )

    def test_exact_actual_receipts_pass_both_suites(self):
        first = self.verify("automation")
        second = self.verify("knowledge")
        self.assertEqual("PASS", first["status"])
        self.assertEqual("PASS", second["status"])
        self.assertEqual(first["evidence_sha256"], second["evidence_sha256"])
        self.assertEqual([2, 2], [row["test_count"] for row in first["suites"]])

    def test_raw_report_context_identity_and_candidate_tamper_are_rejected(self):
        report_path = self.fixture.run_root / unit_qa.UNIT_REPORT_NAME
        report_path.write_bytes(report_path.read_bytes() + b" ")
        with self.assertRaisesRegex(unit_qa.UnitQAVerificationError, "raw digest"):
            self.verify()

        self.fixture.write(report_body(self.fixture.digest))
        (self.fixture.candidate / "app.js").write_bytes(b"changed\n")
        with self.assertRaisesRegex(unit_qa.UnitQAVerificationError, "actual candidate"):
            self.verify()

    def test_wrong_task_head_command_and_process_failures_are_rejected(self):
        mutations = []
        wrong_task = report_body(self.fixture.digest)
        wrong_task["task_id"] = "bc4f4d74-9aaf-43b2-b40a-e988c45e682f"
        mutations.append((wrong_task, "different task"))
        wrong_head = report_body(self.fixture.digest)
        wrong_head["source_head"] = "c" * 40
        mutations.append((wrong_head, "different source HEAD"))
        wrong_command = report_body(self.fixture.digest)
        wrong_command["suites"][0]["command"] = "python -B arbitrary.py"
        mutations.append((wrong_command, "identity or command"))
        failed = report_body(self.fixture.digest)
        failed["suites"][0]["process"]["status"] = "failed"
        failed["suites"][0]["process"]["exit_code"] = 1
        mutations.append((failed, "failed, timed out"))
        timeout_report = report_body(self.fixture.digest)
        timeout_report["suites"][1]["process"].update(
            {"status": "timeout", "exit_code": None, "timed_out": True}
        )
        mutations.append((timeout_report, "failed, timed out"))
        infra = report_body(self.fixture.digest)
        infra["suites"][0]["process"].update(
            {"status": "error", "exit_code": None, "output_sha256": None}
        )
        mutations.append((infra, "infrastructure-error"))
        skipped = report_body(self.fixture.digest)
        skipped["suites"][1]["skip_count"] = 1
        mutations.append((skipped, "skips"))
        no_tests = report_body(self.fixture.digest)
        no_tests["suites"][0]["test_count"] = 0
        mutations.append((no_tests, "required integer"))
        for body, message in mutations:
            with self.subTest(message=message):
                self.fixture.write(body)
                with self.assertRaisesRegex(unit_qa.UnitQAVerificationError, message):
                    self.verify()

    def test_missing_suite_or_unknown_api_suite_is_rejected(self):
        body = report_body(self.fixture.digest)
        body["suites"].pop()
        self.fixture.write(body)
        with self.assertRaisesRegex(unit_qa.UnitQAVerificationError, "exactly two"):
            self.verify()
        with self.assertRaisesRegex(unit_qa.UnitQAVerificationError, "automation or knowledge"):
            self.verify("browser")

    def test_reparse_and_wrong_managed_qa_copy_are_rejected(self):
        wrong = self.fixture.run_root / "qa" / "build-other"
        wrong.mkdir()
        with self.assertRaisesRegex(unit_qa.UnitQAVerificationError, "exact QA build"):
            unit_qa.verify_unit_for_run(
                "automation",
                self.fixture.run_root / unit_qa.UNIT_REPORT_NAME,
                candidate_root=wrong,
            )

        def marked_reparse(path):
            return Path(path).name == unit_qa.UNIT_CONTEXT_NAME

        with mock.patch.object(unit_qa, "_is_reparse", side_effect=marked_reparse):
            with self.assertRaisesRegex(unit_qa.UnitQAVerificationError, "link or reparse"):
                self.verify()

    def test_known_legacy_and_v2_contracts_are_distinct(self):
        self.assertEqual(1, unit_qa.contract_version(unit_qa.LEGACY_STAGE3_QA_COMMANDS))
        self.assertEqual(2, unit_qa.contract_version(unit_qa.STAGE4_QA_COMMANDS))
        self.assertIsNone(unit_qa.contract_version(("caller-pass",)))


class FakeExecutor:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "stdout": self.output,
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "output_overflow": False,
            "output_sha256": hashlib.sha256(self.output.encode("utf-8")).hexdigest(),
        }


class UnitBrokerTests(unittest.TestCase):
    def test_broker_rejects_report_reparse_before_executor(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            candidate = root / "candidate"
            candidate.mkdir()
            (candidate / "app.js").write_bytes(b"const candidate = true;\n")
            report_path = root / unit_qa.UNIT_REPORT_NAME
            report_path.write_bytes(b"{}")
            executor = FakeExecutor("")
            broker = unit_qa.UnitQABroker(executor=executor, work_root=root)

            def marked_reparse(path):
                return Path(path) == report_path

            with mock.patch.object(unit_qa, "_is_reparse", side_effect=marked_reparse):
                with self.assertRaisesRegex(
                    unit_qa.UnitQAVerificationError,
                    "link or reparse",
                ):
                    broker.run(
                        task_id=TASK,
                        source_head=HEAD,
                        candidate_root=candidate,
                        report_path=report_path,
                    )
            self.assertEqual([], executor.calls)

    def test_broker_runs_only_exact_isolated_commands_and_publishes_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            candidate.mkdir()
            (candidate / "app.js").write_bytes(b"const candidate = true;\n")
            output = (
                "test_ok ... ok\n"
                + "-" * 70
                + "\nRan 2 tests in 0.001s\n\nOK\n"
            )
            executor = FakeExecutor(output)
            broker = unit_qa.UnitQABroker(executor=executor, work_root=root)
            report = broker.run(
                task_id=TASK,
                source_head=HEAD,
                candidate_root=candidate,
                report_path=root / unit_qa.UNIT_REPORT_NAME,
            )
            self.assertTrue(report["ready"])
            self.assertEqual(
                [command for _suite, command in unit_qa.UNIT_SUITES],
                [call["command"] for call in executor.calls],
            )
            for call in executor.calls:
                self.assertFalse(call["network_access"])
                self.assertEqual(
                    {"TEMP", "TMP", "LOCALAPPDATA"},
                    set(call["environment"]),
                )
                self.assertNotEqual(candidate, Path(call["cwd"]))
            self.assertTrue((root / unit_qa.UNIT_REPORT_NAME).is_file())
            self.assertTrue((root / unit_qa.UNIT_CONTEXT_NAME).is_file())
            leftovers = [
                item.name
                for item in root.iterdir()
                if item.name.startswith("uq-")
            ]
            self.assertEqual([], leftovers)


if __name__ == "__main__":
    unittest.main()
