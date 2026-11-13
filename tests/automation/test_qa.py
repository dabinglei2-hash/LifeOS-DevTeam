"""Memory/static tests for the Stage 3 QA verifier."""
from __future__ import annotations

import os
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lads_automation import qa

TASK = "ac4f4d74-9aaf-43b2-b40a-e988c45e682f"
HEAD = "a" * 40
ROOT = Path(__file__).resolve().parents[2]
PRODUCT_QA_ROOT = Path(os.environ.get("LADS_PRODUCT_QA_ROOT", str(ROOT.parent / "LifeOS-Product")))


def suite_receipts(required_checks=None):
    required_checks = frozenset(required_checks or qa.REQUIRED_CHECKS)
    return [
        {
            **suite,
            "sha256": "b" * 64,
            "process": {
                "status": "completed",
                "exit_code": 0,
                "timed_out": False,
                "output_overflow": False,
                "output_sha256": "c" * 64,
            },
        }
        for suite in qa.TRUSTED_SUITES
        if suite["required"] or "notes-home-card-contract" in required_checks
    ]


def checks():
    return [
        {"id": identity, "status": "PASS", "evidence": "actual synthetic check receipt"}
        for identity in sorted(qa.REQUIRED_CHECKS)
    ] + [
        {
            "id": identity,
            "status": "NOT_IMPLEMENTED",
            "evidence": "Product limitation observed in source.",
        }
        for identity in sorted(qa.RUNTIME_LIMITATIONS)
    ]


def valid_report(files):
    body = {
        "schema": "lads-stage3-broker-v1",
        "task_id": TASK,
        "source_head": HEAD,
        "candidate_sha256": qa.inventory_digest(HEAD, files),
        "source_inventory_sha256": qa.inventory_digest(HEAD, files),
        "source_files": files,
        "build_number": 1,
        "created_at": "2026-11-04T00:00:00+00:00",
        "simulation": False,
        "private_root": "private-placeholder",
        "suites": suite_receipts(),
        "checks": checks(),
        "required_checks": sorted(qa.REQUIRED_CHECKS),
        "ready": True,
    }
    return qa.seal_broker_report(body)


