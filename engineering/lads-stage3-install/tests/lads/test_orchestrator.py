from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import uuid


ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lads = load_module("lads_orchestrator_test", ROOT / "scripts" / "lads_orchestrator.py")


def make_repo(root, initial="bad"):
    root = Path(root)
    root.mkdir(parents=True)
    (root / ".gitignore").write_text("work/\n__pycache__/\n", encoding="utf-8")
    expression = "a + b + 1" if initial == "bad" else "a + b"
    (root / "add.py").write_text(f"def add(a, b):\n    return {expression}\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_add.py").write_text(
        "import unittest\nfrom add import add\n\n"
        "class AddTests(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(root), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@invalid.local"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "baseline"], check=True, capture_output=True)
    manifest_path = root.parent / "manifest.json"
    manifest_path.write_text(json.dumps({
        "repo": str(root),
        "task": "Repair add so it returns a+b.",
        "allowed_paths": ["add.py"],
        "test_commands": ["python -B -m unittest discover -s tests -v"],
        "applicability": {"browser": False, "multiuser": False},
    }), encoding="utf-8")
    return lads.load_manifest(manifest_path)


def worker_result(task_id, role, status, source_digest=None, commands=None, evidence=None):
    report = {
        "task_id": task_id,
        "role": role,
        "status": status,
        "summary": f"{role} {status}",
        "evidence": evidence or ["actual test evidence"],
    }
    if role == "qa-tester":
        report["source_snapshot_sha256"] = source_digest
    proof = synthetic_preflight_proof(role)
    return lads.WorkerResult(
        role=role,
        report=report,
        commands=commands or [],
        metadata={
            "role": role,
            "provider": "lifeos_deepseek",
            "model": "deepseek-flash",
            "start_time": lads.now(),
            "end_time": lads.now(),
            "input_tokens": None,
            "output_tokens": None,
            "preflight_proof": proof,
        },
        output_sha256="0" * 64,
    )


def synthetic_preflight_proof(role, workspace=ROOT):
    mode = "workspace-write" if role == "builder" else "read-only"
    prefix = ["codex", "--sandbox", mode, "--ask-for-approval", "never"]
    debug = [*prefix, "debug", "prompt-input", "LADS effective permission preflight"]
    return {
        "role": role,
        "home_mode": "frozen",
        "debug_argv": debug,
        "worker_prefix_length": len(prefix),
        "worker_prefix_sha256": lads.sha_bytes(lads.canonical(prefix)),
        "exit_code": 0,
        "stdout_sha256": "b" * 64,
        "permissions": {
            "role": role,
            "sandbox_mode": mode,
            "network": "restricted",
            "approval": "never",
            "writable_roots": [str(Path(workspace).resolve())] if role == "builder" else [],
        },
        "config_sha256": {"synthetic-profile": "c" * 64},
        "runtime_home": str(Path(workspace).resolve()),
        "home_sha256": "a" * 64,
        "authority_sha256": "d" * 64,
    }


def synthetic_main_snapshot(sha256="e" * 64):
    return {
        "present": True,
        "sha256": sha256,
        "project_entries": {"1" * 64: "2" * 64},
        "project_keys": {"1" * 64: "4" * 64},
        "non_projects_sha256": "3" * 64,
    }


def make_directory_link(link, target):
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        if os.name != "nt":
            raise
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise OSError("directory junction creation unavailable")


def write_origin_authority(home):
    home = Path(home)
    home.mkdir(parents=True)
    catalog = home / "models.json"
    catalog.write_text(
        json.dumps({"models": [{"slug": "deepseek-flash"}]}),
        encoding="utf-8",
    )
    for role in ("scanner", "reviewer", "builder", "qa-tester"):
        key = "DEEPSEEK_QA_API_KEY" if role == "qa-tester" else (
            "DEEPSEEK_" + role.upper() + "_API_KEY"
        )
        mode = "workspace-write" if role == "builder" else "read-only"
        (home / (role + "-deepseek.config.toml")).write_text(
            f'model = "deepseek-flash"\n'
            f'model_provider = "lifeos_deepseek"\n'
            f'model_catalog_json = {json.dumps(str(catalog))}\n'
            f'sandbox_mode = "{mode}"\n'
            f'approval_policy = "never"\n'
            f'[model_providers.lifeos_deepseek]\n'
            f'name = "DeepSeek"\n'
            f'base_url = "https://api.deepseek.com"\n'
            f'wire_api = "responses"\n'
            f'env_key = "{key}"\n'
            f'requires_openai_auth = false\n',
            encoding="utf-8",
        )
    (home / "config.toml").write_text('model = "private-origin-placeholder"\n', encoding="utf-8")
    (home / "auth.json").write_text('{"private": "DO_NOT_COPY_AUTH_SENTINEL"}', encoding="utf-8")
    sessions = home / "sessions"
    sessions.mkdir()
    (sessions / "private.jsonl").write_text("DO_NOT_COPY_SESSION_SENTINEL", encoding="utf-8")
    return home, catalog


class FakeWorker:
    def __init__(self, repair=True):
        self.repair = repair
        self.calls = []
        self._main_config_snapshot = synthetic_main_snapshot()

    def main_config_snapshot(self):
        return dict(self._main_config_snapshot)

    def main_config_transition(self, expected, actual):
        if expected == actual:
            return {
                "status": "UNCHANGED",
                "before_present": expected["present"],
                "before_sha256": expected["sha256"],
                "after_present": actual["present"],
                "after_sha256": actual["sha256"],
                "allowed_project_root_names": [str(ROOT)],
                "added_project_root_names": [],
            }
        raise lads.Blocked("main Codex config raw SHA changed between stages")

    def main_project_roots(self):
        return (str(ROOT),)

    def run(self, role, workspace, prompt, task_id, timeout, source_digest=None, test_commands=()):
        self.calls.append((role, prompt))
        if role == "scanner":
            return worker_result(task_id, role, "SCAN_COMPLETE")
        if role == "builder":
            if self.repair:
                (Path(workspace) / "add.py").write_text(
                    "def add(a, b):\n    return a + b\n", encoding="utf-8"
                )
            return worker_result(task_id, role, "BUILT")
        if role == "qa-tester":
            command = test_commands[0]
            completed = subprocess.run(
                command,
                cwd=workspace,
                shell=True,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
            status = "PASS" if completed.returncode == 0 else "FAIL"
            commands = [{"command": command, "status": "completed", "exit_code": completed.returncode}]
            return worker_result(task_id, role, status, source_digest, commands, [command])
        if role == "reviewer":
            if "Scanner report:" in prompt:
                return worker_result(task_id, role, "APPROVED")
            if '"status": "PASS"' in prompt:
                return worker_result(task_id, role, "APPROVED")
            return worker_result(task_id, role, "CHANGES_REQUESTED")
        raise AssertionError(role)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.task_id = str(uuid.uuid4())
        self.digest = "a" * 64

    def test_strict_report_roles_and_foreign_id(self):
        report = {
            "task_id": self.task_id,
            "role": "scanner",
            "status": "SCAN_COMPLETE",
            "summary": "scanned",
            "evidence": ["read source"],
        }
        self.assertEqual(report, lads.validate_report(report, self.task_id, "scanner"))
        for field, value in (("task_id", str(uuid.uuid4())), ("role", "reviewer"), ("status", "PASS")):
            changed = dict(report)
            changed[field] = value
            with self.assertRaises(lads.Blocked):
                lads.validate_report(changed, self.task_id, "scanner")

    def test_report_rejects_extra_or_secretlike_fields(self):
        report = {
            "task_id": self.task_id,
            "role": "builder",
            "status": "BUILT",
            "summary": "built",
            "evidence": ["tests"],
            "extra": True,
        }
        with self.assertRaises(lads.Blocked):
            lads.validate_report(report, self.task_id, "builder")
        report.pop("extra")
        report["evidence"] = ["DEEPSEEK_QA_API_KEY=not-a-real-key"]
        with self.assertRaises(lads.Blocked):
            lads.validate_report(report, self.task_id, "builder")

    def test_parse_last_assistant_and_completion(self):
        report = {
            "task_id": self.task_id,
            "role": "scanner",
            "status": "SCAN_COMPLETE",
            "summary": "ok",
            "evidence": ["read"],
        }
        events = [
            {"type": "thread.started", "thread_id": "independent"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": '{"progress":true}'}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report)}},
            {"type": "turn.completed", "usage": {"input_tokens": 2, "output_tokens": 3}},
        ]
        parsed, commands, usage = lads.parse_event_stream("\n".join(map(json.dumps, events)))
        self.assertEqual(report, parsed)
        self.assertEqual([], commands)
        self.assertEqual(3, usage["output_tokens"])
        with self.assertRaises(lads.Blocked):
            lads.parse_event_stream("\n".join(map(json.dumps, events[:-1])))
        for trailing in (
            {"type": "item.completed", "item": {"type": "agent_message", "text": '{"late":true}'}},
            {"type": "item.completed", "item": {"type": "command_execution", "command": "late"}},
            {"type": "error", "message": "late"},
        ):
            with self.assertRaises(lads.Blocked):
                lads.parse_event_stream("\n".join(map(json.dumps, events + [trailing])))
        with self.assertRaises(lads.Blocked):
            lads.parse_event_stream("\n".join(map(json.dumps, [{"type": "turn.failed"}] + events)))
        with self.assertRaises(lads.Blocked):
            lads.parse_event_stream(json.dumps({"type": "thread.started", "padding": "x" * lads.MAX_LINE}))

    def test_native_child_evidence_is_rejected(self):
        event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "{}", "child_id": "abc"},
        }
        with self.assertRaises(lads.Blocked):
            lads.parse_event_stream(json.dumps(event))

    def test_qa_pass_requires_actual_command_and_exit_zero(self):
        command = "python -B -m unittest discover -s tests -v"
        report = {
            "task_id": self.task_id,
            "role": "qa-tester",
            "status": "PASS",
            "summary": "tests pass",
            "evidence": [command],
            "source_snapshot_sha256": self.digest,
        }
        good = [{"command": command, "status": "completed", "exit_code": 0}]
        self.assertEqual(
            report,
            lads.validate_report(report, self.task_id, "qa-tester", self.digest, [command], good),
        )
        with self.assertRaises(lads.Blocked):
            lads.validate_report(report, self.task_id, "qa-tester", self.digest, [command], [])
        failed = [{"command": command, "status": "completed", "exit_code": 1}]
        with self.assertRaises(lads.Blocked):
            lads.validate_report(report, self.task_id, "qa-tester", self.digest, [command], failed)

    def test_qa_rejects_claim_echo_and_contradictory_fail(self):
        command = "python -B -m unittest discover -s tests -v"
        report = {
            "task_id": self.task_id,
            "role": "qa-tester",
            "status": "FAIL",
            "summary": "tests failed",
            "evidence": [command],
            "source_snapshot_sha256": self.digest,
        }
        wrapped = [{
            "command": 'powershell.exe -NoProfile -Command "' + command + '"',
            "status": "completed",
            "exit_code": 1,
        }]
        self.assertEqual(
            report,
            lads.validate_report(report, self.task_id, "qa-tester", self.digest, [command], wrapped),
        )
        for events in (
            [{"command": "echo " + command, "status": "completed", "exit_code": 1}],
            [{"command": command, "status": "completed", "exit_code": 0}],
        ):
            with self.assertRaises(lads.Blocked):
                lads.validate_report(report, self.task_id, "qa-tester", self.digest, [command], events)

    def test_qa_terminal_failed_status_and_mixed_suite(self):
        commands = [
            "python -B -m unittest discover -s tests -v",
            "python -B -m unittest discover -s tests/unit -v",
        ]
        report = {
            "task_id": self.task_id,
            "role": "qa-tester",
            "status": "FAIL",
            "summary": "one command failed",
            "evidence": commands,
            "source_snapshot_sha256": self.digest,
        }
        mixed = [
            {"command": commands[0], "status": "completed", "exit_code": 0},
            {"command": commands[1], "status": "failed", "exit_code": 1},
        ]
        self.assertEqual(
            report,
            lads.validate_report(report, self.task_id, "qa-tester", self.digest, commands, mixed),
        )
        self.assertEqual(
            report,
            lads.validate_report(
                report,
                self.task_id,
                "qa-tester",
                self.digest,
                commands,
                list(reversed(mixed)),
            ),
        )

        for bad in (
            [{"command": commands[0], "status": "failed", "exit_code": 0},
             {"command": commands[1], "status": "completed", "exit_code": 0}],
            [{"command": commands[0], "status": "running", "exit_code": None},
             {"command": commands[1], "status": "failed", "exit_code": 1}],
            [{"command": commands[0], "status": "failed", "exit_code": True},
             {"command": commands[1], "status": "failed", "exit_code": 1}],
            [{"command": commands[0], "status": "failed", "exit_code": 1}],
            [{"command": commands[0], "status": "failed", "exit_code": 1},
             {"command": commands[1], "status": "failed", "exit_code": 1},
             {"command": "echo extra", "status": "completed", "exit_code": 0}],
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(lads.Blocked):
                    lads.validate_report(report, self.task_id, "qa-tester", self.digest, commands, bad)

        passed = dict(report, status="PASS")
        for bad in (
            [{"command": commands[0], "status": "failed", "exit_code": 1},
             {"command": commands[1], "status": "completed", "exit_code": 0}],
            [{"command": commands[0], "status": "completed", "exit_code": 0},
             {"command": commands[1], "status": "completed", "exit_code": 0},
             {"command": "echo extra", "status": "completed", "exit_code": 0}],
        ):
            with self.subTest(pass_bad=bad):
                with self.assertRaises(lads.Blocked):
                    lads.validate_report(passed, self.task_id, "qa-tester", self.digest, commands, bad)

    def test_actual_cli_failed_completed_event_maps_to_qa_fail(self):
        command = "python -B -m unittest discover -s tests -v"
        report = {
            "task_id": self.task_id,
            "role": "qa-tester",
            "status": "FAIL",
            "summary": "the executed suite failed",
            "evidence": [command],
            "source_snapshot_sha256": self.digest,
        }
        events = [
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": command,
                    "status": "failed",
                    "exit_code": 1,
                },
            },
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": json.dumps(report)},
            },
            {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
        ]
        parsed, commands, _usage = lads.parse_event_stream("\n".join(map(json.dumps, events)))
        self.assertEqual("failed", commands[0]["status"])
        self.assertEqual(1, commands[0]["exit_code"])
        self.assertEqual(
            report,
            lads.validate_report(parsed, self.task_id, "qa-tester", self.digest, [command], commands),
        )

    def test_qa_digest_mismatch_is_blocked(self):
        report = {
            "task_id": self.task_id,
            "role": "qa-tester",
            "status": "FAIL",
            "summary": "failed",
            "evidence": ["python -B -m unittest discover -s tests -v"],
            "source_snapshot_sha256": self.digest,
        }
        commands = [{"command": "python -B -m unittest discover -s tests -v", "status": "completed", "exit_code": 1}]
        with self.assertRaises(lads.Blocked):
            lads.validate_report(
                report,
                self.task_id,
                "qa-tester",
                "b" * 64,
                ["python -B -m unittest discover -s tests -v"],
                commands,
            )

    def test_non_qa_snapshot_digest_is_rejected(self):
        report = {
            "task_id": self.task_id,
            "role": "reviewer",
            "status": "APPROVED",
            "summary": "approved",
            "evidence": ["reviewed diff"],
            "source_snapshot_sha256": self.digest,
        }
        with self.assertRaises(lads.Blocked):
            lads.validate_report(report, self.task_id, "reviewer")


