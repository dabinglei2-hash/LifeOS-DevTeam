"""Memory/static tests for the credential-free app-server executor."""
from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lads_automation import rpc

COMMAND = "python -B -m unittest discover -s tests/automation -v"


class FakeProcess:
    def __init__(self, argv=None, cwd=None, environment=None, **_kwargs):
        self.argv = argv
        self.cwd = cwd
        self.environment = environment
        self.sent = []
        self.closed = False

    def send(self, value):
        self.sent.append(value)

    def receive_id(self, request_id, _deadline):
        if request_id.startswith("stage3-init-"):
            return {"userAgent": "synthetic"}
        return {
            "exitCode": 0,
            "stdout": '{"checks":[]}',
            "stderr": "",
        }

    def close(self):
        self.closed = True


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.executor = object.__new__(rpc.CodexJsonRpcExecutor)
        self.executor.work_root = self.root
        self.executor.allowed_commands = frozenset({COMMAND})
        self.executor.codex_executable = "C:/synthetic/codex.exe"
        self.executor.maximum_timeout = 300.0
        self.executor.process_factory = FakeProcess
        self.executor._lock = rpc.threading.Lock()
        self.executor._processes = set()
        self.executor.command_argv = {
            COMMAND: (
                "C:/synthetic/python.exe",
                "-B",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests/automation",
                "-v",
            )
        }
        self.executor.runtime_environment = {"PATH": "C:/synthetic/bin"}
        self.executor._runtime_executables = {
            "codex": "C:/synthetic/codex.exe",
            "python": "C:/synthetic/python.exe",
        }
        self.executor.runtime_metadata_value = {
            "policy": "sanitized-original-runtime-metadata-v1",
            "environment_names": ["PATH"],
            "codex_home_preserved": False,
            "executables": [],
        }
        self.executor._safe_path = (
            lambda value, label, directory: Path(value)
        )

    def tearDown(self):
        self.executor = None

    def test_executor_sends_only_initialize_and_command_exec(self):
        instances = []

        def factory(*args, **kwargs):
            instance = FakeProcess(*args, **kwargs)
            instances.append(instance)
            return instance

        self.executor.process_factory = factory
        result = self.executor.run(
            command=COMMAND,
            cwd=self.root / "build",
            timeout=10,
            network_access=False,
            writable_roots=(self.root / "build", self.root / "tmp"),
            environment={"LIFEOS_BASE_URL": "http://127.0.0.1:1234"},
        )
        instance = instances[0]
        self.assertEqual(
            ["initialize", "initialized", "command/exec"],
            [item["method"] for item in instance.sent],
        )
        self.assertTrue(
            instance.sent[0]["params"]["capabilities"]["experimentalApi"]
        )
        self.assertEqual(
            [
                "C:/synthetic/codex.exe",
                "--enable",
                "windows_sandbox_service",
                "app-server",
            ],
            instance.argv,
        )
        command_params = instance.sent[2]["params"]
        self.assertIsInstance(command_params["command"], list)
        self.assertEqual("C:/synthetic/python.exe", command_params["command"][0])
        self.assertEqual(
            {"LIFEOS_BASE_URL": "http://127.0.0.1:1234"},
            command_params["env"],
        )
        self.assertNotIn("environment", command_params)
        self.assertEqual("workspaceWrite", command_params["sandboxPolicy"]["type"])
        self.assertFalse(command_params["sandboxPolicy"]["networkAccess"])
        self.assertTrue(instance.closed)
        self.assertEqual(0, result["exit_code"])
        self.assertEqual(
            "sanitized-original-runtime-metadata-v1",
            result["runtime"]["policy"],
        )

    def test_arbitrary_command_network_and_sensitive_environment_are_rejected(self):
        with self.assertRaisesRegex(rpc.RpcExecutionError, "allowlist"):
            self.executor._command_params(
                command="git push origin main",
                cwd=self.root / "build",
                timeout=10,
                network_access=False,
                writable_roots=(self.root / "build",),
                environment={},
            )
        with self.assertRaisesRegex(rpc.RpcExecutionError, "network_access=false"):
            self.executor._command_params(
                command=COMMAND,
                cwd=self.root / "build",
                timeout=10,
                network_access=True,
                writable_roots=(self.root / "build",),
                environment={},
            )
        with self.assertRaisesRegex(rpc.RpcExecutionError, "environment override"):
            self.executor._command_params(
                command=COMMAND,
                cwd=self.root / "build",
                timeout=10,
                network_access=False,
                writable_roots=(self.root / "build",),
                environment={"DEEPSEEK_BUILDER_API_KEY": "synthetic"},
            )

    def test_timeout_is_bounded_and_nonsecret(self):
        class TimeoutProcess(FakeProcess):
            def receive_id(self, request_id, _deadline):
                if request_id.startswith("stage3-init-"):
                    return {}
                raise TimeoutError("Bearer synthetic-secret-value timed out")

        self.executor.process_factory = TimeoutProcess
        result = self.executor.run(
            command=COMMAND,
            cwd=self.root / "build",
            timeout=1,
            network_access=False,
            writable_roots=(self.root / "build",),
            environment={},
        )
        self.assertTrue(result["timed_out"])
        self.assertEqual("TimeoutError", result["error_type"])
        self.assertNotIn("synthetic-secret-value", result["error"])

    def test_success_boolean_does_not_replace_real_exit_code(self):
        with self.assertRaisesRegex(rpc.RpcExecutionError, "integer exitCode"):
            rpc.CodexJsonRpcExecutor._receipt({"success": True}, "", "")

    def test_rpc_error_retains_redacted_full_terminal_evidence_without_claiming_exit(self):
        detail = "prefix " * 4000 + " Bearer synthetic-sensitive-value TERMINAL_FAILURE_MARKER"
        class FailedProcess(FakeProcess):
            def receive_id(self, request_id, _deadline):
                if request_id.startswith("stage3-init-"):
                    return {}
                raise rpc.RpcExecutionError("bounded", raw_detail=detail)
        self.executor.process_factory = FailedProcess
        result = self.executor.run(command=COMMAND,cwd=self.root / "build",timeout=1,network_access=False,writable_roots=(self.root / "build",),environment={})
        self.assertIsNone(result["exit_code"])
        saved = self.root / result["error_evidence"]["path"]
        text = saved.read_text(encoding="utf-8")
        self.assertIn("TERMINAL_FAILURE_MARKER",text)
        self.assertNotIn("synthetic-sensitive-value",text)
        self.assertFalse(result["error_evidence"]["truncated"])

    def test_error_summary_keeps_tail_after_redaction(self):
        summary=rpc.bounded_error_text("x"*12000+" Bearer synthetic-sensitive-value FINAL_FAILURE",limit=800)
        self.assertLessEqual(len(summary),800)
        self.assertIn("FINAL_FAILURE",summary)
        self.assertNotIn("synthetic-sensitive-value",summary)

    def test_windows_tree_cleanup_uses_complete_handle_declarations(self):
        source = inspect.getsource(rpc._terminate_tree)
        for declaration in (
            "CreateToolhelp32Snapshot.argtypes",
            "CreateToolhelp32Snapshot.restype",
            "Process32FirstW.argtypes",
            "Process32FirstW.restype",
            "Process32NextW.argtypes",
            "Process32NextW.restype",
            "OpenProcess.argtypes",
            "OpenProcess.restype",
            "TerminateProcess.argtypes",
            "TerminateProcess.restype",
            "CloseHandle.argtypes",
            "CloseHandle.restype",
        ):
            self.assertIn(declaration, source)
        self.assertIn("process tree did not stop", source)
        self.assertIn("cleanup was incomplete", source)

    def test_runtime_environment_is_explicit_metadata_only(self):
        with self.assertRaisesRegex(rpc.RpcExecutionError, "unsupported name"):
            rpc.CodexJsonRpcExecutor._sanitized_runtime_environment(
                {"PATH": "C:/synthetic/bin", "DEEPSEEK_BUILDER_API_KEY": "hidden"}
            )
        safe = rpc.CodexJsonRpcExecutor._sanitized_runtime_environment(
            {
                "PATH": "C:/synthetic/bin",
                "CODEX_HOME": "C:/synthetic/home",
                "TEMP": "C:/synthetic/temp",
            }
        )
        self.assertEqual(
            {
                "CODEX_HOME": "C:/synthetic/home",
                "PATH": "C:/synthetic/bin",
                "TEMP": "C:/synthetic/temp",
            },
            safe,
        )


if __name__ == "__main__":
    unittest.main()
