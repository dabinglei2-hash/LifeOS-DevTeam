"""Synthetic, memory-only source adapters; actual disk/Git checks run separately."""
from __future__ import annotations

import copy
import io
import json
import unittest
import uuid
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch, MagicMock

from lads_knowledge import KnowledgeStore, KnowledgeError, ConflictError
from lads_knowledge.indexer import KnowledgeIndexer, SourcePolicy, sha256
from lads_knowledge.__main__ import main, parser, execute

HEAD = "a" * 40
ROOT = "repositories/" + "b" * 64
DOCS = ("docs/PRD.md", "docs/ARCHITECTURE.md")
STAMP = "2026-01-02T03:04:05Z"
TASK = "72111111-1111-4111-8111-111111111111"


def synthetic_handoff():
    """Real-shaped metadata, explicitly synthetic, no actual model calls."""
    stages, refs, runs = {}, [], []
    order = [("scanner", 0), ("reviewer", 0), ("builder", 1), ("qa-tester", 1),
             ("reviewer", 1), ("builder", 2), ("qa-tester", 2), ("reviewer", 2)]
    for index, (role, build) in enumerate(order, 1):
        name = f"stages/{index:04d}-{role}-build-{build}-attempt-1.json"
        stage = {"task_id": TASK, "role": role, "build": build, "attempt": 1,
                 "start_time": STAMP, "end_time": STAMP, "status": "PASS", "exit_code": 0,
                 "report": {"task_id": TASK, "role": role, "summary": "Synthetic report"},
                 "output_tail": "PRIVATE_RAW_OUTPUT_MUST_NOT_BE_INDEXED"}
        raw = json.dumps(stage).encode()
        stages[name] = raw
        refs.append({"path": name, "sha256": sha256(raw)})
        runs.append({"role": role, "provider": "lifeos_deepseek", "model": "deepseek-flash", "input_tokens": 10, "output_tokens": 2,
                     "preflight_proof": {"private_fixture": "NEVER_PERSIST_PREFLIGHT"}})
    handoff = {"task_id": TASK, "source_head": HEAD, "task_status": "AWAITING_MAIN_ACCEPTANCE",
               "start_time": STAMP, "end_time": STAMP, "stage_reports": refs, "role_runs": runs,
               "tests": [{"build": 1, "command": "python -B -m unittest", "status": "failed", "exit_code": 1},
                         {"build": 2, "command": "python -B -m unittest", "status": "completed", "exit_code": 0}],
               "files": [{"path": "lads_knowledge/example.py", "change": "added", "after_sha256": "c" * 64}],
               "reviewer_final": {"status": "APPROVED"}}
    return handoff, stages