class PermissionPreflightTests(unittest.TestCase):
    READONLY_BLOCK = (
        "<permissions instructions>\n"
        "Filesystem sandboxing defines which files can be read or written. "
        "`sandbox_mode` is `read-only`: The sandbox only permits reading files. "
        "Network access is restricted.\n"
        "Approval policy is currently never. Do not provide the `sandbox_permissions` "
        "for any reason, commands will be rejected.\n"
        "</permissions instructions>"
    )

    @staticmethod
    def message(texts, role="developer", content_type="input_text"):
        return {
            "type": "message",
            "id": None,
            "role": role,
            "content": [{"type": content_type, "text": text} for text in texts],
            "internal_chat_message_metadata_passthrough": None,
        }

    def prompt(self, *messages):
        return json.dumps(list(messages), ensure_ascii=False)

    def builder_block(self, workspace):
        root = str(Path(workspace).resolve()).replace("/", "\\")
        return (
            "<permissions instructions>\n"
            "Filesystem sandboxing defines which files can be read or written. "
            "`sandbox_mode` is `workspace-write`: The sandbox permits reading files, and "
            "editing files in `cwd` and `writable_roots`. Network access is restricted.\n"
            "Approval policy is currently never. Do not provide the `sandbox_permissions` "
            "for any reason, commands will be rejected.\n"
            f" The writable root is `{root}`.\n"
            "</permissions instructions>"
        )

    def test_actual_serialized_permission_blocks_are_parsed_exactly(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            readonly_stdout = self.prompt(self.message([self.READONLY_BLOCK]))
            builder_stdout = self.prompt(self.message([self.builder_block(workspace)]))
            self.assertIn("\\n", readonly_stdout)
            self.assertIn("\\\\", builder_stdout)
            readonly = lads.verify_permission_probe(readonly_stdout, "reviewer", workspace)
            builder = lads.verify_permission_probe(builder_stdout, "builder", workspace)
            self.assertEqual("read-only", readonly["sandbox_mode"])
            self.assertEqual("workspace-write", builder["sandbox_mode"])
            self.assertEqual("restricted", builder["network"])
            self.assertEqual("never", builder["approval"])

    def test_permission_probe_rejects_duplicate_and_split_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            opening, closing_marker, _ = self.READONLY_BLOCK.partition("</permissions instructions>")
            split = (opening, closing_marker)
            cases = (
                self.prompt(self.message([self.READONLY_BLOCK + "\n" + self.READONLY_BLOCK])),
                self.prompt(
                    self.message([self.READONLY_BLOCK]),
                    self.message([self.READONLY_BLOCK]),
                ),
                self.prompt(self.message(split)),
                self.prompt(
                    self.message([split[0]]),
                    self.message([split[1]]),
                ),
            )
            for stdout in cases:
                with self.subTest(stdout=stdout[:160]):
                    with self.assertRaises(lads.Blocked):
                        lads.verify_permission_probe(stdout, "reviewer", workspace)

    def test_user_role_permission_text_is_never_permission_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            fake_user = self.READONLY_BLOCK.replace(
                "`sandbox_mode` is `read-only`",
                "`sandbox_mode` is `workspace-write`",
            )
            valid_developer = self.prompt(
                self.message([self.READONLY_BLOCK]),
                self.message([fake_user], role="user"),
            )
            lads.verify_permission_probe(valid_developer, "reviewer", workspace)
            user_only = self.prompt(self.message([self.READONLY_BLOCK], role="user"))
            with self.assertRaises(lads.Blocked):
                lads.verify_permission_probe(user_only, "reviewer", workspace)

    def test_permission_probe_rejects_malformed_wrong_shape_and_multi_document(self):
        serialized = self.prompt(self.message([self.READONLY_BLOCK]))
        wrong_shapes = (
            "{",
            self.READONLY_BLOCK,
            serialized + "\n" + serialized,
            json.dumps({"type": "message", "role": "developer", "content": []}),
            json.dumps([1]),
            json.dumps([{"type": "message", "role": "developer", "content": "bad"}]),
            json.dumps([{"type": "other", "role": "developer", "content": []}]),
            json.dumps([{"type": "message", "role": "tool", "content": []}]),
            json.dumps([{
                "type": "message",
                "role": "developer",
                "content": [{"type": "output_text", "text": self.READONLY_BLOCK}],
            }]),
            json.dumps([{
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": None}],
            }]),
            json.dumps([self.message(["not a permission block"])]),
        )
        for stdout in wrong_shapes:
            with self.subTest(stdout=stdout[:160]):
                with self.assertRaises(lads.Blocked):
                    lads.verify_permission_probe(stdout, "reviewer", ROOT)

    def test_permission_preflight_rejects_extra_plural_and_same_prefix_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            base = self.builder_block(workspace)
            root = str(workspace.resolve()).replace("/", "\\")
            cases = (
                base.replace(
                    "</permissions instructions>",
                    f" The writable root is `{workspace / 'second'}`.\n</permissions instructions>",
                ),
                base.replace("The writable root is", "The writable roots are"),
                base.replace(f"`{root}`", f"`{workspace / 'different'}`"),
                base.replace(f"`{root}`", f"`{str(workspace)}-suffix`"),
            )
            for block in cases:
                with self.subTest(block=block[:160]):
                    with self.assertRaises(lads.Blocked):
                        lads.verify_permission_probe(
                            self.prompt(self.message([block])),
                            "builder",
                            workspace,
                        )

    def test_permission_preflight_rejects_mode_network_never_and_missing_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            cases = (
                (
                    "reviewer",
                    self.READONLY_BLOCK.replace("only permits reading files", "also writes"),
                ),
                (
                    "reviewer",
                    self.READONLY_BLOCK.replace(
                        "</permissions instructions>",
                        f"The writable root is `{workspace}`.\n</permissions instructions>",
                    ),
                ),
                (
                    "reviewer",
                    self.READONLY_BLOCK.replace(
                        "</permissions instructions>",
                        f"The writable roots are `{workspace}`.\n</permissions instructions>",
                    ),
                ),
                ("builder", self.builder_block(Path(temporary))),
                (
                    "builder",
                    self.builder_block(workspace).replace(
                        "Network access is restricted.",
                        "Network access is restricted. Network access is enabled.",
                    ),
                ),
                (
                    "builder",
                    self.builder_block(workspace).replace(
                        "Approval policy is currently never.",
                        "Approval policy is currently never. Approval policy is currently on-request.",
                    ),
                ),
                (
                    "builder",
                    self.builder_block(workspace).replace(
                        "Approval policy is currently never",
                        "approval is on-request",
                    ),
                ),
                (
                    "builder",
                    self.builder_block(workspace).replace("Network access is restricted.", ""),
                ),
            )
            for role, block in cases:
                with self.subTest(role=role, block=block[:160]):
                    with self.assertRaises(lads.Blocked):
                        lads.verify_permission_probe(
                            self.prompt(self.message([block])),
                            role,
                            workspace,
                        )

    def test_preflight_uses_same_prefix_environment_and_restrictive_overrides(self):
        workspace = ROOT
        base = [
            "codex", "--no-daemon", "-C", str(workspace), "--profile", "reviewer-deepseek",
            "--sandbox", "read-only", "--ask-for-approval", "never",
            "-c", "sandbox_workspace_write.writable_roots=[]",
            "-c", "sandbox_workspace_write.network_access=false",
            "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
            "-c", "developer_instructions=[REDACTED]",
        ]

        class Helper:
            MODELS = {"reviewer": "deepseek-flash"}
            KEY_ENV = {"reviewer": "DEEPSEEK_REVIEWER_API_KEY"}

            @staticmethod
            def key_present(_role):
                return True

            @staticmethod
            def sandbox_mode(_role):
                return "read-only"

            @staticmethod
            def command(_role, _workspace, explicit_home=None, **_kwargs):
                return list(base), {"synthetic-profile": "d" * 64}

            @staticmethod
            def main_config_snapshot():
                return synthetic_main_snapshot()

            @staticmethod
            def main_config_transition(expected, actual, _roots=None):
                if expected != actual:
                    raise ValueError("changed")
                return {
                    "status": "UNCHANGED",
                    "before_present": True,
                    "before_sha256": expected["sha256"],
                    "after_present": True,
                    "after_sha256": actual["sha256"],
                    "allowed_project_root_names": [str(ROOT)],
                    "added_project_root_names": [],
                }

        captured = {}

        def fake_run(argv, cwd, prompt, timeout, environment=None):
            captured.update(argv=argv, cwd=cwd, prompt=prompt, timeout=timeout, environment=environment)
            stdout = self.prompt(self.message([self.READONLY_BLOCK]))
            return lads.ProcessResult(0, stdout, self.READONLY_BLOCK, False, False)

        source_environment = {
            "PATH": "safe-path",
            "CODEX_HOME": "safe-home",
            "HTTPS_PROXY": "safe-proxy",
            "OPENAI_API_KEY": "must-not-pass",
            "DEEPSEEK_REVIEWER_API_KEY": "reviewer-only",
            "DEEPSEEK_QA_API_KEY": "must-not-pass",
            "DATABASE_URL": "must-not-pass",
        }
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(lads, "run_process", side_effect=fake_run), \
                mock.patch.dict(os.environ, source_environment, clear=True):
            runtime_home = Path(temporary)
            for name in ("config.toml", "reviewer-deepseek.config.toml", "models.json"):
                (runtime_home / name).write_text(name, encoding="utf-8")
            worker = lads.RealWorker(helper=Helper())
            worker._runtime_home = runtime_home
            worker._runtime = {
                "role": "reviewer",
                "home_mode": "frozen",
                "home": runtime_home,
                "original_home": runtime_home,
                "profile": runtime_home / "reviewer-deepseek.config.toml",
                "catalog": runtime_home / "models.json",
            }
            worker._main_config_expected = synthetic_main_snapshot()
            worker._authority_baseline = {"reviewer": {"profile_sha256": "d" * 64}}
            before_environment = dict(os.environ)
            actual, snapshots, mode, environment = worker._preflight(
                "reviewer", workspace, 30
            )
            self.assertEqual(before_environment, os.environ)
        self.assertEqual(base, actual)
        self.assertEqual({"synthetic-profile": "d" * 64}, snapshots)
        self.assertEqual("read-only", mode)
        self.assertEqual(base, captured["argv"][:-3])
        self.assertEqual(["debug", "prompt-input", "LADS effective permission preflight"], captured["argv"][-3:])
        self.assertEqual(workspace, captured["cwd"])
        self.assertEqual({"PATH", "CODEX_HOME", "HTTPS_PROXY", "DEEPSEEK_REVIEWER_API_KEY"}, set(environment))
        self.assertEqual(str(runtime_home.resolve()), environment["CODEX_HOME"])
        self.assertEqual("", captured["prompt"])
        proof = lads.validate_preflight_proof(worker._preflight_proof, "reviewer")
        self.assertEqual(captured["argv"], proof["debug_argv"])
        self.assertEqual("read-only", proof["permissions"]["sandbox_mode"])
        self.assertEqual([], proof["permissions"]["writable_roots"])

    def test_preflight_does_not_fall_back_to_stderr_permission_text(self):
        workspace = ROOT
        base = [
            "codex", "-C", str(workspace), "--profile", "reviewer-deepseek",
            "--sandbox", "read-only", "--ask-for-approval", "never",
            "-c", "sandbox_workspace_write.writable_roots=[]",
            "-c", "sandbox_workspace_write.network_access=false",
            "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
        ]

        class Helper:
            MODELS = {"reviewer": "deepseek-flash"}
            KEY_ENV = {"reviewer": "DEEPSEEK_REVIEWER_API_KEY"}

            @staticmethod
            def key_present(_role):
                return True

            @staticmethod
            def sandbox_mode(_role):
                return "read-only"

            @staticmethod
            def command(_role, _workspace, explicit_home=None):
                return list(base), {}

        def fake_run(*_args, **_kwargs):
            return lads.ProcessResult(0, "{}", self.READONLY_BLOCK, False, False)

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(lads, "run_process", side_effect=fake_run), \
                mock.patch.dict(os.environ, {"PATH": "safe-path"}, clear=True):
            runtime_home = Path(temporary)
            for name in ("config.toml", "reviewer-deepseek.config.toml", "models.json"):
                (runtime_home / name).write_text(name, encoding="utf-8")
            worker = lads.RealWorker(helper=Helper())
            worker._runtime_home = runtime_home
            worker._authority_baseline = {"reviewer": {"profile_sha256": "d" * 64}}
            with self.assertRaises(lads.Blocked):
                worker._preflight("reviewer", workspace, 30)


class FailureEvidenceTests(unittest.TestCase):
    def make_worker(self, role="reviewer", workspace=ROOT):
        mode = "workspace-write" if role == "builder" else "read-only"
        base = [
            "codex", "-C", str(workspace), "--profile", role + "-deepseek",
            "--sandbox", mode, "--ask-for-approval", "never",
            "-c", "sandbox_workspace_write.writable_roots=[]",
            "-c", "sandbox_workspace_write.network_access=false",
            "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
        ]

        class Helper:
            MODELS = {role: "deepseek-flash"}
            KEY_ENV = {role: "DEEPSEEK_" + role.upper().replace("-", "_") + "_API_KEY"}

            def __init__(self):
                self.snapshots = {"synthetic-profile": "d" * 64}
                self.runtime_home = None

            @staticmethod
            def key_present(_role):
                return True

            @staticmethod
            def sandbox_mode(_role):
                return mode

            def command(self, _role, _workspace, explicit_home=None, **_kwargs):
                return list(base), dict(self.snapshots)

            def runtime_binding(self, _role, _workspace, run_root, **_kwargs):
                home = Path(run_root) / "runtime-homes" / str(uuid.uuid4())
                home.mkdir(parents=True)
                for name in (
                    "config.toml",
                    role + "-deepseek.config.toml",
                    "models.json",
                ):
                    (home / name).write_text(name, encoding="utf-8")
                self.runtime_home = home
                return {
                    "role": role,
                    "home_mode": "frozen",
                    "home": home,
                    "original_home": home,
                    "profile": home / (role + "-deepseek.config.toml"),
                    "catalog": home / "models.json",
                    "project_roots": [str(Path(_workspace).resolve())],
                }

            def authority_inventory(self, **_kwargs):
                return {role: {"profile_sha256": "d" * 64}}

            @staticmethod
            def authorized_project_roots(repo):
                return (Path(repo).resolve(),)

            @staticmethod
            def platform_home_mode(_platform_name=None):
                return "frozen"

            @staticmethod
            def main_config_snapshot():
                return synthetic_main_snapshot()

            @staticmethod
            def main_config_transition(expected, actual, _roots=()):
                if expected != actual:
                    raise ValueError("changed")
                return {
                    "status": "UNCHANGED",
                    "before_present": True,
                    "before_sha256": expected["sha256"],
                    "after_present": True,
                    "after_sha256": actual["sha256"],
                    "allowed_project_root_names": [str(ROOT)],
                    "added_project_root_names": [],
                }

        return lads.RealWorker(helper=Helper(), platform_name="posix")

    @staticmethod
    def permission_stdout(workspace=ROOT):
        return json.dumps(
            [PermissionPreflightTests.message([PermissionPreflightTests.READONLY_BLOCK])],
            ensure_ascii=False,
        )

    def make_orchestrator(self, worker, root):
        worker._repo = ROOT
        worker._run_root = Path(root)
        worker._authority_baseline = {"reviewer": {"profile_sha256": "d" * 64}}
        orchestrator = lads.Orchestrator(worker=worker)
        orchestrator.run_root = Path(root)
        orchestrator.events = Path(root) / "events.jsonl"
        orchestrator.state = {
            "task_id": str(uuid.uuid4()),
            "role_runs": [],
            "stage_reports": [],
            "builds": 0,
            "main_config_snapshot": synthetic_main_snapshot(),
            "main_config_guard_events": [],
        }
        return orchestrator

    def run_to_blocked(self, root, orchestrator):
        with self.assertRaises(lads.Blocked):
            orchestrator.worker_call("reviewer", ROOT, "synthetic prompt")
        reference = orchestrator.state["stage_reports"][0]
        return json.loads((Path(root) / reference["path"]).read_text(encoding="utf-8"))

    def test_preflight_failure_persists_direct_process_evidence(self):
        worker = self.make_worker()
        probe_stdout = "raw preflight context must not persist"

        def fake_run(*_args, **_kwargs):
            return lads.ProcessResult(7, probe_stdout, "preflight failed", False, False)

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(lads, "run_process", side_effect=fake_run):
            payload = self.run_to_blocked(
                temporary,
                self.make_orchestrator(worker, temporary),
            )
        self.assertEqual(7, payload["exit_code"])
        self.assertEqual(lads.sha_bytes(probe_stdout.encode("utf-8")), payload["output_sha256"])
        self.assertEqual("[preflight output withheld: raw prompt context]", payload["output_tail"])
        self.assertEqual(
            ["debug", "prompt-input", "LADS effective permission preflight"],
            payload["command"][-3:],
        )
        self.assertNotIn("raw preflight context", json.dumps(payload))

    def test_timeout_persists_evidence_and_successful_preflight_proof(self):
        worker = self.make_worker()
        calls = {"count": 0}

        def fake_run(argv, *_args, **_kwargs):
            calls["count"] += 1
            if argv[-3:] == ["debug", "prompt-input", "LADS effective permission preflight"]:
                return lads.ProcessResult(0, self.permission_stdout(), "", False, False)
            return lads.ProcessResult(7, "timed out stdout", "timed out", True, False)

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(lads, "run_process", side_effect=fake_run):
            payload = self.run_to_blocked(
                temporary,
                self.make_orchestrator(worker, temporary),
            )
        self.assertEqual(2, calls["count"])
        self.assertEqual(7, payload["exit_code"])
        self.assertEqual(lads.sha_bytes(b"timed out stdout"), payload["output_sha256"])
        proof = lads.validate_preflight_proof(payload["metadata"]["preflight_proof"], "reviewer")
        self.assertEqual(payload["command"][:proof["worker_prefix_length"]], proof["debug_argv"][:proof["worker_prefix_length"]])

    def test_nonzero_persists_actual_exit_code(self):
        worker = self.make_worker()

        def fake_run(argv, *_args, **_kwargs):
            if argv[-3:] == ["debug", "prompt-input", "LADS effective permission preflight"]:
                return lads.ProcessResult(0, self.permission_stdout(), "", False, False)
            return lads.ProcessResult(9, "fatal stdout", "fatal failure", False, False)

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(lads, "run_process", side_effect=fake_run):
            payload = self.run_to_blocked(
                temporary,
                self.make_orchestrator(worker, temporary),
            )
        self.assertEqual(9, payload["exit_code"])
        self.assertIn("fatal failure", payload["output_tail"])
        self.assertEqual(lads.sha_bytes(b"fatal stdout"), payload["output_sha256"])
        self.assertIn("preflight_proof", payload["metadata"])

    def test_real_worker_success_uses_redacted_true_tail(self):
        worker = self.make_worker()
        task_id = str(uuid.uuid4())
        report = {
            "task_id": task_id,
            "role": "reviewer",
            "status": "APPROVED",
            "summary": "approved after review",
            "evidence": ["reviewed the supplied snapshot"],
        }
        stdout = "\n".join([
            json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": json.dumps(report)},
            }),
            json.dumps({
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 2,
                    "diagnostic_suffix": "LAST_REAL_SUCCESS",
                },
            }),
        ])
        secret = "Bearer not-a-real-token-value"
        stderr = secret + "\n" + ("x" * 5000) + "\n"

        def fake_run(argv, *_args, **_kwargs):
            if argv[-3:] == ["debug", "prompt-input", "LADS effective permission preflight"]:
                return lads.ProcessResult(0, self.permission_stdout(), "", False, False)
            return lads.ProcessResult(0, stdout, stderr, False, False)

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(lads, "run_process", side_effect=fake_run):
            worker._repo = ROOT
            worker._run_root = Path(temporary)
            worker._authority_baseline = {"reviewer": {"profile_sha256": "d" * 64}}
            result = worker.run("reviewer", ROOT, "synthetic prompt", task_id, 30)
        self.assertLessEqual(len(result.output_tail), 4000)
        self.assertIn("LAST_REAL_SUCCESS", result.output_tail)
        self.assertNotIn("not-a-real-token-value", result.output_tail)

    def test_malformed_report_persists_process_evidence(self):
        worker = self.make_worker()
        malformed = "not-json"

        def fake_run(argv, *_args, **_kwargs):
            if argv[-3:] == ["debug", "prompt-input", "LADS effective permission preflight"]:
                return lads.ProcessResult(0, self.permission_stdout(), "", False, False)
            return lads.ProcessResult(0, malformed, "", False, False)

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(lads, "run_process", side_effect=fake_run):
            payload = self.run_to_blocked(
                temporary,
                self.make_orchestrator(worker, temporary),
            )
        self.assertEqual(0, payload["exit_code"])
        self.assertEqual(lads.sha_bytes(malformed.encode("utf-8")), payload["output_sha256"])
        self.assertIn("non-JSON event", payload["reason"])
        self.assertIn("preflight_proof", payload["metadata"])

    def test_transient_stage_persists_direct_evidence_and_preflight_proof(self):
        worker = self.make_worker()
        calls = {"main": 0}

        def fake_run(argv, *_args, **_kwargs):
            if argv[-3:] == ["debug", "prompt-input", "LADS effective permission preflight"]:
                return lads.ProcessResult(0, self.permission_stdout(), "", False, False)
            calls["main"] += 1
            if calls["main"] == 1:
                return lads.ProcessResult(7, "temporary", "HTTP 503 rate limit", False, False)
            return lads.ProcessResult(9, "fatal", "permanent failure", False, False)

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(lads, "run_process", side_effect=fake_run):
            orchestrator = self.make_orchestrator(worker, temporary)
            with self.assertRaises(lads.Blocked):
                orchestrator.worker_call("reviewer", ROOT, "synthetic prompt")
            payloads = [
                json.loads(
                    (Path(temporary) / reference["path"]).read_text(encoding="utf-8")
                )
                for reference in orchestrator.state["stage_reports"]
            ]
        self.assertEqual(["RETRYABLE", "BLOCKED"], [item["status"] for item in payloads])
        self.assertEqual(7, payloads[0]["exit_code"])
        self.assertEqual(lads.sha_bytes(b"temporary"), payloads[0]["output_sha256"])
        self.assertIn("preflight_proof", payloads[0]["metadata"])

    def test_real_worker_fails_closed_without_successful_preflight_proof(self):
        worker = self.make_worker()
        argv = [
            "codex", "--sandbox", "read-only", "--ask-for-approval", "never",
        ]
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(
                    worker,
                    "_preflight",
                    return_value=(argv, {"synthetic-profile": "d" * 64}, "read-only", {}),
                ), mock.patch.object(lads, "run_process") as run_process:
            worker._repo = ROOT
            worker._run_root = Path(temporary)
            worker._authority_baseline = {"reviewer": {"profile_sha256": "d" * 64}}
            with self.assertRaises(lads.Blocked):
                worker.run(
                    "reviewer",
                    ROOT,
                    "prompt",
                    str(uuid.uuid4()),
                    30,
                )
        run_process.assert_not_called()

    def test_configuration_drift_blocks_every_worker_exit_path(self):
        role = "reviewer"
        task_id = str(uuid.uuid4())
        report = {
            "task_id": task_id,
            "role": role,
            "status": "APPROVED",
            "summary": "approved",
            "evidence": ["reviewed snapshot"],
        }
        valid_stdout = "\n".join([
            json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": json.dumps(report)},
            }),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ])
        cases = (
            ("already-drifted", 2, None),
            ("preflight-failure", 3, "preflight-failure"),
            ("launch", 4, OSError("synthetic launch failure")),
            ("timeout", 4, lads.ProcessResult(7, "timeout", "", True, False)),
            ("nonzero", 4, lads.ProcessResult(9, "fatal", "failure", False, False)),
            ("malformed", 4, lads.ProcessResult(0, "not-json", "", False, False)),
            ("success", 4, lads.ProcessResult(0, valid_stdout, "", False, False)),
        )
        for name, mutate_on, main_result in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                workspace = ROOT
                base = [
                    "codex", "-C", str(workspace), "--profile", "reviewer-deepseek",
                    "--sandbox", "read-only", "--ask-for-approval", "never",
                    "-c", "sandbox_workspace_write.writable_roots=[]",
                    "-c", "sandbox_workspace_write.network_access=false",
                    "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                    "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
                ]

                class Helper:
                    MODELS = {role: "deepseek-flash"}
                    KEY_ENV = {role: "DEEPSEEK_REVIEWER_API_KEY"}

                    def __init__(self):
                        self.calls = 0
                        self.snapshots = {"synthetic-profile": "d" * 64}

                    @staticmethod
                    def key_present(_role):
                        return True

                    @staticmethod
                    def sandbox_mode(_role):
                        return "read-only"

                    def command(self, _role, _workspace, explicit_home=None, **_kwargs):
                        self.calls += 1
                        if self.calls == mutate_on:
                            self.snapshots["synthetic-profile"] = "f" * 64
                        return list(base), dict(self.snapshots)

                    def runtime_binding(self, _role, _workspace, run_root, **_kwargs):
                        home = Path(run_root) / "runtime-homes" / str(uuid.uuid4())
                        home.mkdir(parents=True)
                        for name in (
                            "config.toml",
                            role + "-deepseek.config.toml",
                            "models.json",
                        ):
                            (home / name).write_text(name, encoding="utf-8")
                        return {
                            "role": role,
                            "home_mode": "frozen",
                            "home": home,
                            "original_home": home,
                            "profile": home / (role + "-deepseek.config.toml"),
                            "catalog": home / "models.json",
                            "project_roots": [str(Path(_workspace).resolve())],
                            "authority_sha256": "e" * 64,
                        }

                    def authority_inventory(self, **_kwargs):
                        return {role: {"profile_sha256": "d" * 64}}

                    @staticmethod
                    def authorized_project_roots(repo):
                        return (Path(repo).resolve(),)

                    @staticmethod
                    def platform_home_mode(_platform_name=None):
                        return "frozen"

                    @staticmethod
                    def main_config_snapshot():
                        return synthetic_main_snapshot()

                    @staticmethod
                    def main_config_transition(expected, actual, _roots=()):
                        if expected != actual:
                            raise ValueError("changed")
                        return {
                            "status": "UNCHANGED",
                            "before_present": True,
                            "before_sha256": expected["sha256"],
                            "after_present": True,
                            "after_sha256": actual["sha256"],
                            "allowed_project_root_names": [str(ROOT)],
                            "added_project_root_names": [],
                        }

                run_calls = []

                def fake_run(argv, *_args, **_kwargs):
                    run_calls.append(argv)
                    if argv[-3:] == ["debug", "prompt-input", "LADS effective permission preflight"]:
                        if main_result == "preflight-failure":
                            return lads.ProcessResult(7, "denied", "", False, False)
                        return lads.ProcessResult(0, self.permission_stdout(), "", False, False)
                    if isinstance(main_result, OSError):
                        raise main_result
                    return main_result

                with mock.patch.object(lads, "run_process", side_effect=fake_run):
                    with self.assertRaises(lads.Blocked) as caught:
                        worker = lads.RealWorker(helper=Helper(), platform_name="posix")
                        worker._repo = ROOT
                        worker._run_root = Path(temporary)
                        worker._project_roots = (ROOT,)
                        worker._authority_baseline = {role: {"profile_sha256": "d" * 64}}
                        worker.run(
                            role,
                            workspace,
                            "synthetic prompt",
                            task_id,
                            30,
                        )
                self.assertIn("configuration drift detected", str(caught.exception))
                if name == "already-drifted":
                    self.assertEqual([], run_calls)
                else:
                    self.assertTrue(run_calls)


