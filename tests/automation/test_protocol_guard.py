"""In-memory regressions for staged LADS protocol coordinator candidates."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_PATH = ROOT / "engineering" / "lads-protocol-fix" / "lads_orchestrator.py"
HELPER_PATH = ROOT / "engineering" / "lads-protocol-fix" / "lifeos-deepseek.py"
INSTALL_MAP_PATH = ROOT / "engineering" / "lads-protocol-fix" / "install-map.json"
TASK_ID = "dbc36468-9df7-497c-855a-14f1147b60cf"
SOURCE_HEAD = "aea9e758baac88c310319f34a77f94f2364b0c11"
SOURCE_DIGEST = "cdbe41e9fbcfd49233907df09b64f3e05fa0d29e10fa7d34fedb2e3b5d2f697a"
ORIGINAL_ORCHESTRATOR_SHA256 = (
    "7ad445da36aa1924f96c3f4fddb1e65541d81ce065a2e455d6f85c5b25c4d6bb"
)
ORIGINAL_HELPER_SHA256 = (
    "f19a0abf953ff3db591e9f66be77494c4bdaafc1c98ad3b12004994e50abc818"
)
CHILD_TOKEN = "agent" + "_" + "spawn"
COLLAB_TOKEN = "collab" + "_" + "tool"


def load_candidate(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


ORCHESTRATOR = load_candidate("staged_lads_orchestrator", ORCHESTRATOR_PATH)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def report(role="scanner", status="SCAN_COMPLETE"):
    value = {
        "task_id": TASK_ID,
        "role": role,
        "status": status,
        "summary": "in-memory regression",
        "evidence": ["synthetic event stream"],
    }
    if role == "qa-tester":
        value["source_snapshot_sha256"] = SOURCE_DIGEST
    return value


def assistant_item(value=None, **extra):
    text = json.dumps(value) if isinstance(value, dict) else value
    return {"type": "item.completed", "item": {"type": "agent_message", "text": text, **extra}}


def complete_stream(items):
    return [
        {"type": "thread.started", "thread_id": "synthetic-main"},
        *items,
        {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
    ]


def encode(events):
    return "\n".join(json.dumps(event) for event in events)


class HarmlessDataTests(unittest.TestCase):
    def test_marker_samples_in_ordinary_data_are_allowed(self):
        sample = f"{CHILD_TOKEN} and {COLLAB_TOKEN}"
        command = {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "python -c " + repr("print('" + sample + "')"),
                "aggregated_output": sample + "\nordinary output",
                "stdout": sample,
                "stderr": "sample: " + COLLAB_TOKEN,
                "status": "completed",
                "exit_code": 0,
            },
        }
        file_change = {
            "type": "item.completed",
            "item": {
                "type": "file_change",
                "path": "docs/" + CHILD_TOKEN + "-sample.md",
                "diff": "+ordinary example: " + COLLAB_TOKEN,
            },
        }
        parsed, commands, usage = ORCHESTRATOR.parse_event_stream(
            encode(complete_stream([
                assistant_item("source sample: " + sample),
                command,
                file_change,
                assistant_item(report()),
            ]))
        )
        self.assertEqual(TASK_ID, parsed["task_id"])
        self.assertEqual(1, len(commands))
        self.assertEqual(1, usage["output_tokens"])


class NativeControlTests(unittest.TestCase):
    def assert_stream_blocked(self, event):
        with self.assertRaises(ORCHESTRATOR.Blocked):
            ORCHESTRATOR.parse_event_stream(
                encode(complete_stream([
                    event,
                    assistant_item("progress"),
                    assistant_item(report()),
                ]))
            )

    def test_native_call_and_lifecycle_types_are_rejected(self):
        tags = (
            "collab" + "_" + "tool" + "_call",
            "collab" + "_" + "agent" + "_" + "tool" + "_call",
            "collabAgentToolCall",
            "collabToolCall",
            "collaborationToolCall",
            "sub" + "_" + "agent" + "_" + "activity",
            "subAgentActivity",
            "subAgentStarted",
            "subAgentCompleted",
        )
        for tag in tags:
            with self.subTest(tag=tag):
                self.assert_stream_blocked({
                    "type": "item.completed",
                    "item": {"type": tag},
                })

    def test_native_wrapper_type_is_rejected(self):
        self.assert_stream_blocked({"type": "subAgentActivity", "detail": "ordinary text"})

    def test_native_tool_name_tags_are_rejected(self):
        for tool_name in ("spawn_agent", "send_input", "wait", "close_agent"):
            with self.subTest(tool_name=tool_name):
                self.assert_stream_blocked({
                    "type": "item.completed",
                    "item": {"type": "mcp_tool_call", "name": tool_name},
                })

    def test_nested_control_type_is_rejected_inside_normal_item(self):
        self.assert_stream_blocked(assistant_item(
            "progress",
            metadata={"kind": "subAgentActivity", "note": "harmless text"},
        ))

    def test_structural_child_keys_are_rejected_anywhere(self):
        keys = ("child_id", "new_agent_id", "new_thread_id", "agent_id")
        for key in keys:
            with self.subTest(key=key):
                self.assert_stream_blocked(assistant_item("progress", **{key: "synthetic"}))

    def test_unexpected_child_key_on_final_report_item_is_rejected(self):
        with self.assertRaises(ORCHESTRATOR.Blocked):
            ORCHESTRATOR.parse_event_stream(encode(complete_stream([
                assistant_item("progress"),
                assistant_item(report(), child_id="synthetic"),
            ])))


class TerminalIntegrityTests(unittest.TestCase):
    def test_missing_incomplete_duplicate_and_failed_terminals_remain_blocked(self):
        valid = [assistant_item(report())]
        streams = (
            valid,
            [{"type": "turn.started"}],
            [*valid, {"type": "turn.failed"}, {"type": "turn.completed"}],
            [
                *valid,
                {"type": "turn.completed"},
                {"type": "turn.completed"},
            ],
            [
                *complete_stream(valid),
                assistant_item("late data"),
            ],
        )
        for events in streams:
            with self.subTest(types=[event["type"] for event in events]):
                with self.assertRaises(ORCHESTRATOR.Blocked):
                    ORCHESTRATOR.parse_event_stream(encode(events))

    def test_large_legitimate_line_is_bounded_at_one_mib(self):
        self.assertEqual(1024 * 1024, ORCHESTRATOR.MAX_LINE)
        self.assertEqual(8 * 1024 * 1024, ORCHESTRATOR.MAX_CAPTURE)
        legitimate = {
            "type": "thread.started",
            "thread_id": "synthetic-main",
            "metadata": "x" * (300 * 1024),
        }
        parsed, _commands, _usage = ORCHESTRATOR.parse_event_stream(encode([
            legitimate,
            assistant_item(report()),
            {"type": "turn.completed"},
        ]))
        self.assertEqual(TASK_ID, parsed["task_id"])
        event = {"type": "thread.started", "padding": "x" * ORCHESTRATOR.MAX_LINE}
        with self.assertRaises(ORCHESTRATOR.Blocked):
            ORCHESTRATOR.parse_event_stream(json.dumps(event))


class ReportContractTests(unittest.TestCase):
    COMMAND = "python -B -m unittest discover -s tests/automation -v"

    def qa_report(self, status="PASS"):
        value = report("qa-tester", status)
        value["evidence"] = [self.COMMAND]
        return value

    def command_event(self, status="completed", exit_code=0):
        return {"command": self.COMMAND, "status": status, "exit_code": exit_code}

    def test_qa_pass_requires_exact_command_and_exit_zero(self):
        value = ORCHESTRATOR.validate_report(
            self.qa_report(),
            TASK_ID,
            "qa-tester",
            SOURCE_DIGEST,
            (self.COMMAND,),
            (self.command_event(),),
        )
        self.assertEqual("PASS", value["status"])

    def test_qa_terminal_arity_status_and_exit_checks_remain_strict(self):
        invalid_cases = (
            (({"command": self.COMMAND, "status": "completed"},), (self.COMMAND,)),
            ((self.command_event("failed", 0),), (self.COMMAND,)),
            ((self.command_event("completed", 1),), (self.COMMAND,)),
            ((self.command_event(),), (self.COMMAND, "second command")),
            ((self.command_event(), self.command_event()), (self.COMMAND,)),
        )
        for command_events, prescribed in invalid_cases:
            with self.subTest(command_events=command_events, prescribed=prescribed):
                with self.assertRaises(ORCHESTRATOR.Blocked):
                    ORCHESTRATOR.validate_report(
                        self.qa_report(),
                        TASK_ID,
                        "qa-tester",
                        SOURCE_DIGEST,
                        prescribed,
                        command_events,
                    )

    def test_qa_fail_requires_nonzero_command_evidence(self):
        with self.assertRaises(ORCHESTRATOR.Blocked):
            ORCHESTRATOR.validate_report(
                self.qa_report("FAIL"),
                TASK_ID,
                "qa-tester",
                SOURCE_DIGEST,
                (self.COMMAND,),
                (self.command_event(),),
            )
        validated = ORCHESTRATOR.validate_report(
            self.qa_report("FAIL"),
            TASK_ID,
            "qa-tester",
            SOURCE_DIGEST,
            (self.COMMAND,),
            (self.command_event("failed", 1),),
        )
        self.assertEqual("FAIL", validated["status"])

    def test_report_output_schema_is_simple_and_role_specific(self):
        for role in ORCHESTRATOR.STATUSES:
            with self.subTest(role=role):
                schema = ORCHESTRATOR.report_output_schema(role)
                self.assertEqual("object", schema["type"])
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(set(schema["required"]), set(schema["properties"]))
                self.assertEqual([role], schema["properties"]["role"]["enum"])
                self.assertEqual("string", schema["properties"]["status"]["type"])
                self.assertEqual(
                    sorted(ORCHESTRATOR.STATUSES[role]),
                    schema["properties"]["status"]["enum"],
                )
                for name, value in schema["properties"].items():
                    self.assertIn("type", value, name)
                    if value["type"] == "array":
                        self.assertEqual("string", value["items"]["type"])
                self.assertEqual(
                    role == "qa-tester",
                    "source_snapshot_sha256" in schema["properties"],
                )
                serialized = json.dumps(schema)
                for unsupported in ("allOf", "if", "then"):
                    self.assertNotIn(unsupported, serialized)


class HelperAndMapTests(unittest.TestCase):
    def test_helper_prefix_disables_both_native_features_without_execution(self):
        tree = ast.parse(HELPER_PATH.read_text(encoding="utf-8"))
        command_function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "command"
        )
        constants = {
            node.value for node in ast.walk(command_function)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertIn("multi_agent", constants)
        self.assertIn("multi_agent_v2", constants)
        self.assertIn("--sandbox", constants)
        self.assertIn("--ask-for-approval", constants)
        self.assertIn("lifeos_deepseek", constants)
        self.assertGreaterEqual(
            sum(node.value == "--disable" for node in ast.walk(command_function)
                if isinstance(node, ast.Constant)),
            4,
        )

    def test_install_map_binds_exact_two_targets(self):
        install_map = json.loads(INSTALL_MAP_PATH.read_text(encoding="utf-8"))
        targets = install_map["targets"]
        self.assertEqual(2, len(targets))
        by_source = {item["source_path"]: item for item in targets}
        self.assertEqual(
            {
                "scripts/lads_orchestrator.py",
                "scripts/lifeos-deepseek.py",
            },
            set(by_source),
        )
        expected = {
            "scripts/lads_orchestrator.py": (
                ORIGINAL_ORCHESTRATOR_SHA256,
                "engineering/lads-protocol-fix/lads_orchestrator.py",
            ),
            "scripts/lifeos-deepseek.py": (
                ORIGINAL_HELPER_SHA256,
                "engineering/lads-protocol-fix/lifeos-deepseek.py",
            ),
        }
        for source_path, (original_sha, candidate_path) in expected.items():
            item = by_source[source_path]
            candidate = ROOT / candidate_path
            self.assertEqual(SOURCE_HEAD, item["source_head"])
            self.assertEqual(original_sha, item["original_sha256"])
            self.assertEqual(candidate_path, item["candidate_path"])
            self.assertEqual(sha256(candidate), item["candidate_sha256"])


if __name__ == "__main__":
    unittest.main()