class IndexerTests(unittest.TestCase):
    def setUp(self):
        self.store = KnowledgeStore.memory()
        self.pid = self.store.register_project("Synthetic engineering project", root_ref=ROOT)["id"]
        self.indexer = KnowledgeIndexer(self.store, self.pid, documents=DOCS)

    def tearDown(self):
        self.store.close()

    def rows(self, table):
        with self.store._engine.connect() as connection:
            return [dict(item) for item in connection.exec_driver_sql("SELECT * FROM " + table).mappings()]

    def snapshot(self, text, *, second=None, dirty=True):
        entries = []
        for path, value in zip(DOCS, (text, second)):
            if value is None:
                entries.append({"path": path, "status": "missing"})
            else:
                entries.append({"path": path, "status": "present", "text": value, "raw_sha": sha256(value.encode()), "dirty": dirty, "committed_sha": None if dirty else HEAD})
        return {"root_ref": ROOT, "observed_head": HEAD, "entries": entries}

    def search(self, term):
        return self.store.reader(self.pid).search_project_knowledge(term, limit=20)

    def test_atomic_batch_rolls_back_and_swallowed_error_poisons(self):
        for swallowed in (False, True):
            identity = str(uuid.uuid4())
            with self.assertRaises(ConflictError):
                with self.store.batch():
                    self.store.create_task(self.pid, "first", task_id=identity)
                    if swallowed:
                        try:
                            self.store.create_task(self.pid, "different", task_id=identity)
                        except ConflictError:
                            pass
                    else:
                        self.store.create_task(self.pid, "different", task_id=identity)
            self.assertEqual(self.rows("dev_tasks"), [])

    def test_explicit_binding_rootless_and_conflicting_root(self):
        pid = self.store.register_project("Rootless")["id"]
        indexer = KnowledgeIndexer(self.store, pid, documents=DOCS)
        with self.assertRaises(ConflictError):
            indexer.index_documents(**self.snapshot("content"))
        self.store.bind_project_root(pid, ROOT)
        self.store.bind_project_root(pid, ROOT)
        self.assertEqual(indexer.index_documents(**self.snapshot("content"))["updated_chunks"], 1)
        with self.assertRaises(ConflictError):
            self.store.bind_project_root(pid, "repositories/" + "c" * 64)

    def test_full_long_doc_tail_citations_update_shrink_and_noop(self):
        text = "alphaoldtoken " + "ordinary engineering text " * 1100 + " terminaltailneedle"
        first = self.indexer.index_documents(**self.snapshot(text))
        self.assertGreater(first["updated_chunks"], 3)
        match = self.search("terminaltailneedle")["items"][0]
        self.assertEqual(match["source_file"], "docs/prd.md")
        self.assertGreater(match["chunk_number"], 1)
        before = self.rows("knowledge_items")
        self.assertEqual(self.indexer.index_documents(**self.snapshot(text))["updated_chunks"], 0)
        self.assertEqual(before, self.rows("knowledge_items"))
        old_id = next(item["id"] for item in before if item["source_ref"] == "docs/prd.md")
        result = self.indexer.index_documents(**self.snapshot("newshortneedle"))
        self.assertGreater(result["tombstoned"], 2)
        self.assertEqual(self.search("alphaoldtoken")["count"], 0)
        self.assertEqual(self.search("terminaltailneedle")["count"], 0)
        self.assertEqual(self.search("newshortneedle")["items"][0]["id"], old_id)

    def test_missing_and_rejected_sources_do_not_remain_current(self):
        for state in ("missing", "rejected"):
            self.indexer.index_documents(**self.snapshot("olduniqueneedle"))
            snapshot = self.snapshot(None)
            snapshot["entries"][0]["status"] = state
            result = self.indexer.index_documents(**snapshot)
            self.assertEqual(self.search("olduniqueneedle")["count"], 0)
            self.assertEqual(len(result["errors"]), int(state == "rejected"))

    def test_invalid_partial_duplicate_wrongroot_batches_preserve_rows(self):
        self.indexer.index_documents(**self.snapshot("preserveneedle"))
        before = self.rows("knowledge_items")
        snapshots = [self.snapshot("replacement") for _ in range(5)]
        snapshots[0]["entries"].pop()
        snapshots[1]["root_ref"] = "repositories/" + "d" * 64
        snapshots[2]["entries"][1] = snapshots[2]["entries"][0]
        snapshots[3]["entries"][0]["raw_sha"] = "0" * 64
        snapshots[4]["entries"][0]["path"] = ".env"
        for value in snapshots:
            with self.assertRaises(KnowledgeError):
                self.indexer.index_documents(**value)
            self.assertEqual(before, self.rows("knowledge_items"))

    def test_committed_vs_working_provenance(self):
        self.indexer.index_documents(**self.snapshot("committedneedle", dirty=False))
        row = self.search("committedneedle")["items"][0]
        self.assertEqual(row["actual_content_commit_sha"], HEAD)
        self.indexer.index_documents(**self.snapshot("workingneedle"))
        row = self.search("workingneedle")["items"][0]
        self.assertIsNone(row["actual_content_commit_sha"])
        self.assertEqual(row["observed_head"], HEAD)
        self.assertTrue(row["dirty"])

    def test_policy_and_size_limits(self):
        for paths in [(".env",), ("docs/PRD.md", "docs/PRD.md"), ("docs/../auth.json",), ()]:
            with self.assertRaises(KnowledgeError):
                SourcePolicy(paths)
        with self.assertRaises(KnowledgeError):
            self.indexer.index_documents(**self.snapshot("x" * (512 * 1024 + 1)))

    def test_markers_ignore_fenced_examples_and_reconcile_history(self):
        decision = '<!-- lads:decision {"id":"real-decision","title":"Engineering decision","status":"accepted"} -->'
        issue = '<!-- lads:issue {"id":"real-issue","title":"Engineering issue","status":"open","severity":"medium"} -->'
        text = "```md\n" + decision + "\n```\n~~~\n" + issue + "\n~~~\n"
        self.indexer.index_documents(**self.snapshot(text))
        self.assertEqual(self.rows("architecture_decisions"), [])
        self.indexer.index_documents(**self.snapshot(decision + "\n" + issue))
        reader = self.store.reader(self.pid)
        self.assertEqual(reader.get_architecture_decisions()["items"][0]["source_file"], "docs/prd.md")
        self.assertEqual(reader.get_known_issues()["count"], 1)
        before = self.rows("architecture_decisions")
        self.indexer.index_documents(**self.snapshot(decision + "\n" + issue))
        self.assertEqual(before, self.rows("architecture_decisions"))
        self.indexer.index_documents(**self.snapshot("No markers now"))
        self.assertEqual(reader.get_architecture_decisions()["count"], 0)
        self.assertEqual(reader.get_known_issues()["count"], 0)
        self.assertEqual(self.rows("known_issues")[0]["status"], "inactive")

    def test_duplicate_markers_abort_whole_snapshot_and_bad_marker_invalidates(self):
        text = '<!-- lads:decision {"id":"shared","title":"Decision","status":"accepted"} -->'
        with self.assertRaises(ConflictError):
            self.indexer.index_documents(**self.snapshot(text, second=text))
        self.assertEqual(self.rows("knowledge_items"), [])
        self.indexer.index_documents(**self.snapshot("oldmarkerneedle"))
        result = self.indexer.index_documents(**self.snapshot('<!-- lads:decision {bad} -->'))
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(self.search("oldmarkerneedle")["count"], 0)

    def test_redaction_occurs_before_chunk_boundaries(self):
        text = "x " * 2995 + " api_key=sk-syntheticcredential123456789 test@example.invalid "
        self.indexer.index_documents(**self.snapshot(text))
        stored = " ".join(item["body"] for item in self.rows("knowledge_items"))
        self.assertNotIn("syntheticcredential", stored)
        self.assertNotIn("test@example.invalid", stored)

    def test_git_metadata_related_lookup_and_partial_windows_keep_history(self):
        row = {"sha": HEAD, "date": STAMP, "title": "Synthetic migrationfix", "files": ["lads_knowledge/example.py"], "files_truncated": False}
        self.assertEqual(self.indexer.index_git_records([row], root_ref=ROOT)["updated"], 1)
        self.assertEqual(self.indexer.index_git_records([row], root_ref=ROOT)["updated"], 0)
        self.indexer.index_git_records([], root_ref=ROOT)
        reader = self.store.reader(self.pid)
        self.assertEqual(reader.get_related_changes(commit_sha=HEAD)["items"][0]["type"], "git_commit")
        self.assertEqual(reader.get_related_changes(query="migrationfix")["count"], 1)

    def test_pr_empty_partial_and_strict_fields(self):
        snapshot = {"repository": "synthetic/repo", "repository_id": 123, "observed_at": STAMP, "complete": False, "items": []}
        self.assertEqual(self.indexer.index_pr_snapshot(snapshot, repository="synthetic/repo")["observed"], 0)
        item = {"number": 1, "title": "Synthetic PR", "state": "OPEN", "url": "https://github.com/synthetic/repo/pull/1",
                "head_sha": HEAD, "base_sha": HEAD, "merge_sha": None, "created_at": STAMP, "updated_at": STAMP}
        snapshot["items"] = [item]
        self.assertEqual(self.indexer.index_pr_snapshot(snapshot, repository="synthetic/repo")["updated"], 1)
        self.assertEqual(self.indexer.index_pr_snapshot(snapshot, repository="synthetic/repo")["updated"], 0)
        snapshot["items"] = []
        self.indexer.index_pr_snapshot(snapshot, repository="synthetic/repo")
        self.assertEqual(self.search("Synthetic PR")["count"], 1)
        snapshot["items"] = [{**item, "body": "Forbidden raw discussion"}]
        with self.assertRaises(KnowledgeError):
            self.indexer.index_pr_snapshot(snapshot, repository="synthetic/repo")

    def test_handoff_runs_failed_case_sources_and_immutable_replay(self):
        handoff, stages = synthetic_handoff()
        result = self.indexer.index_handoff(handoff, stages, root_ref=ROOT, requirement_summary="Synthetic rework")
        self.assertEqual(result["runs"], 8)
        self.assertEqual(result["failed_tests"], 1)
        self.assertEqual(len(self.rows("agent_runs")), 8)
        self.assertEqual(sum(row["input_tokens"] for row in self.rows("model_usage")), 80)
        self.assertEqual(self.rows("dev_tasks")[0]["state"], "review")
        self.assertIsNone(self.rows("dev_tasks")[0]["completed_time"])
        found = self.search("failed")["items"]
        self.assertTrue(any(item["source_ref"].startswith("handoffs/" + TASK + "/tests/") for item in found))
        persisted = json.dumps(self.rows("knowledge_items") + self.rows("task_events"))
        self.assertNotIn("PRIVATE_RAW_OUTPUT", persisted)
        self.assertNotIn("NEVER_PERSIST_PREFLIGHT", persisted)
        counts = [len(self.rows(table)) for table in ("agent_runs", "task_events", "test_runs", "model_usage")]
        self.assertEqual(self.indexer.index_handoff(handoff, stages, root_ref=ROOT, requirement_summary="Synthetic rework")["status"], "unchanged")
        self.assertEqual(counts, [len(self.rows(table)) for table in ("agent_runs", "task_events", "test_runs", "model_usage")])
        with self.assertRaises(ConflictError):
            self.indexer.index_handoff(handoff, stages, root_ref=ROOT, requirement_summary="Conflicting import summary")
        changed = copy.deepcopy(handoff)
        changed["task_status"] = "BLOCKED"
        with self.assertRaises(ConflictError):
            self.indexer.index_handoff(changed, stages, root_ref=ROOT, requirement_summary="Synthetic rework")

    def test_handoff_hash_and_late_conflict_roll_back_all_records(self):
        handoff, stages = synthetic_handoff()
        corrupted = dict(stages)
        corrupted[next(iter(stages))] += b" "
        with self.assertRaises(ConflictError):
            self.indexer.index_handoff(handoff, corrupted, root_ref=ROOT, requirement_summary="Synthetic")
        bad = copy.deepcopy(handoff)
        bad["tests"][1]["command"] = 123
        with self.assertRaises(KnowledgeError):
            self.indexer.index_handoff(bad, stages, root_ref=ROOT, requirement_summary="Synthetic")
        bad_metadata = copy.deepcopy(handoff)
        bad_metadata["role_runs"][0] = None
        with self.assertRaises(KnowledgeError):
            self.indexer.index_handoff(bad_metadata, stages, root_ref=ROOT, requirement_summary="Synthetic")
        bad_metadata["role_runs"][0] = {"role": "scanner", "provider": "deepseek", "model": "deepseek-flash", "input_tokens": True}
        with self.assertRaises(KnowledgeError):
            self.indexer.index_handoff(bad_metadata, stages, root_ref=ROOT, requirement_summary="Synthetic")
        for table in ("dev_tasks", "task_events", "agent_runs", "test_runs", "model_usage"):
            self.assertEqual(self.rows(table), [])

    def test_full_child_payload_changes_are_conflicts(self):
        task = self.store.create_task(self.pid, "Synthetic payload test")["id"]
        run = self.store.record_run(self.pid, task, role="builder", model_name="synthetic", build_number=1, attempt=1, status="done", started_at=STAMP)
        with self.assertRaises(ConflictError):
            self.store.record_run(self.pid, task, role="builder", model_name="synthetic", build_number=1, attempt=1, status="done", started_at="2026-01-03T00:00:00Z")
        test = self.store.record_test(self.pid, task, test_name="same", command="first", status="passed")
        with self.assertRaises(ConflictError):
            self.store.record_test(self.pid, task, test_name="same", command="second", status="passed", test_id=test["id"])
        change = self.store.record_change(self.pid, task, file_path="docs/a.md", change_type="modified", source_raw_sha="c" * 64)
        with self.assertRaises(ConflictError):
            self.store.record_change(self.pid, task, file_path="docs/a.md", change_type="modified", source_raw_sha="d" * 64, change_id=change["id"])
        self.store.record_usage(self.pid, task, run["id"], model_name="synthetic", input_tokens=None)
        with self.assertRaises(ConflictError):
            self.store.record_usage(self.pid, task, run["id"], model_name="synthetic", input_tokens=1)

    def test_cli_help_no_init_and_read_route_uses_reader_only(self):
        with patch("lads_knowledge.__main__.FileStore.open", side_effect=AssertionError("Unexpected writer")):
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exited:
                main(["--help"])
            self.assertEqual(exited.exception.code, 0)
            with patch("lads_knowledge.__main__._existing", return_value=Path("unused")), patch("lads_knowledge.__main__.KnowledgeReader.open") as factory:
                factory.return_value.__enter__.return_value.search_project_knowledge.return_value = {"items": []}
                result = execute(parser().parse_args(["search", "--project", self.pid, "--query", "test"]))
                self.assertEqual(result, {"items": []})
        output = io.StringIO()
        with redirect_stderr(output):
            self.assertEqual(main(["--credential", "synthetic-private-argument"]), 2)
        self.assertNotIn("synthetic-private-argument", output.getvalue())


if __name__ == "__main__":
    unittest.main()