class PathAndSnapshotTests(unittest.TestCase):
    def test_path_traversal_case_collision_and_protected_paths(self):
        for value in ("../escape", "/absolute", "C:/drive", ".git/config", ".env", "dir\\file"):
            with self.assertRaises(lads.Blocked):
                lads.safe_relative(value)
        with self.assertRaises(lads.Blocked):
            lads.validate_allowed(["A.py", "a.py"])
        with self.assertRaises(lads.Blocked):
            lads.validate_allowed(["AGENTS.md"])
        with self.assertRaises(lads.Blocked):
            lads.validate_allowed([".codex/agents/qa-tester.toml"])

    def test_test_command_is_stdout_only_python_b(self):
        self.assertEqual(
            "python -B -m unittest discover -s tests -v",
            lads.validate_test_command("python -B -m unittest discover -s tests -v"),
        )
        for value in ("python -m unittest", "python -B -m unittest && echo bad", "pytest -q"):
            with self.assertRaises(lads.Blocked):
                lads.validate_test_command(value)

    def test_snapshot_uses_source_inventory_not_ignored_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            ignored = manifest.repo / "work" / "private.json"
            ignored.parent.mkdir()
            ignored.write_text('{"private":true}', encoding="utf-8")
            snapshot = lads.capture_source(manifest.repo, Path(temporary) / "snapshot")
            self.assertIn("add.py", snapshot.files)
            self.assertIn("tests/test_add.py", snapshot.files)
            self.assertNotIn("work/private.json", snapshot.files)

    def test_manifest_requires_exact_fields_and_false_unavailable_applicability(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            data = json.loads(Path(temporary, "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest.applicability["browser"])
            data["extra"] = "invalid"
            Path(temporary, "bad.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(lads.Blocked):
                lads.load_manifest(Path(temporary, "bad.json"))
            data.pop("extra")
            data["test_commands"].append(data["test_commands"][0])
            Path(temporary, "duplicate.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(lads.Blocked):
                lads.load_manifest(Path(temporary, "duplicate.json"))
            data["test_commands"] = [
                "python -B -m unittest discover -s tests -v # DEEPSEEK_QA_API_KEY=not-a-real-key"
            ]
            Path(temporary, "secretlike-command.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(lads.Blocked):
                lads.load_manifest(Path(temporary, "secretlike-command.json"))

    def test_changed_files_and_outscope_detection_inputs(self):
        before = {"add.py": "1", "tests/test_add.py": "2"}
        after = {"add.py": "3", "new.txt": "4"}
        changes = lads.changed_files(before, after)
        self.assertEqual({"add.py", "tests/test_add.py", "new.txt"}, {item["path"] for item in changes})
        self.assertEqual(
            ["new.txt", "tests/test_add.py"],
            [item["path"] for item in changes if item["path"] not in {"add.py"}],
        )


class RuntimePrimitiveTests(unittest.TestCase):
    @staticmethod
    def make_generated_run(repo):
        run_root = Path(repo) / "work" / "lads" / "runs" / str(uuid.uuid4())
        builder = run_root / "builder"
        (builder / "tracked").mkdir(parents=True)
        (builder / ".git").write_text("gitdir: metadata\n", encoding="utf-8")
        (builder / "tracked" / "file.txt").write_text("generated\n", encoding="utf-8")
        return run_root

    def test_clear_generated_tree_derives_only_verified_uuid_builder(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            run_root = self.make_generated_run(repo)
            lads.clear_generated_tree(repo, run_root)
            self.assertEqual([".git"], [item.name for item in (run_root / "builder").iterdir()])
            outside_repo = Path(temporary) / "outside"
            outside_run = self.make_generated_run(outside_repo)
            sentinel = outside_run / "builder" / "tracked" / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with self.assertRaises(lads.Blocked):
                lads.clear_generated_tree(repo, outside_run)
            self.assertTrue(sentinel.is_file())

    def test_clear_generated_tree_rejects_internal_junction_without_deleting_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            run_root = self.make_generated_run(repo)
            outside = Path(temporary) / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")
            junction = run_root / "builder" / "junction"
            try:
                make_directory_link(junction, outside)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"directory symlink unavailable: {type(error).__name__}")
            with self.assertRaises(lads.Blocked):
                lads.clear_generated_tree(repo, run_root)
            self.assertTrue(sentinel.is_file())

    def test_run_and_managed_parents_reject_junctions(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            (repo / "work" / "lads").mkdir(parents=True)
            outside = Path(temporary) / "outside"
            outside.mkdir()
            task_id = str(uuid.uuid4())
            outside_builder = outside / task_id / "builder"
            outside_builder.mkdir(parents=True)
            outside_sentinel = outside_builder / "sentinel.txt"
            outside_sentinel.write_text("keep", encoding="utf-8")
            try:
                make_directory_link(repo / "work" / "lads" / "runs", outside)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"directory symlink unavailable: {type(error).__name__}")
            with self.assertRaises(lads.Blocked):
                lads.clear_generated_tree(repo, repo / "work" / "lads" / "runs" / task_id)
            self.assertTrue(outside_sentinel.is_file())

            safe_repo = Path(temporary) / "safe-repo"
            safe_run = self.make_generated_run(safe_repo)
            stage_target = Path(temporary) / "stage-target"
            stage_target.mkdir()
            make_directory_link(safe_run / "stages", stage_target)
            with self.assertRaises(lads.Blocked):
                lads.managed_path(safe_run, "stages/report.json", directory=True)

    def test_lock_is_exclusive_and_releases(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = lads.RepoLock(Path(temporary), "same task").acquire()
            second = lads.RepoLock(Path(temporary), "same task")
            with self.assertRaises(lads.Blocked):
                second.acquire()
            first.release()
            third = lads.RepoLock(Path(temporary), "same task").acquire()
            third.release()

    def test_timeout_kills_process_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "child-survived.txt"
            child = Path(temporary) / "child.py"
            child.write_text(
                "import pathlib,time\n"
                "time.sleep(2)\n"
                f"pathlib.Path({str(marker)!r}).write_text('alive')\n",
                encoding="utf-8",
            )
            parent = Path(temporary) / "parent.py"
            parent.write_text(
                "import subprocess,sys,time\n"
                f"subprocess.Popen([sys.executable,'-B',{str(child)!r}])\n"
                "time.sleep(10)\n",
                encoding="utf-8",
            )
            started = time.monotonic()
            result = lads.run_process([sys.executable, "-B", str(parent)], ROOT, "", 0.4)
            self.assertTrue(result.timed_out)
            self.assertLess(time.monotonic() - started, 8)
            time.sleep(2.2)
            self.assertFalse(marker.exists())

    def test_completed_parent_leaves_no_background_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "background-survived.txt"
            child = Path(temporary) / "child.py"
            child.write_text(
                "import pathlib,time\n"
                "time.sleep(1)\n"
                f"pathlib.Path({str(marker)!r}).write_text('alive')\n",
                encoding="utf-8",
            )
            parent = Path(temporary) / "parent.py"
            parent.write_text(
                "import subprocess,sys\n"
                f"subprocess.Popen([sys.executable,'-B',{str(child)!r}])\n",
                encoding="utf-8",
            )
            result = lads.run_process([sys.executable, "-B", str(parent)], ROOT, "", 10)
            self.assertEqual(0, result.exit_code)
            time.sleep(1.2)
            self.assertFalse(marker.exists())

    def test_child_environment_keeps_only_assigned_dedicated_key(self):
        keys = {
            "scanner": "DEEPSEEK_SCANNER_API_KEY",
            "reviewer": "DEEPSEEK_REVIEWER_API_KEY",
            "builder": "DEEPSEEK_BUILDER_API_KEY",
            "qa-tester": "DEEPSEEK_QA_API_KEY",
        }
        source = {
            "DEEPSEEK_SCANNER_API_KEY": "scanner-value",
            "DEEPSEEK_QA_API_KEY": "qa-value",
            "UNRELATED_TOKEN": "token-value",
            "PATH": "safe-path",
            "SYSTEMROOT": "system-root",
            "CODEX_HOME": "codex-home",
            "HTTPS_PROXY": "proxy",
            "OPENAI_API_KEY": "openai-value",
            "DATABASE_URL": "database-value",
            "SSH_AUTH_SOCK": "ssh-value",
            "AWS_ACCESS_KEY_ID": "cloud-value",
            "UNRELATED": "ordinary-but-not-runtime",
        }
        with tempfile.TemporaryDirectory() as temporary:
            isolated_home = Path(temporary)
            environment = lads.child_environment(
                "qa-tester",
                keys,
                source,
                codex_home_path=isolated_home,
            )
        self.assertEqual(
            {
                "DEEPSEEK_QA_API_KEY": "qa-value",
                "PATH": "safe-path",
                "SYSTEMROOT": "system-root",
                "CODEX_HOME": str(isolated_home.resolve()),
                "HTTPS_PROXY": "proxy",
            },
            environment,
        )
        self.assertNotEqual("codex-home", environment["CODEX_HOME"])

    def test_readonly_transient_retries_once(self):
        class Flaky:
            def __init__(self):
                self.calls = 0

            @staticmethod
            def main_config_snapshot():
                return synthetic_main_snapshot()

            @staticmethod
            def main_config_transition(expected, actual):
                if expected != actual:
                    raise lads.Blocked("main Codex config raw SHA changed between stages")
                return {
                    "status": "UNCHANGED",
                    "before_present": expected["present"],
                    "before_sha256": expected["sha256"],
                    "after_present": actual["present"],
                    "after_sha256": actual["sha256"],
                    "allowed_project_root_names": [str(ROOT)],
                    "added_project_root_names": [],
                }

            @staticmethod
            def main_project_roots():
                return (str(ROOT),)

            def run(self, role, workspace, prompt, task_id, timeout, source_digest=None, test_commands=()):
                self.calls += 1
                if self.calls == 1:
                    raise lads.TransientWorkerError("temporary")
                return worker_result(task_id, role, "SCAN_COMPLETE")

        with tempfile.TemporaryDirectory() as temporary:
            orchestrator = lads.Orchestrator(worker=Flaky())
            orchestrator.run_root = Path(temporary)
            orchestrator.events = Path(temporary) / "events.jsonl"
            orchestrator.state = {
                "task_id": str(uuid.uuid4()),
                "role_runs": [],
                "main_config_snapshot": synthetic_main_snapshot(),
                "main_config_guard_events": [],
            }
            result = orchestrator.worker_call("scanner", ROOT, "prompt")
            self.assertEqual("SCAN_COMPLETE", result.report["status"])
            self.assertEqual(2, orchestrator.worker.calls)

    def test_atomic_state_json_is_parseable(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            lads.atomic_json(path, {"state": "CREATED", "value": "中文"})
            self.assertEqual("CREATED", json.loads(path.read_text(encoding="utf-8"))["state"])

    def test_redacted_tail_is_true_suffix_after_full_redaction(self):
        secret = "Bearer not-a-real-token-value"
        text = secret + "\n" + ("discard\n" * 1000) + "LAST_TAIL_SENTINEL"
        tail = lads.redacted_tail(text)
        self.assertEqual(4000, len(tail))
        self.assertTrue(tail.endswith("LAST_TAIL_SENTINEL"))
        self.assertNotIn("not-a-real-token-value", tail)

        partial_at_cutoff = ("x" * 3990) + "\n" + secret
        partial_tail = lads.redacted_tail(partial_at_cutoff)
        self.assertLessEqual(len(partial_tail), 4000)
        self.assertNotIn("not-a-real-token-value", partial_tail)
        self.assertIn("Bearer [REDACTED]", partial_tail)

        oversized = ("q" * (lads.MAX_CAPTURE + 20)) + "UNCAPTURED_LAST"
        oversized_tail = lads.redacted_tail(oversized)
        self.assertLessEqual(len(oversized_tail), 4000)
        self.assertNotIn("UNCAPTURED_LAST", oversized_tail)

    def test_stage_report_is_bounded_redacted_and_hash_referenced(self):
        with tempfile.TemporaryDirectory() as temporary:
            orchestrator = lads.Orchestrator(worker=FakeWorker())
            orchestrator.run_root = Path(temporary)
            orchestrator.events = Path(temporary) / "events.jsonl"
            task_id = str(uuid.uuid4())
            orchestrator.state = {
                "task_id": task_id,
                "role_runs": [],
                "stage_reports": [],
                "builds": 0,
            }
            result = worker_result(task_id, "builder", "BUILT")
            result.command = [
                "codex",
                "developer_instructions=private task prompt",
                "DEEPSEEK_BUILDER_API_KEY=not-a-real-key",
            ]
            result.output_tail = (
                "Bearer not-a-real-token-value\n"
                + ("x" * 5000)
                + "\nLAST_STAGE_SENTINEL"
            )
            orchestrator.record_stage("builder", 1, "BUILT", lads.now(), result=result)
            reference = orchestrator.state["stage_reports"][0]
            path = Path(temporary) / Path(reference["path"])
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("developer_instructions=[REDACTED]", payload["command"][1])
            self.assertNotIn("private task prompt", json.dumps(payload))
            self.assertNotIn("not-a-real-key", json.dumps(payload))
            self.assertNotIn("not-a-real-token-value", json.dumps(payload))
            self.assertEqual(4000, len(payload["output_tail"]))
            self.assertTrue(payload["output_tail"].endswith("LAST_STAGE_SENTINEL"))
            self.assertEqual(result.report, payload["report"])
            self.assertEqual(
                lads.sha_bytes(lads.canonical(payload["report"])),
                payload["report_sha256"],
            )
            self.assertEqual(reference["sha256"], lads.sha_file(path))


class WorkflowTests(unittest.TestCase):
    def test_happy_path_reaches_main_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            worker = FakeWorker(repair=True)
            handoff = lads.Orchestrator(worker=worker, timeout=60).run(manifest)
            self.assertEqual("AWAITING_MAIN_ACCEPTANCE", handoff["task_status"])
            self.assertEqual(1, handoff["builds"])
            self.assertEqual("lifeos_deepseek", handoff["model_provider"])
            self.assertEqual("deepseek-flash", handoff["model_name"])
            self.assertEqual({"input_tokens": None, "output_tokens": None}, handoff["usage"])
            run_root = manifest.repo / "work" / "lads" / "runs" / handoff["task_id"]
            self.assertTrue((run_root / "state.json").is_file())
            self.assertEqual(5, len(handoff["stage_reports"]))
            roles = []
            for reference in handoff["stage_reports"]:
                stage_path = run_root / Path(reference["path"])
                self.assertTrue(stage_path.is_file())
                self.assertEqual(reference["sha256"], lads.sha_file(stage_path))
                payload = json.loads(stage_path.read_text(encoding="utf-8"))
                roles.append(payload["role"])
                self.assertIn("command", payload)
                self.assertIn("commands", payload)
                self.assertIn("exit_code", payload)
                self.assertIn("output_tail", payload)
                self.assertIn("metadata", payload)
                self.assertIn("output_sha256", payload)
                self.assertIsInstance(payload["report"], dict)
                self.assertEqual(
                    lads.sha_bytes(lads.canonical(payload["report"])),
                    payload["report_sha256"],
                )
            self.assertEqual(
                ["scanner", "reviewer", "builder", "qa-tester", "reviewer"],
                roles,
            )
            self.assertEqual("a + b + 1", (manifest.repo / "add.py").read_text(encoding="utf-8").split("return ")[1].strip())

    def test_explicit_phase_contracts_bind_head_scope_tests_and_strict_final(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            worker = FakeWorker(repair=True)
            handoff = lads.Orchestrator(worker=worker, timeout=60).run(manifest)
            self.assertEqual("AWAITING_MAIN_ACCEPTANCE", handoff["task_status"])
            self.assertEqual(
                ["scanner", "reviewer", "builder", "qa-tester", "reviewer"],
                [role for role, _ in worker.calls],
            )
            prompts = {role: prompt for role, prompt in worker.calls if role != "reviewer"}
            source_head = lads.git_head(manifest.repo)
            source_digest = handoff["source_snapshot_sha256"]
            self.assertIn("PHASE=SCANNING", prompts["scanner"])
            self.assertIn("PHASE=BUILDING", prompts["builder"])
            self.assertIn("PHASE=QA", prompts["qa-tester"])
            self.assertIn("Source HEAD: " + source_head, prompts["builder"])
            self.assertIn("Source snapshot SHA-256: " + source_digest, prompts["builder"])
            self.assertIn("Build number: 1", prompts["builder"])
            self.assertIn("Safe Scanner report:", prompts["builder"])
            self.assertIn("APPROVED plan report:", prompts["builder"])
            self.assertIn("Safe Builder report:", prompts["qa-tester"])
            self.assertIn("Bounded redacted diff:", prompts["qa-tester"])
            self.assertIn("Do not run extra exploratory commands.", prompts["qa-tester"])
            self.assertIn(json.dumps(manifest.allowed_paths), prompts["qa-tester"])

            reviewer_prompts = [prompt for role, prompt in worker.calls if role == "reviewer"]
            plan_prompt, final_prompt = reviewer_prompts
            self.assertIn("PHASE=PLAN_REVIEW", plan_prompt)
            self.assertIn("Source HEAD: " + source_head, plan_prompt)
            self.assertIn("Source snapshot SHA-256: " + source_digest, plan_prompt)
            self.assertIn("Proposed exact allowed paths:", plan_prompt)
            self.assertIn(json.dumps(manifest.allowed_paths), plan_prompt)
            self.assertIn("Immutable prescribed test commands:", plan_prompt)
            self.assertIn(json.dumps(manifest.test_commands), plan_prompt)
            self.assertIn("buggy/unfinished candidate, not a completed implementation", plan_prompt)
            self.assertIn("PHASE=FINAL_REVIEW", plan_prompt)

            self.assertIn("PHASE=FINAL_REVIEW", final_prompt)
            self.assertIn("Strict final phase", final_prompt)
            self.assertIn("Immutable prescribed test commands:", final_prompt)
            self.assertIn("A QA FAIL requires CHANGES_REQUESTED", final_prompt)
            self.assertNotIn("DEMO SCOPE ONLY", plan_prompt)

    def test_safe_prompt_context_excludes_process_tails_and_redacts_diff(self):
        process_sentinel = "PROCESS_TAIL_MUST_NOT_REACH_PROMPTS"
        token_sentinel = "Bearer not-a-real-prompt-token"

        class TailWorker(FakeWorker):
            def run(self, role, workspace, prompt, task_id, timeout, source_digest=None, test_commands=()):
                if role == "builder":
                    (Path(workspace) / "add.py").write_text(
                        "def add(a, b):\n    return a + b\n# " + token_sentinel + "\n",
                        encoding="utf-8",
                    )
                    result = worker_result(task_id, role, "BUILT")
                    result.output_tail = process_sentinel
                    return result
                return super().run(role, workspace, prompt, task_id, timeout, source_digest, test_commands)

        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            worker = TailWorker()
            handoff = lads.Orchestrator(worker=worker, timeout=60).run(manifest)
            self.assertEqual("AWAITING_MAIN_ACCEPTANCE", handoff["task_status"])
            prompt_text = "\n".join(prompt for _role, prompt in worker.calls)
            self.assertNotIn(process_sentinel, prompt_text)
            self.assertNotIn("not-a-real-prompt-token", prompt_text)
            self.assertIn("Bearer [REDACTED]", prompt_text)

    def test_plan_rejection_persists_safe_report_and_stops_at_zero_builds(self):
        class RejectingPlanWorker(FakeWorker):
            def run(self, role, workspace, prompt, task_id, timeout, source_digest=None, test_commands=()):
                if role == "reviewer" and "PHASE=PLAN_REVIEW" in prompt:
                    self.calls.append((role, prompt))
                    return worker_result(
                        task_id,
                        role,
                        "CHANGES_REQUESTED",
                        evidence=["Reject unsafe or underspecified plan scope."],
                    )
                return super().run(role, workspace, prompt, task_id, timeout, source_digest, test_commands)

        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            worker = RejectingPlanWorker()
            handoff = lads.Orchestrator(worker=worker, timeout=60).run(manifest)
            self.assertEqual("BLOCKED", handoff["task_status"])
            self.assertEqual(0, handoff["builds"])
            self.assertIn("plan review did not approve (CHANGES_REQUESTED)", handoff["reason"])
            self.assertEqual(["scanner", "reviewer"], [role for role, _ in worker.calls])
            self.assertEqual(2, len(handoff["stage_reports"]))
            run_root = manifest.repo / "work" / "lads" / "runs" / handoff["task_id"]
            payloads = [
                json.loads((run_root / Path(reference["path"])).read_text(encoding="utf-8"))
                for reference in handoff["stage_reports"]
            ]
            self.assertEqual(["scanner", "reviewer"], [item["role"] for item in payloads])
            self.assertEqual("CHANGES_REQUESTED", payloads[1]["status"])
            self.assertEqual(0, payloads[1]["build"])
            self.assertEqual(
                lads.sha_bytes(lads.canonical(payloads[1]["report"])),
                payloads[1]["report_sha256"],
            )
            self.assertFalse(any(item["role"] == "builder" for item in payloads))

    def test_qa_fail_is_reviewed_then_reworked_once(self):
        class ReworkWorker(FakeWorker):
            def __init__(self):
                super().__init__(repair=False)
                self.builds = 0
                self.qa_prompts = []
                self.qa_digests = []

            def run(self, role, workspace, prompt, task_id, timeout, source_digest=None, test_commands=()):
                if role == "builder":
                    self.builds += 1
                    self.repair = self.builds >= 2
                if role == "qa-tester":
                    self.qa_prompts.append(prompt)
                    self.qa_digests.append(source_digest)
                return super().run(role, workspace, prompt, task_id, timeout, source_digest, test_commands)

        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            worker = ReworkWorker()
            handoff = lads.Orchestrator(worker=worker, timeout=60).run(manifest)
            self.assertEqual("AWAITING_MAIN_ACCEPTANCE", handoff["task_status"])
            self.assertEqual(2, handoff["builds"])
            roles = [role for role, _ in worker.calls]
            self.assertEqual(["scanner", "reviewer", "builder", "qa-tester", "reviewer", "builder"], roles[:6])
            self.assertEqual({1, 2}, {item["build"] for item in handoff["tests"]})
            self.assertIn(1, {item["exit_code"] for item in handoff["tests"]})
            self.assertIn(0, {item["exit_code"] for item in handoff["tests"]})
            run_root = manifest.repo / "work" / "lads" / "runs" / handoff["task_id"]
            stage_payloads = [
                json.loads((run_root / Path(item["path"])).read_text(encoding="utf-8"))
                for item in handoff["stage_reports"]
            ]
            qa_statuses = [item["status"] for item in stage_payloads if item["role"] == "qa-tester"]
            self.assertEqual(["FAIL", "PASS"], qa_statuses)
            self.assertEqual(8, len(stage_payloads))
            self.assertEqual(2, len(worker.qa_prompts))
            self.assertEqual(2, len(worker.qa_digests))
            source_digest = handoff["source_snapshot_sha256"]
            self.assertNotEqual(worker.qa_digests[0], worker.qa_digests[1])
            self.assertNotEqual(worker.qa_digests[1], source_digest)
            for qa_prompt, candidate_digest in zip(worker.qa_prompts, worker.qa_digests):
                self.assertEqual(
                    1,
                    qa_prompt.count("source_snapshot_sha256 REQUIRED VALUE:"),
                )
                self.assertIn("source_snapshot_sha256 REQUIRED VALUE: " + candidate_digest, qa_prompt)
                self.assertNotIn("Source snapshot SHA-256: " + source_digest, qa_prompt)
                self.assertIn("Source HEAD: " + handoff["source_head"], qa_prompt)
            self.assertNotIn(source_digest, worker.qa_prompts[1])

            command = manifest.test_commands[0]
            old_digest_report = {
                "task_id": handoff["task_id"],
                "role": "qa-tester",
                "status": "FAIL",
                "summary": "old aggregate returned",
                "evidence": [command],
                "source_snapshot_sha256": source_digest,
            }
            failed_command = [{"command": command, "status": "completed", "exit_code": 1}]
            with self.assertRaises(lads.Blocked):
                lads.validate_report(
                    old_digest_report,
                    handoff["task_id"],
                    "qa-tester",
                    worker.qa_digests[-1],
                    manifest.test_commands,
                    failed_command,
                )
            with self.assertRaises(lads.Blocked):
                lads.validate_report(
                    dict(old_digest_report, source_snapshot_sha256=worker.qa_digests[-1]),
                    handoff["task_id"],
                    "qa-tester",
                    worker.qa_digests[-1],
                    manifest.test_commands,
                    failed_command + [{"command": "echo extra", "status": "completed", "exit_code": 0}],
                )

    def test_qa_raw_commands_and_evidence_are_never_exposed_after_validation(self):
        command_sentinel = "DEEPSEEK_QA_API_KEY=not-a-real-command-sentinel"
        report_sentinel = "not-a-real-report-sentinel"

        class SensitiveQaWorker(FakeWorker):
            def __init__(self):
                super().__init__(repair=False)
                self.builds = 0
                self.raw_qa_reports = []

            def run(self, role, workspace, prompt, task_id, timeout, source_digest=None, test_commands=()):
                if role == "builder":
                    self.builds += 1
                    self.repair = self.builds >= 2
                result = super().run(
                    role,
                    workspace,
                    prompt,
                    task_id,
                    timeout,
                    source_digest,
                    test_commands,
                )
                if role == "qa-tester":
                    command = test_commands[0]
                    exit_code = result.commands[0]["exit_code"]
                    result.commands = [{
                        "command": (
                            command_sentinel
                            + ' powershell.exe -NoProfile -Command "'
                            + command
                            + '"'
                        ),
                        "status": "completed",
                        "exit_code": exit_code,
                    }]
                    result.report["evidence"] = [
                        report_sentinel,
                        "observed while running " + command,
                    ]
                    self.raw_qa_reports.append(result.report)
                return result

        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            worker = SensitiveQaWorker()
            handoff = lads.Orchestrator(worker=worker, timeout=60).run(manifest)
            self.assertEqual("AWAITING_MAIN_ACCEPTANCE", handoff["task_status"])
            self.assertEqual(2, handoff["builds"])
            prompt_text = "\n".join(prompt for _, prompt in worker.calls)
            self.assertNotIn(command_sentinel, prompt_text)
            self.assertNotIn(report_sentinel, prompt_text)
            self.assertNotIn(command_sentinel, json.dumps(handoff, ensure_ascii=False))
            self.assertNotIn(report_sentinel, json.dumps(handoff, ensure_ascii=False))
            self.assertEqual(
                [manifest.test_commands[0], manifest.test_commands[0]],
                [item["command"] for item in handoff["tests"]],
            )
            self.assertIn(manifest.test_commands[0], handoff["qa_final"]["evidence"])
            run_root = manifest.repo / "work" / "lads" / "runs" / handoff["task_id"]
            state_text = (run_root / "state.json").read_text(encoding="utf-8")
            self.assertNotIn(command_sentinel, state_text)
            self.assertNotIn(report_sentinel, state_text)
            for reference in handoff["stage_reports"]:
                stage_text = (run_root / Path(reference["path"])).read_text(encoding="utf-8")
                self.assertNotIn(command_sentinel, stage_text)
                self.assertNotIn(report_sentinel, stage_text)
                payload = json.loads(stage_text)
                if payload["role"] == "qa-tester":
                    self.assertEqual(
                        lads.sha_bytes(lads.canonical(payload["report"])),
                        payload["report_sha256"],
                    )
                    self.assertEqual(
                        [
                            "[QA evidence omitted from control-plane data]",
                            manifest.test_commands[0],
                        ],
                        payload["report"]["evidence"],
                    )
            self.assertEqual(2, len(worker.raw_qa_reports))
            qa_payloads = [
                json.loads((run_root / Path(reference["path"])).read_text(encoding="utf-8"))
                for reference in handoff["stage_reports"]
                if "qa-tester" in reference["path"]
            ]
            for raw_report, payload in zip(worker.raw_qa_reports, qa_payloads):
                self.assertNotEqual(
                    lads.sha_bytes(lads.canonical(raw_report)),
                    payload["report_sha256"],
                )
                self.assertEqual(
                    lads.sha_bytes(lads.canonical(payload["report"])),
                    payload["report_sha256"],
                )

    def test_qa_fail_requires_explicit_final_changes_requested(self):
        class ApprovingFinalReviewer(FakeWorker):
            def __init__(self):
                super().__init__(repair=False)

            def run(self, role, workspace, prompt, task_id, timeout, source_digest=None, test_commands=()):
                if role == "reviewer" and "PHASE=FINAL_REVIEW" in prompt:
                    self.calls.append((role, prompt))
                    return worker_result(task_id, role, "APPROVED")
                return super().run(role, workspace, prompt, task_id, timeout, source_digest, test_commands)

        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            handoff = lads.Orchestrator(
                worker=ApprovingFinalReviewer(),
                timeout=60,
            ).run(manifest)
            self.assertEqual("BLOCKED", handoff["task_status"])
            self.assertEqual(1, handoff["builds"])
            self.assertIn("must request changes for QA FAIL", handoff["reason"])

    def test_four_build_limit_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            worker = FakeWorker(repair=False)
            handoff = lads.Orchestrator(worker=worker, timeout=60).run(manifest)
            self.assertEqual("BLOCKED", handoff["task_status"])
            self.assertEqual(4, handoff["builds"])
            self.assertIn("four builds", handoff["reason"])

    def test_out_of_scope_builder_change_blocks_without_copyback(self):
        class OutscopeWorker(FakeWorker):
            def run(self, role, workspace, prompt, task_id, timeout, source_digest=None, test_commands=()):
                if role == "builder":
                    ignored = Path(workspace) / "__pycache__" / "outside.pyc"
                    ignored.parent.mkdir()
                    ignored.write_bytes(b"bad")
                    return worker_result(task_id, role, "BUILT")
                return super().run(role, workspace, prompt, task_id, timeout, source_digest, test_commands)

        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            handoff = lads.Orchestrator(worker=OutscopeWorker(), timeout=60).run(manifest)
            self.assertEqual("BLOCKED", handoff["task_status"])
            self.assertFalse((manifest.repo / "__pycache__" / "outside.pyc").exists())

    def test_builder_commit_is_blocked(self):
        class CommitWorker(FakeWorker):
            def run(self, role, workspace, prompt, task_id, timeout, source_digest=None, test_commands=()):
                if role == "builder":
                    (Path(workspace) / "add.py").write_text(
                        "def add(a, b):\n    return a + b\n", encoding="utf-8"
                    )
                    subprocess.run(["git", "-C", str(workspace), "add", "add.py"], check=True)
                    subprocess.run(
                        ["git", "-C", str(workspace), "commit", "-m", "forbidden"],
                        check=True,
                        capture_output=True,
                    )
                    return worker_result(task_id, role, "BUILT")
                return super().run(role, workspace, prompt, task_id, timeout, source_digest, test_commands)

        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            handoff = lads.Orchestrator(worker=CommitWorker(), timeout=60).run(manifest)
            self.assertEqual("BLOCKED", handoff["task_status"])
            self.assertIn("worktree HEAD", handoff["reason"])

    def test_source_baseline_drift_blocks_final_acceptance(self):
        class DriftWorker(FakeWorker):
            def __init__(self, repo):
                super().__init__(repair=True)
                self.repo = repo

            def run(self, role, workspace, prompt, task_id, timeout, source_digest=None, test_commands=()):
                result = super().run(role, workspace, prompt, task_id, timeout, source_digest, test_commands)
                if role == "reviewer" and "QA report:" in prompt:
                    (self.repo / "add.py").write_text(
                        "def add(a, b):\n    return a + b\n# concurrent drift\n",
                        encoding="utf-8",
                    )
                return result

        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            handoff = lads.Orchestrator(worker=DriftWorker(manifest.repo), timeout=60).run(manifest)
            self.assertEqual("BLOCKED", handoff["task_status"])
            self.assertIn("source baseline drifted", handoff["reason"])

    def test_run_level_main_config_drift_blocks_between_stages(self):
        class ConfigDriftWorker(FakeWorker):
            def run(self, role, workspace, prompt, task_id, timeout, source_digest=None, test_commands=()):
                result = super().run(role, workspace, prompt, task_id, timeout, source_digest, test_commands)
                if role == "scanner":
                    self._main_config_snapshot["sha256"] = "f" * 64
                return result

        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            handoff = lads.Orchestrator(worker=ConfigDriftWorker(), timeout=60).run(manifest)
            self.assertEqual("BLOCKED", handoff["task_status"])
            self.assertEqual(0, handoff["builds"])
            self.assertIn("main Codex config raw SHA changed", handoff["reason"])
            self.assertEqual(
                synthetic_main_snapshot(),
                handoff["main_config_snapshot"],
            )

    def test_run_level_main_config_drift_blocks_failed_worker_path(self):
        class FailedRoleConfigDriftWorker(FakeWorker):
            def run(self, role, workspace, prompt, task_id, timeout, source_digest=None, test_commands=()):
                if role == "scanner":
                    self._main_config_snapshot["sha256"] = "f" * 64
                    raise lads.Blocked("synthetic worker failure")
                return super().run(role, workspace, prompt, task_id, timeout, source_digest, test_commands)

        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            handoff = lads.Orchestrator(
                worker=FailedRoleConfigDriftWorker(),
                timeout=60,
            ).run(manifest)
            self.assertEqual("BLOCKED", handoff["task_status"])
            self.assertIn("main Codex config raw SHA changed", handoff["reason"])
            self.assertEqual(0, handoff["builds"])

    def test_scanner_blocked_stops_immediately(self):
        class BlockedScanner(FakeWorker):
            def run(self, role, workspace, prompt, task_id, timeout, source_digest=None, test_commands=()):
                if role == "scanner":
                    self.calls.append((role, prompt))
                    return worker_result(task_id, role, "BLOCKED")
                return super().run(role, workspace, prompt, task_id, timeout, source_digest, test_commands)

        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            worker = BlockedScanner()
            handoff = lads.Orchestrator(worker=worker, timeout=60).run(manifest)
            self.assertEqual("BLOCKED", handoff["task_status"])
            self.assertEqual(1, len(worker.calls))

    def test_unexpected_runtime_error_has_blocked_handoff(self):
        class BrokenWorker(FakeWorker):
            def run(self, role, workspace, prompt, task_id, timeout, source_digest=None, test_commands=()):
                if role == "scanner":
                    raise RuntimeError("synthetic failure")
                return super().run(role, workspace, prompt, task_id, timeout, source_digest, test_commands)

        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            handoff = lads.Orchestrator(worker=BrokenWorker(), timeout=60).run(manifest)
            self.assertEqual("BLOCKED", handoff["task_status"])
            self.assertIn("RuntimeError", handoff["reason"])

    def test_block_before_snapshot_still_has_complete_handoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            manifest = lads.Manifest(
                manifest.repo,
                manifest.task,
                manifest.allowed_paths,
                manifest.test_commands,
                {"browser": True, "multiuser": False},
                manifest.raw_sha256,
            )
            handoff = lads.Orchestrator(worker=FakeWorker(), timeout=60).run(manifest)
            self.assertEqual("BLOCKED", handoff["task_status"])
            self.assertIsNone(handoff["source_snapshot_sha256"])
            self.assertEqual("BLOCKED", handoff["unmet_checks"][0]["status"])

    def test_qa_mutation_is_detected(self):
        class MutatingQa(FakeWorker):
            def run(self, role, workspace, prompt, task_id, timeout, source_digest=None, test_commands=()):
                result = super().run(role, workspace, prompt, task_id, timeout, source_digest, test_commands)
                if role == "qa-tester":
                    (Path(workspace) / "added-by-qa.txt").write_text("bad", encoding="utf-8")
                return result

        with tempfile.TemporaryDirectory() as temporary:
            manifest = make_repo(Path(temporary) / "repo")
            handoff = lads.Orchestrator(worker=MutatingQa(), timeout=60).run(manifest)
            self.assertEqual("BLOCKED", handoff["task_status"])
            self.assertIn("QA source snapshot changed", handoff["reason"])


class FrozenHomeAdapterTests(unittest.TestCase):
    @staticmethod
    def make_run_root(repo):
        run_root = Path(repo) / "work" / "lads" / "runs" / str(uuid.uuid4())
        run_root.mkdir(parents=True)
        return run_root

    def test_four_role_homes_are_fresh_minimal_and_copy_only_catalog(self):
        helper = load_module("helper_frozen_home_roles", ROOT / "scripts" / "lifeos-deepseek.py")
        with tempfile.TemporaryDirectory() as temporary:
            origin, source_catalog = write_origin_authority(Path(temporary) / "origin")
            repo = make_repo(Path(temporary) / "worktree" / "repo").repo
            run_root = self.make_run_root(repo)
            source_profile_hashes = {
                path.name: lads.sha_file(path)
                for path in origin.glob("*-deepseek.config.toml")
            }
            created = []
            with mock.patch.dict(
                os.environ,
                {
                    "CODEX_HOME": str(origin),
                    "DEEPSEEK_BUILDER_API_KEY": "DO_NOT_COPY_ENV_SENTINEL",
                },
                clear=False,
            ):
                for role in ("scanner", "reviewer", "builder", "qa-tester"):
                    created.append(helper.create_runtime_home(
                        role, repo, run_root, platform_name="posix",
                    ))
                authority = helper.authority_inventory(platform_name="posix")

            self.assertEqual(set(helper.MODELS), set(authority))
            self.assertEqual(4, len({str(item["home"]) for item in created}))
            self.assertEqual(source_profile_hashes, {
                path.name: lads.sha_file(path)
                for path in origin.glob("*-deepseek.config.toml")
            })
            for item in created:
                home = Path(item["home"])
                self.assertEqual(run_root / "runtime-homes", home.parent)
                self.assertEqual(home.name, str(uuid.UUID(home.name)))
                self.assertTrue(lads.path_within(home, run_root))
                self.assertEqual(
                    {"config.toml", "models.json", item["role"] + "-deepseek.config.toml"},
                    {path.name for path in home.iterdir()},
                )
                self.assertFalse((home / "auth.json").exists())
                self.assertFalse((home / "sessions").exists())
                runtime_text = "\n".join(
                    path.read_text(encoding="utf-8")
                    for path in home.iterdir()
                    if path.is_file()
                )
                self.assertNotIn("DO_NOT_COPY_AUTH_SENTINEL", runtime_text)
                self.assertNotIn("DO_NOT_COPY_SESSION_SENTINEL", runtime_text)
                self.assertNotIn("DO_NOT_COPY_ENV_SENTINEL", runtime_text)
                self.assertNotIn("private-origin-placeholder", runtime_text)
                parsed = __import__("tomllib").loads(
                    (home / "config.toml").read_text(encoding="utf-8")
                )
                self.assertEqual("deepseek-flash", parsed["model"])
                self.assertEqual("lifeos_deepseek", parsed["model_provider"])
                self.assertEqual([], parsed["notify"])
                self.assertEqual({"sandbox": "elevated"}, parsed["windows"])
                self.assertEqual({"enabled": False}, parsed["agents"])
                self.assertEqual({"apps": False, "plugins": False}, parsed["features"])
                self.assertNotIn("hooks", parsed)
                self.assertNotIn("mcp_servers", parsed)
                self.assertEqual(
                    "workspace-write" if item["role"] == "builder" else "read-only",
                    parsed["sandbox_mode"],
                )
                expected_projects = {
                    __import__("os").path.normcase(str(root)): {"trust_level": "trusted"}
                    for root in helper.authorized_project_roots(repo)
                }
                self.assertEqual(expected_projects, parsed["projects"])
                self.assertEqual(lads.sha_file(source_catalog), lads.sha_file(home / "models.json"))

        self.assertTrue(all(hasattr(helper, name) for name in (
            "check", "command", "create_runtime_home", "authority_inventory"
        )))

    def test_runtime_home_containment_allows_managed_userhome_descendant(self):
        helper = load_module("helper_runtime_containment", ROOT / "scripts" / "lifeos-deepseek.py")
        with tempfile.TemporaryDirectory() as temporary:
            fake_user_home = Path(temporary) / "Administrator"
            origin, _catalog = write_origin_authority(fake_user_home / ".codex")
            repo = make_repo(
                fake_user_home / ".codex" / "worktrees" / "750c" / "Life OS"
            ).repo
            run_root = self.make_run_root(repo)
            with mock.patch.object(helper.Path, "home", return_value=fake_user_home), \
                    mock.patch.dict(os.environ, {"CODEX_HOME": str(origin)}, clear=False):
                runtime = helper.create_runtime_home(
                    "reviewer", repo, run_root, platform_name="posix",
                )
            runtime_home = Path(runtime["home"])
            self.assertTrue(lads.path_within(runtime_home, fake_user_home))
            self.assertNotEqual(fake_user_home, runtime_home)
            self.assertNotEqual(origin, runtime_home)

            with self.assertRaises(ValueError):
                helper.validate_runtime_home(
                    run_root,
                    fake_user_home,
                    user_home=fake_user_home,
                    original_home=origin,
                )
            with self.assertRaises(ValueError):
                helper.validate_runtime_home(
                    run_root,
                    Path(temporary) / "outside" / str(uuid.uuid4()),
                    user_home=fake_user_home,
                    original_home=origin,
                )

    def test_runtime_home_rejects_existing_and_reparse_paths(self):
        helper = load_module("helper_runtime_reparse", ROOT / "scripts" / "lifeos-deepseek.py")
        with tempfile.TemporaryDirectory() as temporary:
            origin, _catalog = write_origin_authority(Path(temporary) / "origin")
            repo = make_repo(Path(temporary) / "repo").repo
            run_root = self.make_run_root(repo)
            container = run_root / "runtime-homes"
            container.mkdir()
            existing = container / str(uuid.uuid4())
            existing.mkdir()
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(origin)}, clear=False):
                with self.assertRaises(ValueError):
                    helper.validate_runtime_home(run_root, existing)

            outside = Path(temporary) / "outside"
            outside.mkdir()
            unsafe_repo = make_repo(Path(temporary) / "unsafe-repo").repo
            unsafe_run = self.make_run_root(unsafe_repo)
            try:
                make_directory_link(unsafe_run / "runtime-homes", outside)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"directory symlink unavailable: {type(error).__name__}")
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(origin)}, clear=False):
                with self.assertRaises(ValueError):
                    helper.create_runtime_home(
                        "reviewer", unsafe_repo, unsafe_run, platform_name="posix",
                    )

    def test_project_hooks_block_and_mcp_is_disabled_without_runtime_sections(self):
        helper = load_module("helper_project_security", ROOT / "scripts" / "lifeos-deepseek.py")
        with tempfile.TemporaryDirectory() as temporary:
            origin, _catalog = write_origin_authority(Path(temporary) / "origin")
            repo = make_repo(Path(temporary) / "repo").repo
            run_root = self.make_run_root(repo)
            project_config = repo / ".codex" / "config.toml"
            project_config.parent.mkdir()
            project_config.write_text(
                '[mcp_servers.synthetic]\ncommand = "not-executed"\n',
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(origin)}, clear=False):
                runtime = helper.create_runtime_home(
                    "reviewer", repo, run_root, platform_name="posix",
                )
                with mock.patch.object(helper.shutil, "which", return_value=sys.executable):
                    argv, snapshots = helper.command(
                        "reviewer", repo, runtime["home"], platform_name="posix",
                    )
                self.assertIn("mcp_servers.synthetic.enabled=false", argv)
                runtime_text = (Path(runtime["home"]) / "config.toml").read_text(encoding="utf-8")
                self.assertNotIn("mcp_servers", runtime_text)
                self.assertNotIn("hooks", runtime_text)
                self.assertIn(str(project_config), snapshots)

                project_config.write_text("[hooks]\ncommand = 'forbidden'\n", encoding="utf-8")
                with mock.patch.object(helper.shutil, "which", return_value=sys.executable):
                    with self.assertRaises(ValueError):
                        helper.command(
                            "reviewer", repo, runtime["home"], platform_name="posix",
                        )

    def test_real_worker_uses_same_frozen_home_argv_and_environment(self):
        helper = load_module("helper_real_frozen_run", ROOT / "scripts" / "lifeos-deepseek.py")
        with tempfile.TemporaryDirectory() as temporary:
            origin, _catalog = write_origin_authority(Path(temporary) / "origin")
            repo = make_repo(Path(temporary) / "repo").repo
            run_root = self.make_run_root(repo)
            task_id = str(uuid.uuid4())
            report = {
                "task_id": task_id,
                "role": "builder",
                "status": "BUILT",
                "summary": "synthetic frozen-home proof",
                "evidence": ["no file write canary in adapter unit test"],
            }
            stdout = "\n".join([
                json.dumps({
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": json.dumps(report)},
                }),
                json.dumps({"type": "turn.completed", "usage": {}}),
            ])
            calls = []

            def fake_run(argv, cwd, prompt, timeout, environment=None):
                calls.append({
                    "argv": list(argv),
                    "environment": dict(environment or {}),
                    "prompt": prompt,
                })
                if argv[-3:] == ["debug", "prompt-input", "LADS effective permission preflight"]:
                    block = PermissionPreflightTests().builder_block(repo)
                    return lads.ProcessResult(
                        0,
                        PermissionPreflightTests().prompt(
                            PermissionPreflightTests.message([block])
                        ),
                        "",
                        False,
                        False,
                    )
                return lads.ProcessResult(0, stdout, "", False, False)

            source_environment = {
                "PATH": os.environ.get("PATH", ""),
                "CODEX_HOME": str(origin),
                "DEEPSEEK_BUILDER_API_KEY": "builder-only-sentinel",
                "DEEPSEEK_REVIEWER_API_KEY": "must-not-pass",
                "OPENAI_API_KEY": "must-not-pass",
            }
            with mock.patch.dict(os.environ, source_environment, clear=True), \
                    mock.patch.object(helper.shutil, "which", return_value=sys.executable), \
                    mock.patch.object(lads, "run_process", side_effect=fake_run):
                before_environment = dict(os.environ)
                worker = lads.RealWorker(helper=helper, platform_name="posix")
                worker.bind_run(repo, run_root)
                result = worker.run(
                    "builder",
                    repo,
                    "synthetic prompt",
                    task_id,
                    30,
                )
                self.assertEqual(before_environment, os.environ)

            self.assertEqual(2, len(calls))
            self.assertEqual("BUILT", result.report["status"])
            self.assertEqual(calls[0]["environment"], calls[1]["environment"])
            exec_position = calls[1]["argv"].index("exec")
            self.assertEqual(
                calls[0]["argv"][: result.metadata["preflight_proof"]["worker_prefix_length"]],
                calls[1]["argv"][:exec_position],
            )
            self.assertEqual(
                ["--json", "--ephemeral", "--skip-git-repo-check", "--output-schema"],
                calls[1]["argv"][exec_position + 1 : exec_position + 5],
            )
            schema_path = Path(calls[1]["argv"][exec_position + 5])
            self.assertEqual("-", calls[1]["argv"][exec_position + 6])
            self.assertEqual(len(calls[1]["argv"]), exec_position + 7)
            self.assertEqual(
                lads.report_output_schema("builder"),
                json.loads(schema_path.read_text(encoding="utf-8")),
            )
            runtime_home = Path(result.metadata["runtime_home"])
            self.assertTrue(lads.path_within(runtime_home, run_root))
            self.assertEqual(str(runtime_home), calls[0]["environment"]["CODEX_HOME"])
            self.assertEqual(str(runtime_home), calls[1]["environment"]["CODEX_HOME"])
            self.assertNotIn("DEEPSEEK_REVIEWER_API_KEY", calls[0]["environment"])
            self.assertNotIn("OPENAI_API_KEY", calls[0]["environment"])
            self.assertEqual(
                result.metadata["preflight_proof"]["runtime_home"],
                str(runtime_home),
            )

    def test_runtime_file_and_original_authority_tamper_block_after_preflight(self):
        helper = load_module("helper_runtime_tamper", ROOT / "scripts" / "lifeos-deepseek.py")
        for mutate in ("preflight", "runtime", "authority"):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as temporary:
                origin, _catalog = write_origin_authority(Path(temporary) / "origin")
                repo = make_repo(Path(temporary) / "repo").repo
                run_root = self.make_run_root(repo)
                task_id = str(uuid.uuid4())
                report = {
                    "task_id": task_id,
                    "role": "reviewer",
                    "status": "APPROVED",
                    "summary": "synthetic",
                    "evidence": ["synthetic"],
                }
                stdout = "\n".join([
                    json.dumps({
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": json.dumps(report)},
                    }),
                    json.dumps({"type": "turn.completed", "usage": {}}),
                ])
                mutated = {"done": False}
                main_calls = {"count": 0}

                def mutate_once():
                    if mutated["done"]:
                        return
                    if mutate in {"runtime", "preflight"}:
                        runtime_home = next((run_root / "runtime-homes").iterdir())
                        with (runtime_home / "config.toml").open("a", encoding="utf-8") as stream:
                            stream.write("\n# runtime tamper\n")
                    else:
                        profile = origin / "reviewer-deepseek.config.toml"
                        with profile.open("a", encoding="utf-8") as stream:
                            stream.write("\n# authority tamper\n")
                    mutated["done"] = True

                def fake_run(argv, *_args, **_kwargs):
                    if argv[-3:] == ["debug", "prompt-input", "LADS effective permission preflight"]:
                        response = lads.ProcessResult(
                            0,
                            PermissionPreflightTests().prompt(
                                PermissionPreflightTests.message(
                                    [PermissionPreflightTests.READONLY_BLOCK]
                                )
                            ),
                            "",
                            False,
                            False,
                        )
                        if mutate == "preflight":
                            mutate_once()
                        return response
                    main_calls["count"] += 1
                    if mutate != "preflight":
                        mutate_once()
                    return lads.ProcessResult(0, stdout, "", False, False)

                with mock.patch.dict(
                    os.environ,
                    {
                        "PATH": os.environ.get("PATH", ""),
                        "CODEX_HOME": str(origin),
                        "DEEPSEEK_REVIEWER_API_KEY": "synthetic-only",
                    },
                    clear=True,
                ), mock.patch.object(helper.shutil, "which", return_value=sys.executable), \
                        mock.patch.object(lads, "run_process", side_effect=fake_run):
                    worker = lads.RealWorker(helper=helper, platform_name="posix")
                    worker.bind_run(repo, run_root)
                    with self.assertRaises(lads.Blocked) as caught:
                        worker.run("reviewer", repo, "synthetic", task_id, 30)
                self.assertIn("configuration drift detected", str(caught.exception))
                self.assertTrue(mutated["done"])
                self.assertEqual(0 if mutate == "preflight" else 1, main_calls["count"])


class WindowsSharedHomeTests(unittest.TestCase):
    @staticmethod
    def write_main_config(home, projects, *, model="synthetic"):
        lines = [f"model = {json.dumps(model)}", ""]
        for key, values in projects.items():
            lines.append(f"[projects.{json.dumps(key)}]")
            for name, value in values.items():
                lines.append(f"{name} = {json.dumps(value)}")
            lines.append("")
        (Path(home) / "config.toml").write_text("\n".join(lines), encoding="utf-8")

    def test_nt_runtime_binding_uses_original_home_without_runtime_creation(self):
        helper = load_module("helper_windows_shared_binding", ROOT / "scripts" / "lifeos-deepseek.py")
        with tempfile.TemporaryDirectory() as temporary:
            origin, _catalog = write_origin_authority(Path(temporary) / "origin")
            repo = make_repo(Path(temporary) / "repo").repo
            missing_run_root = repo / "work" / "lads" / "runs" / str(uuid.uuid4())
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(origin)}, clear=False), \
                    mock.patch.object(helper, "create_runtime_home") as create_home:
                bindings = {
                    role: helper.runtime_binding(
                        role,
                        repo,
                        missing_run_root,
                        platform_name="nt",
                    )
                    for role in helper.MODELS
                }
            create_home.assert_not_called()
            self.assertFalse(missing_run_root.exists())
            for role, binding in bindings.items():
                self.assertEqual("shared", binding["home_mode"])
                self.assertEqual(role, binding["role"])
                self.assertEqual(origin.resolve(), Path(binding["home"]))
                self.assertEqual(origin.resolve(), Path(binding["original_home"]))
                self.assertEqual(origin / (role + "-deepseek.config.toml"), Path(binding["profile"]))

    def test_nt_four_role_routes_keep_shared_environment_sandbox_and_config_exclusion(self):
        helper = load_module("helper_windows_shared_routes", ROOT / "scripts" / "lifeos-deepseek.py")
        with tempfile.TemporaryDirectory() as temporary:
            origin, catalog = write_origin_authority(Path(temporary) / ".codex")
            repo = make_repo(origin / "worktrees" / "750c" / "repo").repo
            secret = "DO_NOT_SERIALIZE_MAIN_CONFIG"
            main_config = origin / "config.toml"
            main_config.write_text(
                f'model = "synthetic"\nprivate_setting = "{secret}"\n',
                encoding="utf-8",
            )
            source_environment = {
                "PATH": os.environ.get("PATH", ""),
                "HTTPS_PROXY": "http://synthetic.invalid:8080",
                "CODEX_HOME": str(origin),
                "OPENAI_API_KEY": "must-not-pass",
                "UNRELATED": "must-not-pass",
            }
            source_environment.update({
                helper.KEY_ENV[role]: role + "-only-sentinel"
                for role in helper.MODELS
            })
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(origin)}, clear=False), \
                    mock.patch.object(helper.shutil, "which", return_value=sys.executable):
                for role in helper.MODELS:
                    with self.subTest(role=role):
                        argv, snapshots = helper.command(role, repo, platform_name="nt")
                        mode = "workspace-write" if role == "builder" else "read-only"
                        self.assertEqual(role + "-deepseek", argv[argv.index("--profile") + 1])
                        self.assertEqual(mode, argv[argv.index("--sandbox") + 1])
                        self.assertEqual("never", argv[argv.index("--ask-for-approval") + 1])
                        for override in (
                            "sandbox_workspace_write.writable_roots=[]",
                            "sandbox_workspace_write.network_access=false",
                            "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                            "sandbox_workspace_write.exclude_slash_tmp=true",
                            'shell_environment_policy.exclude=["*KEY*", "*TOKEN*", "*SECRET*", "*PASSWORD*"]',
                        ):
                            self.assertIn(override, argv)
                        self.assertFalse(any(item.startswith('projects."') for item in argv))
                        self.assertNotIn(str(main_config), snapshots)
                        self.assertNotIn(secret, json.dumps(snapshots))
                        self.assertEqual(lads.sha_file(catalog), snapshots[str(catalog)])

                        environment = lads.child_environment(
                            role,
                            helper.KEY_ENV,
                            source=source_environment,
                            codex_home_path=origin,
                            home_mode="shared",
                            original_home=origin,
                        )
                        self.assertEqual(
                            {"PATH", "HTTPS_PROXY", "CODEX_HOME", helper.KEY_ENV[role]},
                            set(environment),
                        )
                        self.assertEqual(str(origin.resolve()), environment["CODEX_HOME"])
                        self.assertEqual(
                            role + "-only-sentinel",
                            environment[helper.KEY_ENV[role]],
                        )

    def test_main_config_allows_only_exact_source_and_common_trusted_addition(self):
        helper = load_module("helper_windows_shared_allow", ROOT / "scripts" / "lifeos-deepseek.py")
        with tempfile.TemporaryDirectory() as temporary:
            origin = Path(temporary) / "origin"
            origin.mkdir()
            old_project = Path(temporary) / "old-project"
            old_project.mkdir()
            repo = make_repo(Path(temporary) / "repo").repo
            linked = Path(temporary) / "linked"
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "add", "--detach", str(linked), "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
            roots = helper.authorized_project_roots(linked)
            self.assertEqual({linked.resolve(), repo.resolve()}, set(roots))
            old_key = str(old_project.resolve())
            if os.name == "nt":
                old_key = old_key.upper().replace("\\", "/")
            baseline = {old_key: {"trust_level": "trusted"}}
            self.write_main_config(origin, baseline)
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(origin)}, clear=False):
                expected = helper.main_config_snapshot()
                unchanged = helper.main_config_transition(expected, expected, roots)
                self.assertEqual("UNCHANGED", unchanged["status"])

                actual_projects = dict(baseline)
                for root in roots:
                    key = str(root.resolve())
                    if os.name == "nt":
                        key = key.replace("\\", "/") if root == linked else key.upper()
                    actual_projects[key] = {"trust_level": "trusted"}
                self.write_main_config(origin, actual_projects)
                actual = helper.main_config_snapshot()
                transition = helper.main_config_transition(expected, actual, roots)
            self.assertEqual("ALLOWED_TRUST_REGISTRATION", transition["status"])
            self.assertEqual(
                {os.path.normcase(str(root.resolve())) for root in roots},
                {os.path.normcase(name) for name in transition["added_project_root_names"]},
            )

    def test_main_config_rejects_unsafe_transitions_and_old_key_rename_with_addition(self):
        helper = load_module("helper_windows_shared_reject", ROOT / "scripts" / "lifeos-deepseek.py")
        with tempfile.TemporaryDirectory() as temporary:
            origin = Path(temporary) / "origin"
            origin.mkdir()
            old_project = Path(temporary) / "old-project"
            old_project.mkdir()
            repo = make_repo(Path(temporary) / "repo").repo
            run_root = repo / "work" / "lads" / "runs" / str(uuid.uuid4())
            run_root.mkdir(parents=True)
            source = repo / "source"
            source.mkdir()
            roots = (source.resolve(), repo.resolve())
            old_key = str(old_project.resolve())
            baseline = {old_key: {"trust_level": "trusted"}}
            self.write_main_config(origin, baseline)
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(origin)}, clear=False):
                expected = helper.main_config_snapshot()

                changed = dict(baseline)
                changed[old_key] = {"trust_level": "untrusted"}
                removed = {}
                unknown = dict(baseline)
                unknown[str(run_root.resolve())] = {"trust_level": "trusted"}
                extra_trust = dict(baseline)
                extra_trust[str(source.resolve())] = {
                    "trust_level": "trusted",
                    "unexpected": "field",
                }
                cases = {
                    "changed": (changed, "synthetic", roots),
                    "removed": (removed, "synthetic", roots),
                    "nonproject": (baseline, "changed-model", roots),
                    "unknown-run-root": (unknown, "synthetic", roots),
                    "extra-trust-field": (extra_trust, "synthetic", roots),
                }
                for name, (projects, model, allowed) in cases.items():
                    with self.subTest(name=name):
                        self.write_main_config(origin, projects, model=model)
                        actual = helper.main_config_snapshot()
                        with self.assertRaises(ValueError):
                            helper.main_config_transition(expected, actual, allowed)

                renamed_key = old_key.lower() if os.name == "nt" else old_key
                if renamed_key == old_key:
                    renamed_key = old_key + "/"
                renamed_and_added = {
                    renamed_key: {"trust_level": "trusted"},
                    str(repo.resolve()): {"trust_level": "trusted"},
                }
                self.write_main_config(origin, renamed_and_added)
                actual = helper.main_config_snapshot()
                with self.assertRaisesRegex(ValueError, "key spelling changed"):
                    helper.main_config_transition(expected, actual, roots)

    def test_main_config_snapshot_rejects_aliases_case_collisions_and_serializes_no_contents(self):
        helper = load_module("helper_windows_shared_snapshot", ROOT / "scripts" / "lifeos-deepseek.py")
        with tempfile.TemporaryDirectory() as temporary:
            origin = Path(temporary) / "origin"
            origin.mkdir()
            project = Path(temporary) / "project"
            project.mkdir()
            secret = "DO_NOT_SERIALIZE_PROJECT_VALUE"
            valid_key = str(project.resolve())
            if os.name == "nt":
                valid_key = valid_key.upper().replace("\\", "/")
            self.write_main_config(origin, {
                valid_key: {"trust_level": "trusted", "label": secret},
            })
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(origin)}, clear=False):
                snapshot = helper.main_config_snapshot()
                transition = helper.main_config_transition(snapshot, snapshot)
            serialized = json.dumps(snapshot) + json.dumps(transition)
            self.assertNotIn(secret, serialized)
            self.assertNotIn(valid_key, serialized)

            alias_cases = {
                "dot": str(project.resolve()) + os.sep + "." + os.sep + "child",
                "parent": str(project.resolve()) + os.sep + ".." + os.sep + "other",
            }
            for name, key in alias_cases.items():
                with self.subTest(alias=name):
                    self.write_main_config(origin, {key: {"trust_level": "trusted"}})
                    with mock.patch.dict(os.environ, {"CODEX_HOME": str(origin)}, clear=False):
                        with self.assertRaisesRegex(ValueError, "dot/path aliases"):
                            helper.main_config_snapshot()

            if os.name == "nt":
                first = str(project.resolve()).upper()
                second = first.lower().replace("\\", "/")
                self.write_main_config(origin, {
                    first: {"trust_level": "trusted"},
                    second: {"trust_level": "trusted"},
                })
                with mock.patch.dict(os.environ, {"CODEX_HOME": str(origin)}, clear=False):
                    with self.assertRaisesRegex(ValueError, "alias or collide"):
                        helper.main_config_snapshot()


