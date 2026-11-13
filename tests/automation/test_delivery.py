"""Memory/static tests for Stage 3 MAIN-ACK and delivery finalization."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from lads_automation import delivery
from lads_automation.engine import DeliveryError

TASK = "ac4f4d74-9aaf-43b2-b40a-e988c45e682f"
HEAD = "a" * 40
OLD = {"app.js": "b" * 64}
NEW = {"app.js": "c" * 64}


def stage(role, status, *, path, sha, build=1, commands=None):
    value = {
        "task_id": TASK,
        "role": role,
        "status": status,
        "build": build,
        "exit_code": 0,
        "metadata": {
            "role": role,
            "provider": "lifeos_deepseek",
            "model": "deepseek-flash",
            "preflight_proof": {"role": role, "exit_code": 0},
        },
        "report": {
            "task_id": TASK,
            "role": role,
            "status": status,
            "summary": "synthetic",
            "evidence": ["synthetic"],
            **({"source_snapshot_sha256": delivery.qa.inventory_digest(HEAD, NEW)} if role == "qa-tester" else {}),
        },
    }
    if commands is not None:
        value["commands"] = commands
    return value


def worker_metadata(role, *, token_base=1):
    return {
        "role": role,
        "provider": "lifeos_deepseek",
        "model": "deepseek-flash",
        "sandbox": "workspace-write" if role == "builder" else "read-only",
        "start_time": "2026-11-04T00:00:00+00:00",
        "end_time": "2026-11-04T00:00:01+00:00",
        "input_tokens": token_base,
        "output_tokens": token_base + 1,
        "config_snapshot_sha256": "1" * 64,
        "preflight_proof": {"role": role, "exit_code": 0},
        "runtime_home": "C:/synthetic/runtime",
        "home_mode": "original",
        "home_sha256": "2" * 64,
        "authority_sha256": "3" * 64,
        "main_config_guard_events": [],
    }


def document_receipt(document, before, after):
    body = {
        "document": document,
        "before_sha256": before,
        "after_sha256": after,
        "event_ids": ["8" * 8 + "-8888-4888-8888-" + "8" * 12],
    }
    return {
        **body,
        "receipt_sha256": delivery._digest({"task_id": TASK, **body}),
        "idempotent": False,
        "text": "synthetic",
    }


class BundleValidationTests(unittest.TestCase):
    def setUp(self):
        self.expected = delivery.unit_qa.STAGE4_QA_COMMANDS

    def test_stage_worker_rejects_missing_or_nonzero_verifier(self):
        qa_stage = stage(
            "qa-tester",
            "PASS",
            path="stages/qa.json",
            sha="d" * 64,
            commands=[
                {"command": command, "status": "completed", "exit_code": 0}
                for command in self.expected[:-1]
            ],
        )
        self.assertEqual(2, len(qa_stage["commands"]))
        self.assertNotEqual(set(self.expected), {
            item["command"] for item in qa_stage["commands"]
        })
        with self.assertRaisesRegex(delivery.Stage3DeliveryError, "known contract"):
            delivery._qa_contract(qa_stage["commands"])

    def test_delivery_distinguishes_exact_legacy_and_v2_command_tuples(self):
        def rows(commands):
            return [
                {"command": command, "status": "completed", "exit_code": 0}
                for command in commands
            ]

        legacy_rows = rows(delivery.unit_qa.LEGACY_STAGE3_QA_COMMANDS)
        v2_rows = rows(delivery.unit_qa.STAGE4_QA_COMMANDS)
        self.assertEqual(
            (1, delivery.unit_qa.LEGACY_STAGE3_QA_COMMANDS),
            delivery._qa_contract(legacy_rows),
        )
        self.assertEqual(
            (2, delivery.unit_qa.STAGE4_QA_COMMANDS),
            delivery._qa_contract(v2_rows),
        )
        delivery._validate_qa_executions(
            legacy_rows,
            delivery.unit_qa.LEGACY_STAGE3_QA_COMMANDS,
        )
        delivery._validate_qa_executions(
            v2_rows,
            delivery.unit_qa.STAGE4_QA_COMMANDS,
        )
        failed = [dict(item) for item in v2_rows]
        failed[0]["exit_code"] = 1
        with self.assertRaisesRegex(delivery.Stage3DeliveryError, "integer-zero"):
            delivery._validate_qa_executions(
                failed,
                delivery.unit_qa.STAGE4_QA_COMMANDS,
            )
        reordered = [v2_rows[1], v2_rows[0], v2_rows[2]]
        with self.assertRaisesRegex(delivery.Stage3DeliveryError, "known contract"):
            delivery._qa_contract(reordered)

    def test_preflight_requires_assigned_worker_identity(self):
        item = stage("reviewer", "APPROVED", path="stages/reviewer.json", sha="e" * 64)
        with self.assertRaisesRegex(delivery.Stage3DeliveryError, "assigned DeepSeek"):
            delivery._validate_preflight(
                {
                    **item,
                    "metadata": {
                        **item["metadata"],
                        "provider": "openai",
                    },
                },
                "reviewer",
            )

    def test_main_ack_binds_exact_handoff_and_candidate(self):
        bundle = mock.Mock()
        bundle.task_id = TASK
        bundle.run_root = Path("C:/synthetic-run")
        bundle.candidate_sha256 = "f" * 64
        bundle.source_head = HEAD
        bundle.handoff_sha256 = "1" * 64
        with mock.patch.object(delivery, "_sha_file", return_value="1" * 64):
            value = delivery.create_main_ack(bundle)
        self.assertEqual("ACKNOWLEDGED", value["status"])
        self.assertEqual("1" * 64, value["handoff_sha256"])
        self.assertEqual(
            "controller_action_only_not_deploy_push_or_merge",
            value["authorization"],
        )

    def test_event_plan_contains_evidence_and_never_claims_commit(self):
        bundle = mock.Mock()
        bundle.task_id = TASK
        bundle.source_head = HEAD
        bundle.candidate_sha256 = "f" * 64
        bundle.changed_files = ({"path": "index.html"},)
        bundle.reviewer_stage_sha256 = "2" * 64
        bundle.expected_commands = self.expected
        bundle.handoff = {"source_head": HEAD}
        bundle.qa_contract_version = 2
        bundle.unit_evidence = {"evidence_sha256": "3" * 64}
        plan = delivery.standard_event_plan(bundle, reason="synthetic")
        details = plan[0]["payload"]["details"]
        self.assertIn(TASK, details)
        self.assertIn('"working_content_commit":null', details)
        self.assertIn('"unit_evidence_sha256":"' + "3" * 64 + '"', details)
        self.assertEqual(["task_state_changed", "task_delivered"], [item["event_type"] for item in plan])
        bundle.qa_contract_version = 3
        split_plan = delivery.standard_event_plan(bundle, reason="actual split contract metadata")
        import json
        self.assertEqual(3, json.loads(split_plan[0]["payload"]["details"])["qa_contract_version"])
        bundle.qa_contract_version = 2
        all_events = delivery.standard_event_plan(bundle, reason="Explicit synthetic mapping coverage",
                                                 event_types=tuple(delivery.EVENT_DOCUMENTS))
        self.assertEqual(10, len(all_events))
        for event_types in (("unknown",), ("task_delivered", "task_delivered"), (), "task_delivered"):
            with self.assertRaises(delivery.Stage3DeliveryError):
                delivery.standard_event_plan(bundle, reason="synthetic", event_types=event_types)
        with self.assertRaises(delivery.Stage3DeliveryError):
            delivery.standard_event_plan(bundle, reason=" ")

    def test_all_actual_worker_stages_become_bounded_receipts(self):
        loaded = []
        role_runs = []
        for index, role in enumerate(
            ("scanner", "reviewer", "builder", "qa-tester", "reviewer"),
            start=1,
        ):
            if index == 2:
                status = "APPROVED"
            elif index == 5:
                status = "APPROVED"
            elif role == "qa-tester":
                status = "PASS"
            else:
                status = "SCAN_COMPLETE" if role == "scanner" else "BUILT"
            metadata = worker_metadata(role, token_base=index)
            role_runs.append(metadata)
            build = 0 if index == 2 else 1
            loaded.append(
                (
                    index,
                    {
                        "task_id": TASK,
                        "role": role,
                        "status": status,
                        "build": build,
                        "exit_code": 0,
                        "metadata": metadata,
                        "report": (
                            {
                                "task_id": TASK,
                                "role": role,
                                "status": status,
                                "source_snapshot_sha256": "f" * 64,
                            }
                            if role == "qa-tester"
                            else {"task_id": TASK, "role": role, "status": status}
                        ),
                    },
                    f"stages/{index:04d}-{role}.json",
                    str(index) * 64,
                )
            )
        records = delivery._worker_stage_records(
            loaded,
            role_runs,
            task_id=TASK,
            candidate_sha256="f" * 64,
            final_build=1,
        )
        self.assertEqual(5, len(records))
        self.assertEqual(
            {"scanner", "reviewer", "builder", "qa-tester"},
            {item["role"] for item in records},
        )
        self.assertEqual(3, records[2]["stage_metadata"]["input_tokens"])
        self.assertEqual("stages/0001-scanner.json", records[0]["stage_ref"]["path"])
        self.assertEqual("f" * 64, records[3]["candidate_sha256"])
        self.assertEqual("f" * 64, records[4]["candidate_sha256"])
        self.assertIsNone(records[0]["candidate_sha256"])
        self.assertIsNone(records[1]["candidate_sha256"])
        serialized = repr(records)
        self.assertNotIn("stdout", serialized)
        self.assertNotIn("prompt", serialized.casefold())

    def test_index_failed_retry_allows_only_exact_publisher_document_state(self):
        before = "a" * 64
        after = "b" * 64
        base = {"app.js": "c" * 64, "docs/STATUS.md": before}
        state = {
            "documents_receipt": {
                "documents": {
                    "docs/STATUS.md": document_receipt(
                        "docs/STATUS.md",
                        before,
                        after,
                    )
                }
            }
        }
        expected = delivery._document_published_files(
            base,
            state,
            task_id=TASK,
        )
        self.assertEqual(
            {"app.js": "c" * 64, "docs/STATUS.md": after},
            expected,
        )
        receipt = state["documents_receipt"]["documents"]["docs/STATUS.md"]
        durable = {
            "document": "docs/STATUS.md",
            "before_sha256": before,
            "after_sha256": after,
            "event_ids": receipt["event_ids"],
            "receipt_sha256": receipt["receipt_sha256"],
        }
        events = [
            {
                "task_id": TASK,
                "event_type": "document_write_completed",
                "payload": durable,
            }
        ]
        self.assertTrue(
            delivery._publisher_events_match(state, events, task_id=TASK)
        )
        forged_events = [
            {
                **events[0],
                "payload": {**durable, "after_sha256": "f" * 64},
            }
        ]
        self.assertFalse(
            delivery._publisher_events_match(state, forged_events, task_id=TASK)
        )
        unrelated = {**expected, "app.js": "d" * 64}
        self.assertNotEqual(expected, unrelated)
        forged = {
            "documents_receipt": {
                "documents": {
                    "docs/STATUS.md": {
                        **state["documents_receipt"]["documents"]["docs/STATUS.md"],
                        "after_sha256": "e" * 64,
                    }
                }
            }
        }
        self.assertIsNone(
            delivery._document_published_files(base, forged, task_id=TASK)
        )


class EngineFlowTests(unittest.TestCase):
    def test_retry_requires_reason_and_does_not_mask_engine_failure(self):
        engine = mock.Mock()
        engine.retry.side_effect = DeliveryError("persisted state")
        with self.assertRaises(DeliveryError):
            delivery.retry_handoff(engine, reason="actual retry")
        with self.assertRaises(delivery.Stage3DeliveryError):
            delivery.retry_handoff(engine, reason="")


if __name__ == "__main__":
    unittest.main()
