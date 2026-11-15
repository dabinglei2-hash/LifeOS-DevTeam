"""Synthetic regressions for the observed REST/GraphQL merge race and resume."""
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest import mock

from lads_automation import github_release as release
import test_github_release as fixtures


class MergeRaceTests(unittest.TestCase):
    def setUp(self):
        self.controller = release.ReleaseController.__new__(release.ReleaseController)
        self.auth = fixtures.GitHubSafetyTests._authorization()
        self.auth.risk_flags = ()
        self.state = fixtures.GitHubSafetyTests._state()
        self.pr = fixtures.GitHubSafetyTests._pr()
        self.merged = fixtures.GitHubSafetyTests._pr(merged=True, merge_commit_sha="c" * 40)
        self.gate = SimpleNamespace(
            task_id=self.auth.task_id, main_ack_sha256="1" * 64,
            docs_receipt_sha256="2" * 64, index_receipt_sha256="3" * 64,
            bundle=SimpleNamespace(source_head="a" * 40, candidate_sha256=self.auth.candidate_sha256, expected_commands=(), worker_stage_receipts=[]),
        )

    def poll(self, second):
        c = self.controller
        c.poll_attempts = 1
        c._controller_status_expectation = mock.Mock(return_value=("digest", {}))
        c._get_pr = mock.Mock(side_effect=[self.pr, second])
        c._verify_prerequisites = mock.Mock(side_effect=release.ReleaseConflict("OPEN-to-MERGED"))
        return c._poll_merge(self.auth, self.gate, self.state, self.pr)

    def test_poll_rest_open_graphql_merged_rechecks_native_proof(self):
        c = self.controller
        c._verify_native_merge_evidence = mock.Mock()
        self.assertIs(self.merged, self.poll(self.merged))
        c._verify_native_merge_evidence.assert_called_once_with(self.auth, self.gate, self.state, self.merged)

    def test_poll_conflict_with_still_open_pr_is_not_suppressed(self):
        c = self.controller
        c._verify_native_merge_evidence = mock.Mock()
        with self.assertRaisesRegex(release.ReleaseConflict, "OPEN-to-MERGED"):
            self.poll(self.pr)
        c._verify_native_merge_evidence.assert_not_called()

    def test_poll_merged_without_durable_native_proof_stays_blocked(self):
        with self.assertRaises(release.ReleaseBlocked) as error:
            self.poll(self.merged)
        self.assertEqual("AUTO_MERGE_EVIDENCE_CONFLICT", error.exception.reason)

    def prepare_publish(self, prior):
        c = self.controller
        c.store = SimpleNamespace(lock=lambda _task: nullcontext())
        c._state = mock.Mock(return_value=self.state)
        c._verify_repository = mock.Mock()
        c.repo_root = "synthetic-repo"
        c.run_root = "synthetic-run"
        c.journal = None
        c.knowledge_history_loader = mock.Mock()
        c.github = SimpleNamespace(resolve_repository=mock.Mock(return_value={}))
        c._bind_repository_resolution = mock.Mock(return_value={})
        c._save = mock.Mock(side_effect=lambda state, status, **values: state.update(status=status, **values) or state)
        c._get_pr = mock.Mock(return_value=prior)
        c._fetch_base = mock.Mock(side_effect=release.ReleaseBlocked("Remote base branch drifted"))
        c._capture = mock.Mock()
        c._push_once = mock.Mock()
        c._finish_reconciled_merge = mock.Mock(return_value={"status": "MERGED"})

    def publish(self):
        with mock.patch.object(release, "load_authorization", return_value=self.auth), \
             mock.patch.object(release, "validate_local_gate", return_value=self.gate), \
             mock.patch.object(release, "_validate_authorization_against_gate"), \
             mock.patch.object(release, "audit_changes", return_value=release.ChangeAudit((), ())):
            return self.controller.publish("synthetic-authorization", knowledge_home="synthetic-knowledge", knowledge_db="synthetic.sqlite3", project_id="synthetic-project")

    def test_resume_merged_before_premerge_base_fetch_and_no_push(self):
        c = self.controller
        self.prepare_publish(self.merged)
        self.assertEqual("MERGED", self.publish()["status"])
        c._finish_reconciled_merge.assert_called_once()
        c._fetch_base.assert_not_called()
        c._capture.assert_not_called()
        c._push_once.assert_not_called()

    def test_resume_open_pr_keeps_original_base_drift_gate(self):
        self.prepare_publish(self.pr)
        with self.assertRaisesRegex(release.ReleaseBlocked, "base branch drifted"):
            self.publish()
        self.controller._finish_reconciled_merge.assert_not_called()
        self.controller._push_once.assert_not_called()

    def test_invalid_pinned_pr_number_cannot_reconcile(self):
        self.prepare_publish(self.merged)
        self.state["pr_number"] = True
        with self.assertRaises(release.ReleaseConflict):
            self.publish()
        self.controller._get_pr.assert_not_called()
        self.controller._finish_reconciled_merge.assert_not_called()

    def finish(self, ci="SUCCESS", verify_native=True):
        c = self.controller
        if verify_native:
            c._verify_native_merge_evidence = mock.Mock()
        c._branch_protection = mock.Mock()
        c._check_snapshot = mock.Mock(return_value=(ci, []))
        c._verify_merge = mock.Mock(return_value="c" * 40)
        c._postmerge_knowledge = mock.Mock(return_value={"status": "MERGED"})
        store = mock.Mock(spec=release.KnowledgeStore)
        return c._finish_reconciled_merge(self.auth, self.gate, self.state, self.merged, project_id="synthetic-project", knowledge_store=store)

    def test_reconciliation_requires_native_proof_before_any_index(self):
        with self.assertRaises(release.ReleaseBlocked) as error:
            self.finish(verify_native=False)
        self.assertEqual("AUTO_MERGE_EVIDENCE_CONFLICT", error.exception.reason)
        self.controller._check_snapshot.assert_not_called()
        self.controller._postmerge_knowledge.assert_not_called()

    def test_reconciliation_failed_ci_never_writes_merge_receipt(self):
        with self.assertRaises(release.ReleaseBlocked):
            self.finish(ci="CI_FAILED")
        self.controller._verify_merge.assert_not_called()
        self.controller._postmerge_knowledge.assert_not_called()

    def test_reconciliation_success_rechecks_protection_ci_and_exact_merge(self):
        self.assertEqual("MERGED", self.finish()["status"])
        c = self.controller
        c._branch_protection.assert_called_once_with(self.auth)
        c._check_snapshot.assert_called_once_with(self.auth, self.state["commit_sha"])
        c._verify_merge.assert_called_once_with(self.auth, self.merged)
        self.assertEqual("c" * 40, c._postmerge_knowledge.call_args.kwargs["merge_commit_sha"])


if __name__ == "__main__":
    unittest.main()
