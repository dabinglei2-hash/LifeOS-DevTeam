"""Real synthetic subprocess and pure diagnostic regressions; no API calls."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("lads_runtime_test", ROOT / "scripts/lads_runtime.py")
runtime = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runtime
spec.loader.exec_module(runtime)
SECRETS = (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._-]{12,}"), re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"))


class DiagnosticsTests(unittest.TestCase):
    def test_terminal_stderr_survives_large_stdout_and_redaction(self):
        secret = "sk-" + "x" * 32
        value = runtime.ProcessResult(1, "source-prefix\n" * 5000, "HTTP 503 FINAL " + secret, False, False)
        result = runtime.failure_diagnostics(value, SECRETS)
        self.assertIn("HTTP 503 FINAL", result["error_tail"])
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual("connection", runtime.classify_failure(value, result))

    def test_last_twenty_errors_include_terminal_nested_error(self):
        events = [{"type": "error", "code": str(i)} for i in range(30)]
        events.append({"type": "turn.failed", "error": {"code": "rate_limited", "message": "actual-last"}})
        value = runtime.ProcessResult(1, "\n".join(json.dumps(x) for x in events), "", False, False)
        result = runtime.failure_diagnostics(value, SECRETS)
        self.assertEqual(20, len(result["error_events"]))
        self.assertEqual("rate_limited", result["error_events"][-1]["code"])
        self.assertIn("actual-last", result["error_tail"])

    def test_auth_word_in_rate_limit_message_is_not_authentication(self):
        value = runtime.ProcessResult(1, '{"type":"error","code":"rate_limited","message":"auth word only"}', "", False, False)
        self.assertEqual("connection", runtime.classify_failure(value, runtime.failure_diagnostics(value, SECRETS)))

    def test_explicit_authentication_and_timeout_are_separate(self):
        value = runtime.ProcessResult(1, '{"type":"error","status":401}', "", False, False)
        self.assertEqual("authentication", runtime.classify_failure(value, runtime.failure_diagnostics(value, SECRETS)))
        value.timed_out = True
        self.assertEqual("timeout", runtime.classify_failure(value, runtime.failure_diagnostics(value, SECRETS)))

    def test_partial_leading_line_is_not_persisted(self):
        tail = runtime._Tail(128)
        tail.append(("private-line-" + "x" * 200 + "\nTERMINAL\n").encode())
        self.assertEqual("TERMINAL\n", tail.text())


class ExecutionTests(unittest.TestCase):
    def test_unicode_and_cache_environment(self):
        command = [sys.executable, "-B", "-c", "import os;print('中文 ✓');print(os.environ['PYTHONDONTWRITEBYTECODE'])"]
        result = runtime.run_process(command, ROOT, "", 10)
        self.assertEqual(0, result.exit_code)
        self.assertIn("中文 ✓", result.stdout)
        self.assertIn("1", result.stdout)

    def test_bounded_output_retains_real_terminal_error(self):
        command = [sys.executable, "-B", "-c", "import sys;print('x'*2000);print('END_REAL_ERROR');sys.exit(7)"]
        result = runtime.run_process(command, ROOT, "", 10, maximum=256)
        self.assertEqual(7, result.exit_code)
        self.assertTrue(result.bounded_output)
        self.assertIn("END_REAL_ERROR", result.stdout)
        self.assertLessEqual(len(result.stdout.encode()), 256)

    def test_completed_parent_cannot_leave_child_from_venv_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "survived.txt"
            child = Path(directory) / "child.py"
            child.write_text("import time,pathlib;time.sleep(0.8);pathlib.Path(" + repr(str(marker)) + ").write_text('bad')", encoding="utf-8")
            parent = Path(directory) / "parent.py"
            parent.write_text("import subprocess,sys;subprocess.Popen([sys.executable,'-B'," + repr(str(child)) + "])", encoding="utf-8")
            result = runtime.run_process([sys.executable, "-B", str(parent)], ROOT, "", 10)
            self.assertEqual(0, result.exit_code)
            import time
            time.sleep(1)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell boundary")
    def test_explicit_powershell_unicode(self):
        shell = shutil.which("pwsh") or shutil.which("powershell")
        self.assertTrue(shell)
        result = runtime.run_process([shell, "-NoProfile", "-NonInteractive", "-Command",
                                     "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); [Console]::Write('中文 ✓')"], ROOT, "", 15)
        self.assertEqual(0, result.exit_code)
        self.assertEqual("中文 ✓", result.stdout)


if __name__ == "__main__":
    unittest.main()
