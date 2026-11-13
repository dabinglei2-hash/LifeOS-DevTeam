"""Native acceptance must require explicit network/model-test opt-in."""
from pathlib import Path
import importlib.util
import os
import unittest
from types import SimpleNamespace
from unittest import mock

PATH = Path(__file__).resolve().parents[2] / "tests/acceptance/lads-native-hooks.py"


def load_entrypoint():
    spec = importlib.util.spec_from_file_location("lads_native_acceptance_test", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NativeEntrypointTests(unittest.TestCase):
    def test_import_does_not_start_a_model_process(self):
        with mock.patch("subprocess.Popen") as start:
            load_entrypoint()
        start.assert_not_called()

    def test_missing_explicit_opt_in_does_not_execute(self):
        module = load_entrypoint()
        with mock.patch.object(module.sys, "argv", [str(PATH), "--namespace", "synthetic"]), \
                mock.patch.object(module, "execute_live") as execute:
            with self.assertRaises(SystemExit) as caught:
                module.main()
        self.assertEqual(2, caught.exception.code)
        execute.assert_not_called()

    def test_unbounded_timeout_is_rejected_before_execution(self):
        module = load_entrypoint()
        argv = [str(PATH), "--namespace", "synthetic", "--execute-live", "--timeout", "99999"]
        with mock.patch.object(module.sys, "argv", argv), \
                mock.patch.object(module, "execute_live") as execute:
            with self.assertRaises(SystemExit) as caught:
                module.main()
        self.assertEqual(2, caught.exception.code)
        execute.assert_not_called()

    def test_actual_failure_result_cannot_return_success(self):
        module = load_entrypoint()
        argv = [str(PATH), "--namespace", "synthetic", "--execute-live"]
        with mock.patch.object(module.sys, "argv", argv), \
                mock.patch.object(module, "os", SimpleNamespace(name="nt")), \
                mock.patch.object(module, "execute_live", return_value={"status": "FAIL"}):
            self.assertEqual(1, module.main())


    def test_non_windows_execution_is_rejected_before_starting_model(self):
        module = load_entrypoint()
        argv = [str(PATH), "--namespace", "synthetic", "--execute-live"]
        with mock.patch.object(module.sys, "argv", argv), \
                mock.patch.object(module, "os", SimpleNamespace(name="posix")), \
                mock.patch.object(module, "execute_live") as execute:
            with self.assertRaises(SystemExit) as caught:
                module.main()
        self.assertEqual(2, caught.exception.code)
        execute.assert_not_called()


class NativeJournalSelectionTests(unittest.TestCase):
    def load_hook(self):
        path = PATH.parents[2] / "scripts/lads_hook.py"
        spec = importlib.util.spec_from_file_location("lads_native_journal_selection", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_explicit_namespace_never_opens_default_journal(self):
        module = self.load_hook()
        with mock.patch.dict(os.environ, {"LADS_HOOK_ACCEPTANCE_NAMESPACE": "synthetic"}), \
                mock.patch.object(module, "acceptance_root", return_value=mock.Mock(exists=lambda: True)), \
                mock.patch.object(module, "default_automation_root") as default, \
                mock.patch.object(module.FileJournal, "open", return_value=object()) as open_journal:
            journal, warning = module._journal_and_warning()
        self.assertIsNotNone(journal)
        self.assertIsNone(warning)
        default.assert_not_called()
        open_journal.assert_called_once_with(acceptance_namespace="synthetic")

    def test_absent_namespace_journal_does_not_fall_back_or_create(self):
        module = self.load_hook()
        with mock.patch.dict(os.environ, {"LADS_HOOK_ACCEPTANCE_NAMESPACE": "synthetic"}), \
                mock.patch.object(module, "acceptance_root", return_value=mock.Mock(exists=lambda: False)), \
                mock.patch.object(module.FileJournal, "open") as open_journal:
            self.assertEqual((None, None), module._journal_and_warning())
        open_journal.assert_not_called()

    def test_default_runtime_keeps_its_existing_storage_path(self):
        module = self.load_hook()
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(module, "default_automation_root", return_value=mock.Mock(exists=lambda: True)), \
                mock.patch.object(module.FileJournal, "open", return_value=object()) as open_journal:
            journal, warning = module._journal_and_warning()
        self.assertIsNotNone(journal)
        self.assertIsNone(warning)
        open_journal.assert_called_once_with()
