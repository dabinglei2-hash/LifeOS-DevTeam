"""Memory-only native-hook behavior tests."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

from lads_automation.hooks import handle_hook
from lads_automation.state import JournalError, JournalSessionCorrupt, MemoryJournal

TASK = "1c432bf8-ae19-4148-aef4-ed6f51ce7cc7"
ROOT = Path(__file__).resolve().parents[2]


def journal_with_task(*, ready=False, session_id="session-synthetic"):
    journal = MemoryJournal()
    journal.create_task(
        {
            "task_id": TASK,
            "status": "DELIVERY_READY" if ready else "QA_REVIEW_VERIFIED",
            "ready": ready,
            "session_id": session_id,
            "event_ids": [],
        }
    )
    return journal


class HookTests(unittest.TestCase):
    def test_invalid_existing_journal_is_a_bounded_warning_and_absent_is_no_write(self):
        spec = importlib.util.spec_from_file_location(
            "lads_hook_entrypoint_test",
            ROOT / "scripts/lads_hook.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch.object(
            module,
            "default_automation_root",
            return_value=mock.Mock(exists=lambda: False),
        ):
            journal, warning = module._journal_and_warning()
        self.assertIsNone(journal)
        self.assertIsNone(warning)
        with mock.patch.object(
            module,
            "default_automation_root",
            return_value=mock.Mock(exists=lambda: True),
        ), \
                mock.patch.object(module.FileJournal, "open", side_effect=JournalError("corrupt")):
            journal, warning = module._journal_and_warning()
        self.assertIsNone(journal)
        self.assertIn("invalid", warning)
        self.assertNotIn("corrupt", warning)

    def test_journal_warning_preserves_pre_tool_denial(self):
        spec = importlib.util.spec_from_file_location(
            "lads_hook_entrypoint_warning_test",
            ROOT / "scripts/lads_hook.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "exec_command",
            "tool_input": {"command": "git push origin main"},
        }
        fake_stdin = type("Stdin", (), {})()
        fake_stdin.buffer = io.BytesIO(
            json.dumps(payload, ensure_ascii=True).encode("utf-8")
        )
        output = io.StringIO()
        with mock.patch.object(
            module,
            "_journal_and_warning",
            return_value=(None, module._WARNING),
        ), mock.patch.object(module.sys, "stdin", fake_stdin), mock.patch.object(
            module.sys,
            "stdout",
            output,
        ):
            self.assertEqual(module.main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(
            result["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )
        self.assertIn(module._WARNING, result["hookSpecificOutput"]["additionalContext"])

    def test_pre_tool_denies_destructive_git_and_direct_storage_clear(self):
        commands = (
            "git reset --hard HEAD",
            "git push origin main",
            "git -C . reset --hard HEAD",
            "git -C . push origin main",
            "git --git-dir .git push origin main",
            "git.exe reset --hard HEAD",
            "git -C . clean -fd",
            "git --git-dir=.git reset --hard",
            "git -C . push --force origin HEAD",
            "cmd /c git push origin main",
        )
        for command in commands:
            with self.subTest(command=command):
                result = handle_hook(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "exec_command",
                        "tool_input": {"command": command},
                    }
                )
                self.assertEqual(
                    result["hookSpecificOutput"]["permissionDecision"],
                    "deny",
                )
        storage = handle_hook(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "exec_command",
                "tool_input": {
                    "command": 'node -e "localStorage.clear();"',
                },
            }
        )
        self.assertEqual(
            storage["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_unrecognized_git_global_option_requires_review(self):
        commands = (
            "git -c safe=true --unknown push origin main",
            "git --foo=bar reset --hard",
            "cmd /c git --unknown clean -fd",
        )
        for command in commands:
            with self.subTest(command=command):
                result = handle_hook(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "exec_command",
                        "tool_input": {"command": command},
                    }
                )
                self.assertIn(
                    "unrecognized global option",
                    result["hookSpecificOutput"]["additionalContext"],
                )
                self.assertNotIn("permissionDecision", result["hookSpecificOutput"])

    def test_pre_tool_does_not_block_harmless_quoted_test_text(self):
        result = handle_hook(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "exec_command",
                "tool_input": {
                    "command": 'echo "fixture: git reset --hard is denied"',
                },
            }
        )
        self.assertEqual(result, {})

    def test_pre_tool_denies_command_writes_to_credentials_and_business_db(self):
        commands = (
            "echo SECRET > .env",
            "Set-Content -Path .env -Value SECRET",
            'powershell -Command "Set-Content -Path .env -Value SECRET"',
            'sqlite3 notion.db "INSERT INTO users VALUES (1)"',
        )
        for command in commands:
            with self.subTest(command=command):
                result = handle_hook(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "exec_command",
                        "tool_input": {"command": command},
                    }
                )
                self.assertEqual(
                    result["hookSpecificOutput"]["permissionDecision"],
                    "deny",
                )

    def test_pre_tool_does_not_mistake_quoted_fixture_for_private_write(self):
        result = handle_hook(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "exec_command",
                "tool_input": {
                    "command": 'echo "fixture: sqlite3 notion.db open(\'.env\')"',
                },
            }
        )
        self.assertEqual(result, {})

    def test_post_tool_records_only_status_hash_metadata_without_raw_output(self):
        journal = journal_with_task()
        result = handle_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "session-synthetic",
                "tool_name": "exec_command",
                "tool_input": {"command": "PRIVATE COMMAND TEXT"},
                "tool_response": {
                    "exit_code": 0,
                    "stdout": "PRIVATE RAW OUTPUT",
                    "stderr": "PRIVATE ERROR OUTPUT",
                },
            },
            journal,
        )
        self.assertEqual(result, {})
        rows = journal.list_events(TASK, event_type="hook_post_tool")
        self.assertEqual(len(rows), 1)
        payload = rows[0]["payload"]
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["exit_code"], 0)
        self.assertIn("response_sha256", payload)
        encoded = repr(payload)
        self.assertNotIn("PRIVATE", encoded)
        self.assertNotIn("command", encoded.casefold())
        self.assertNotIn("session-synthetic", encoded)
        self.assertIn("session_binding_sha256", payload)

    def test_unknown_exit_is_not_success(self):
        journal = journal_with_task()
        handle_hook(
            {
                "hook_event_name": "PostToolUse",
                "task_id": TASK,
                "session_id": "session-synthetic",
                "tool_response": {"status": "maybe"},
            },
            journal,
        )
        payload = journal.list_events(TASK, event_type="hook_post_tool")[0]["payload"]
        self.assertIsNone(payload["exit_code"])
        self.assertEqual(payload["status"], "unknown")

    def test_missing_empty_wrong_typed_and_boolean_exit_are_unknown(self):
        responses = (
            {},
            {"exit_code": None},
            {"exit_code": "0"},
            {"exit_code": True},
            {"exit_code": 0.0},
        )
        for response in responses:
            with self.subTest(response=response):
                journal = journal_with_task()
                handle_hook(
                    {
                        "hook_event_name": "PostToolUse",
                        "session_id": "session-synthetic",
                        "tool_response": response,
                    },
                    journal,
                )
                payload = journal.list_events(
                    TASK, event_type="hook_post_tool"
                )[0]["payload"]
                self.assertIsNone(payload["exit_code"])
                self.assertEqual(payload["status"], "unknown")

    def test_task_id_without_matching_session_is_not_logged(self):
        journal = journal_with_task()
        for session_id in (None, "unknown-private-session"):
            with self.subTest(session_id=session_id):
                payload = {
                    "hook_event_name": "PostToolUse",
                    "task_id": TASK,
                    "tool_response": {"exit_code": 0},
                }
                if session_id is not None:
                    payload["session_id"] = session_id
                handle_hook(payload, journal)
        self.assertEqual(journal.list_events(TASK), [])

    def test_unbound_chat_is_not_logged(self):
        journal = journal_with_task()
        handle_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "unknown-private-session",
                "tool_response": {"exit_code": 0},
            },
            journal,
        )
        self.assertEqual(journal.list_events(TASK), [])

    def test_native_subagent_stop_is_recorded_but_deepseek_cli_is_not(self):
        journal = journal_with_task()
        handle_hook(
            {
                "hook_event_name": "SubagentStop",
                "task_id": TASK,
                "session_id": "session-synthetic",
                "agent_type": "native-reviewer",
            },
            journal,
        )
        events = journal.list_events(TASK)
        self.assertEqual([row["event_type"] for row in events], ["native_subagent_stop"])
        self.assertTrue(events[0]["payload"]["native_event"])
        handle_hook(
            {
                "hook_event_name": "SubagentStop",
                "task_id": TASK,
                "session_id": "session-synthetic",
                "agent_type": "deepseek-builder-cli",
                "provider": "deepseek",
            },
            journal,
        )
        self.assertEqual(
            len(journal.list_events(TASK, event_type="native_subagent_stop")),
            1,
        )
        handle_hook(
            {
                "hook_event_name": "SubagentStop",
                "task_id": TASK,
                "session_id": "session-synthetic",
                "role": "builder-deepseek",
                "event_source": "controller",
            },
            journal,
        )
        self.assertEqual(
            len(journal.list_events(TASK, event_type="native_subagent_stop")),
            1,
        )
        handle_hook(
            {
                "hook_event_name": "worker_completed",
                "task_id": TASK,
                "session_id": "session-synthetic",
                "role": "builder",
                "provider": "deepseek",
                "worker_type": "cli",
            },
            journal,
        )
        self.assertEqual(
            len(journal.list_events(TASK, event_type="native_subagent_stop")),
            1,
        )
        self.assertEqual(
            len(journal.list_events(TASK, event_type="worker_completed")),
            0,
        )

    def test_stop_requests_one_continuation_then_records_warning_without_loop(self):
        journal = journal_with_task(ready=False)
        first = handle_hook(
            {
                "hook_event_name": "Stop",
                "task_id": TASK,
                "session_id": "session-synthetic",
                "stop_hook_active": False,
            },
            journal,
        )
        self.assertEqual(first["decision"], "block")
        repeated = handle_hook(
            {
                "hook_event_name": "Stop",
                "task_id": TASK,
                "session_id": "session-synthetic",
                "stop_hook_active": False,
            },
            journal,
        )
        self.assertEqual(repeated, {})
        second = handle_hook(
            {
                "hook_event_name": "Stop",
                "task_id": TASK,
                "session_id": "session-synthetic",
                "stop_hook_active": True,
            },
            journal,
        )
        self.assertEqual(second, {})
        self.assertEqual(
            len(journal.list_events(TASK, event_type="stop_continuation_requested")),
            1,
        )
        self.assertEqual(
            len(journal.list_events(TASK, event_type="stop_gate_warning")),
            1,
        )

    def test_delete_private_credential_path_is_denied(self):
        result = handle_hook(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "write_file",
                "cwd": "C:/workspace",
                "tool_input": {"file_path": "C:/workspace/auth.json"},
            }
        )
        self.assertEqual(
            result["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_ambiguous_session_returns_bounded_warning_without_writes(self):
        journal = MemoryJournal()
        identities = (
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
        )
        for identity in identities:
            journal.create_task(
                {
                    "task_id": identity,
                    "status": "TASK_CREATED",
                    "ready": False,
                    "session_id": "session-synthetic",
                    "event_ids": [],
                }
            )
        result = handle_hook(
            {
                "hook_event_name": "Stop",
                "session_id": "session-synthetic",
            },
            journal,
        )
        encoded = repr(result)
        self.assertIn("ambiguous", encoded)
        self.assertNotIn("session-synthetic", encoded)
        self.assertNotIn("decision", result)
        for identity in identities:
            self.assertEqual(journal.list_events(identity), [])

    def test_corrupt_journal_denies_pre_tool_use_without_leaking_details(self):
        class CorruptJournal:
            def resolve_session(self, _session):
                raise JournalSessionCorrupt(2)

        result = handle_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "session-synthetic",
                "tool_name": "exec_command",
                "tool_input": {"command": "git status --short"},
            },
            CorruptJournal(),
        )
        encoded = repr(result)
        self.assertEqual(
            "deny",
            result["hookSpecificOutput"]["permissionDecision"],
        )
        self.assertIn("unreadable task state", encoded)
        self.assertIn("Unreadable task count: 2", encoded)
        self.assertNotIn("session-synthetic", encoded)


if __name__ == "__main__":
    unittest.main()