class PinnedSuiteTests(unittest.TestCase):
    def test_controller_suite_files_match_pinned_hashes(self):
        inventory = qa.trusted_suite_inventory(PRODUCT_QA_ROOT)
        self.assertEqual(
            set(qa.TRUSTED_SUITE_SHA256),
            {item["id"] for item in inventory},
        )
        for item in inventory:
            if next(
                suite.get("pin", True)
                for suite in qa.TRUSTED_SUITES
                if suite["id"] == item["id"]
            ):
                self.assertIn(
                    item["sha256"],
                    qa.TRUSTED_SUITE_SHA256[item["id"]],
                )

    def test_pwa_pins_accept_exact_lf_and_crlf_but_reject_changed_bytes(self):
        cases = (
            ("pwa-build", "scripts/build-pwa.mjs"),
            ("pwa-check", "scripts/check-pwa.mjs"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for identity, relative_path in cases:
                with self.subTest(identity=identity):
                    source = (PRODUCT_QA_ROOT / relative_path).read_bytes()
                    lf_bytes = source.replace(b"\r\n", b"\n")
                    crlf_bytes = lf_bytes.replace(b"\n", b"\r\n")
                    self.assertEqual(
                        {
                            hashlib.sha256(lf_bytes).hexdigest(),
                            hashlib.sha256(crlf_bytes).hexdigest(),
                        },
                        set(qa.TRUSTED_SUITE_SHA256[identity]),
                    )
                    suite = next(
                        item for item in qa.TRUSTED_SUITES if item["id"] == identity
                    )
                    target = root / relative_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with mock.patch.object(qa, "TRUSTED_SUITES", (suite,)):
                        for data in (lf_bytes, crlf_bytes):
                            target.write_bytes(data)
                            inventory = qa.trusted_suite_inventory(root)
                            self.assertEqual(
                                hashlib.sha256(data).hexdigest(),
                                inventory[0]["sha256"],
                            )
                        target.write_bytes(lf_bytes + b"\n// changed\n")
                        with self.assertRaisesRegex(
                            qa.BrokerVerificationError,
                            "source drifted",
                        ):
                            qa.trusted_suite_inventory(root)

    def test_node_syntax_treats_app_js_as_candidate_subject(self):
        inventory = qa.trusted_suite_inventory(PRODUCT_QA_ROOT)
        node = next(item for item in inventory if item["id"] == "node-syntax")
        self.assertFalse(node["pin"])
        self.assertEqual(
            qa._sha_file(PRODUCT_QA_ROOT / "app.js"),
            node["sha256"],
        )

    def test_broker_sandbox_covers_artifacts_and_private_acceptance(self):
        source = (ROOT / "lads_automation/qa.py").read_text(encoding="utf-8")
        for required in (
            'private_local_app_data = root / "localappdata"',
            'str(artifact_root)',
            'str(private_local_app_data)',
            '"LOCALAPPDATA": str(private_local_app_data)',
            "before_suite != trusted_baseline",
            "after_suite != trusted_baseline",
            "A prior suite changed trusted harness bytes.",
        ):
            self.assertIn(required, source)


class BrokerPathIsolationTests(unittest.TestCase):
    def test_report_cannot_write_inside_candidate_snapshot(self):
        root = Path(tempfile.gettempdir()).resolve() / "lads-synthetic-path-test"
        candidate = root / "candidate"
        source_copy = root / "source"
        build_copy = root / "build"
        with self.assertRaisesRegex(
            qa.BrokerVerificationError,
            "outside the QA source snapshot",
        ):
            qa._broker_report_destination(
                candidate / "qa-broker-current.json",
                candidate=candidate,
                source_copy=source_copy,
                build_copy=build_copy,
            )

    def test_report_outside_candidate_is_allowed(self):
        root = Path(tempfile.gettempdir()).resolve() / "lads-synthetic-path-test"
        candidate = root / "qa/build-1"
        destination = qa._broker_report_destination(
            root / "qa/qa-broker-current.json",
            candidate=candidate,
            source_copy=root / "private/source",
            build_copy=root / "private/build",
        )
        self.assertEqual(
            destination,
            root / "qa/qa-broker-current.json",
        )


class BrokerVerifierTests(unittest.TestCase):
    def setUp(self):
        self.files = {"app.js": "d" * 64}
        self.suites = [
            {**suite, "sha256": "b" * 64}
            for suite in qa.TRUSTED_SUITES
        ]
        self.patches = (
            mock.patch.object(qa, "source_inventory", return_value=self.files),
            mock.patch.object(qa, "trusted_suite_inventory", return_value=self.suites),
        )
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()

    def verify(self, report):
        return qa.validate_broker_report(
            report,
            candidate_root="C:/synthetic-candidate",
            task_id=TASK,
            source_head=HEAD,
            expected_inventory_sha256=qa.inventory_digest(HEAD, self.files),
        )

    def test_actual_complete_receipts_pass_and_bind_source(self):
        result = self.verify(valid_report(self.files))
        self.assertEqual("PASS", result["status"])
        self.assertEqual(TASK, result["task_id"])

    def test_arbitrary_pass_json_and_simulation_are_rejected(self):
        for report in (
            {"status": "PASS"},
            {**valid_report(self.files), "simulation": True},
        ):
            with self.subTest(report=report):
                with self.assertRaises(qa.BrokerVerificationError):
                    self.verify(report)

    def test_failed_timeout_and_overflow_processes_fail_verification(self):
        mutations = (
            {"status": "failed", "exit_code": 1},
            {"status": "timeout", "timed_out": True},
            {"status": "completed", "exit_code": 0, "output_overflow": True},
        )
        for mutation in mutations:
            report = valid_report(self.files)
            report["suites"][0]["process"].update(mutation)
            report = qa.seal_broker_report(report)
            with self.subTest(mutation=mutation):
                with self.assertRaises(qa.BrokerVerificationError):
                    self.verify(report)

    def test_missing_required_check_cannot_become_pass(self):
        report = valid_report(self.files)
        report["checks"] = [
            row for row in report["checks"] if row["id"] != "json-v11-export-preview-restore"
        ]
        report = qa.seal_broker_report(report)
        with self.assertRaises(qa.BrokerVerificationError):
            self.verify(report)

    def test_future_demo_contract_is_explicitly_opt_in(self):
        report = valid_report(self.files)
        report["required_checks"] = sorted(qa.STAGE3_DEMO_REQUIRED_CHECKS)
        report = qa.seal_broker_report(report)
        with self.assertRaises(qa.BrokerVerificationError):
            self.verify(report)
        report["checks"].append(
            {
                "id": "notes-home-card-contract",
                "status": "PASS",
                "evidence": "Exactly one notes card has the fixed aria-label.",
            }
        )
        notes_suite = {**next(
            suite for suite in qa.TRUSTED_SUITES if suite["id"] == "notes-contract"
        ), "sha256": "b" * 64}
        notes_suite["process"] = {
            "status": "completed",
            "exit_code": 0,
            "timed_out": False,
            "output_overflow": False,
            "output_sha256": "c" * 64,
        }
        report["suites"].append(notes_suite)
        report = qa.seal_broker_report(report)
        self.assertEqual("PASS", self.verify(report)["status"])

    def test_nonzero_qa_verification_maps_to_fail(self):
        self.assertEqual("FAIL", qa.qa_status_from_verification(None))
        self.assertEqual("FAIL", qa.qa_status_from_verification({"status": "ERROR"}))


if __name__ == "__main__":
    unittest.main()