class HelperAndSchemaTests(unittest.TestCase):
    def test_qa_explicit_key_mapping_and_schema(self):
        helper = load_module("helper_test", ROOT / "scripts" / "lifeos-deepseek.py")
        self.assertEqual("deepseek-flash", helper.MODELS["qa-tester"])
        self.assertEqual("DEEPSEEK_QA_API_KEY", helper.KEY_ENV["qa-tester"])
        schema = json.loads((ROOT / "schemas" / "lads-task-report.schema.json").read_text(encoding="utf-8"))
        self.assertIn("qa-tester", schema["properties"]["role"]["enum"])
        self.assertIn("source_snapshot_sha256", schema["properties"])
        rules = json.dumps(schema["allOf"])
        self.assertIn("SCAN_COMPLETE", rules)
        self.assertIn("PASS", rules)
        self.assertIn("source_snapshot_sha256", rules)

    def test_helper_adds_restrictive_overrides_and_terminal_json_protocol(self):
        helper = load_module("helper_protocol_test", ROOT / "scripts" / "lifeos-deepseek.py")
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            profile = home / "reviewer-deepseek.config.toml"
            catalog = home / "models.json"
            profile.write_text("", encoding="utf-8")
            catalog.write_text('{"models":[]}', encoding="utf-8")
            main_config = home / "config.toml"
            main_config.write_text('model = "synthetic"\n', encoding="utf-8")
            before = main_config.read_bytes()
            with mock.patch.object(
                helper,
                "check",
                return_value=(home, profile, catalog, "read-only"),
            ), mock.patch.object(
                helper,
                "authority_inventory",
                return_value={
                    role: {"profile_sha256": "d" * 64, "catalog_sha256": "e" * 64}
                    for role in helper.MODELS
                },
            ), mock.patch.object(helper.shutil, "which", return_value=sys.executable):
                argv, _snapshots = helper.command(
                    "reviewer", ROOT, home, platform_name="posix",
                )
            self.assertEqual(before, main_config.read_bytes())
        self.assertIn("sandbox_workspace_write.writable_roots=[]", argv)
        self.assertIn("sandbox_workspace_write.network_access=false", argv)
        self.assertIn("sandbox_workspace_write.exclude_tmpdir_env_var=true", argv)
        self.assertIn("sandbox_workspace_write.exclude_slash_tmp=true", argv)
        self.assertFalse(any(item.startswith('projects."') for item in argv))
        developer = next(item for item in argv if item.startswith("developer_instructions="))
        self.assertIn("ORCHESTRATED OUTPUT PROTOCOL", developer)
        self.assertIn("LAST assistant message must be one plain JSON object only", developer)
        self.assertIn("Do not add metadata", developer)

    def test_authorized_project_roots_use_git_linkage_and_case_dedup(self):
        helper = load_module("helper_roots_test", ROOT / "scripts" / "lifeos-deepseek.py")
        with tempfile.TemporaryDirectory() as temporary:
            repo = make_repo(Path(temporary) / "repo").repo
            nested = repo / "nested"
            nested.mkdir()
            roots = helper.authorized_project_roots(nested)
            self.assertEqual((nested.resolve(), repo.resolve()), roots)

            linked = Path(temporary) / "linked"
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "add", "--detach", str(linked), "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue((linked / ".git").is_file())
            linked_roots = helper.authorized_project_roots(linked)
            self.assertEqual({linked.resolve(), repo.resolve()}, set(linked_roots))
            if os.name == "nt":
                case_roots = helper.authorized_project_roots(Path(str(linked).upper()))
                self.assertEqual(linked_roots, case_roots)

    def test_authorized_project_roots_reject_invalid_git_home_root_wildcard_and_reparse(self):
        helper = load_module("helper_roots_reject_test", ROOT / "scripts" / "lifeos-deepseek.py")
        with tempfile.TemporaryDirectory() as temporary:
            plain = Path(temporary) / "plain"
            plain.mkdir()
            invalid = Path(temporary) / "invalid"
            invalid.mkdir()
            (invalid / ".git").write_text("not a valid gitdir link\n", encoding="utf-8")
            repo = make_repo(Path(temporary) / "repo").repo
            link = Path(temporary) / "repo-link"
            make_directory_link(link, repo)
            with mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(Path(temporary).resolve())}):
                with self.assertRaises(ValueError):
                    helper.authorized_project_roots(plain)
            for value in (
                invalid,
                Path.home(),
                Path(Path.cwd().anchor),
                Path(str(repo) + "*"),
                link,
            ):
                with self.subTest(value=str(value)):
                    with self.assertRaises(ValueError):
                        helper.authorized_project_roots(value)

    def test_demo_manifest_has_exact_three_file_synthetic_source_and_windows_guidance(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = lads.create_demo_manifest(Path(temporary))
            files = lads.inventory_tree(manifest.repo)
            self.assertEqual({".gitignore", "add.py", "tests/test_add.py"}, set(files))
            self.assertIn("PowerShell", manifest.task)
            self.assertIn("pwsh", manifest.task)
            self.assertIn("do not use a default bash stub", manifest.task)
            self.assertIn("Run only the one prescribed Python -B test command", manifest.task)
            self.assertIn("never traverse the parent repository", manifest.task)
            self.assertIn("read .codex, user, or private data", manifest.task)
            self.assertEqual(("add.py",), manifest.allowed_paths)


if __name__ == "__main__":
    unittest.main()
