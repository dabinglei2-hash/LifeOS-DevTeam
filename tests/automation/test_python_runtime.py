"""Runtime preparation preserves provider authority and all original PATH entries."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock
from lads_automation.team import prepare_python_runtime


class PythonRuntimeTests(unittest.TestCase):
    def test_selected_interpreter_is_idempotent_and_does_not_change_auth(self):
        original = os.pathsep.join(("first-runtime-directory", "second-runtime-directory"))
        with mock.patch.dict(os.environ, {"PATH": original, "DEEPSEEK_SCANNER_API_KEY": "synthetic-auth-sentinel"}, clear=True), \
                mock.patch("lads_automation.team.shutil.which", return_value="trusted-codex"):
            runtime = prepare_python_runtime()
            prepared = os.environ["PATH"]
            self.assertEqual(str(Path(sys.executable).absolute()), runtime)
            self.assertTrue(prepared.endswith(os.pathsep + original))
            self.assertEqual(str(Path(runtime).parent), prepared.split(os.pathsep)[0])
            self.assertEqual(runtime, prepare_python_runtime())
            self.assertEqual(prepared, os.environ["PATH"])
            self.assertEqual("synthetic-auth-sentinel", os.environ["DEEPSEEK_SCANNER_API_KEY"])

    def test_codex_shadowing_is_rejected_before_mutation(self):
        with mock.patch.dict(os.environ, {"PATH": "original-path"}, clear=True), \
                mock.patch("lads_automation.team.shutil.which", side_effect=("trusted-codex", "other-codex")):
            with self.assertRaises(RuntimeError):
                prepare_python_runtime()
            self.assertEqual("original-path", os.environ["PATH"])
