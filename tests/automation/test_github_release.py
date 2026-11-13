"""Synthetic tests for the Stage 4 GitHub release controller.

The GitHub transport here is deliberately in-memory. These tests prove local
authorization, Git, state, and fail-closed behavior; they do not claim a live
GitHub API or auto-merge result.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from lads_automation import github_release as release

TASK = "9f638d64-27db-4c3e-9048-389ea147796c"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *args: str, check: bool = True) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    if check and process.returncode:
        raise AssertionError(process.stderr or process.stdout)
    return process.stdout.strip()


def configure(repo: Path) -> None:
    git(repo, "config", "user.name", "LADS Synthetic")
    git(repo, "config", "user.email", "lads-synthetic@invalid.example")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "core.autocrlf", "false")


class GitFixture:
    def __init__(self, base: Path) -> None:
        self.base = base
        self.origin = base / "origin.git"
        self.seed = base / "seed"
        self.repo = base / "repo"
        git(base, "init", "--bare", "--initial-branch=main", str(self.origin))
        git(base, "init", "--initial-branch=main", str(self.seed))
        configure(self.seed)
        (self.seed / "app.js").write_bytes(b"const value = 'base';\n")
        (self.seed / "README.md").write_bytes(b"# Synthetic\n")
        git(self.seed, "add", "--", "app.js", "README.md")
        git(self.seed, "commit", "-m", "base")
        self.base_sha = git(self.seed, "rev-parse", "HEAD")
        git(self.seed, "remote", "add", "origin", str(self.origin))
        git(self.seed, "push", "-u", "origin", "main")
        git(base, "clone", str(self.origin), str(self.repo))
        configure(self.repo)
        # Clone checkout may pre-date the fixture's core.autocrlf=false setting.
        # Rewrite to the exact base blob bytes before creating dirty changes.
        (self.repo / "app.js").write_bytes(b"const value = 'base';\n")
        self.old_app = (self.repo / "app.js").read_bytes()
        self.new_app = b"const value = 'approved';\n"
        self.new_doc = b"# Approved release\n"
        (self.repo / "app.js").write_bytes(self.new_app)
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "new.md").write_bytes(self.new_doc)
        (self.repo / "foreign.txt").write_text("foreign dirty data\n", encoding="utf-8")

    def authorization(
        self,
        *,
        source_app: str | None = None,
        git_app: str | None = None,
    ) -> dict:
        return {
            "schema": release.AUTHORIZATION_SCHEMA,
            "task_id": TASK,
            "source_head": self.base_sha,
            "candidate_sha256": "a" * 64,
            "repository": release.CANONICAL_REPOSITORY,
            "base_branch": "main",
            "base_sha": self.base_sha,
            "short_description": "stage-four-release",
            "demand": "Publish reviewed engineering automation.",
            "summary": "Add evidence-bound GitHub release automation.",
            "reason": "Controller-authorized engineering release.",
            "risk_flags": [],
            "candidate_files": [
                {
                    "path": "docs/new.md",
                    "source_before_sha256": None,
                    "git_before_sha256": None,
                    "after_sha256": sha(self.new_doc),
                    "mode": "100644",
                }
            ],
            "prerequisite_files": [
                {
                    "path": "app.js",
                    "source_before_sha256": source_app or sha(self.old_app),
                    "git_before_sha256": git_app or sha(self.old_app),
                    "after_sha256": sha(self.new_app),
                    "mode": "100644",
                }
            ],
            "deletions": [],
            "binary_assets": [],
        }


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self.pulls: list[dict] = []
        self.graphql_response: dict = {"data": {}}

    def api(self, method: str, route: str, body=None):
        self.calls.append((method, route, body))
        if method == "GET" and "/pulls?" in route:
            return list(self.pulls)
        if method == "POST" and route.endswith("/pulls"):
            assert isinstance(body, dict)
            value = {
                "number": 7,
                "node_id": "PR_node_7",
                "html_url": "https://github.com/dabinglei2-hash/LifeOS/pull/7",
                "state": "open",
                "draft": False,
                "mergeable": True,
                "head": {"sha": "b" * 40, "ref": body["head"]},
                "base": {"ref": body["base"]},
                "merged": False,
                "merge_commit_sha": None,
            }
            self.pulls = [value]
            return value
        if method == "GET" and route.endswith("/pulls/7"):
            return self.pulls[0]
        if method == "GET" and route.endswith("/status"):
            return {"statuses": [{"context": "LADS / controller", "state": "success"}]}
        if method == "GET" and "/check-runs" in route:
            return {"check_runs": []}
        if method == "POST" and "/statuses/" in route:
            return {"state": "success"}
        raise AssertionError((method, route))

    def graphql(self, query: str, variables):
        self.calls.append(("GRAPHQL", query, dict(variables)))
        return self.graphql_response


class CanonicalRouteRunner(release.CommandRunner):
    """Test transport seam that reports the audited route while using a local Git fixture."""

    def __init__(self, *, push_urls=None, fetch_urls=None):
        super().__init__()
        self.fetch_urls = list(
            fetch_urls or ["https://github.com/dabinglei2-hash/LifeOS.git"]
        )
        self.push_urls = list(
            push_urls or ["https://github.com/dabinglei2-hash/LifeOS.git"]
        )

    def run(self, argv, *, cwd=None, timeout=30.0, stdin=None):
        args = list(argv)
        if args[1:3] == ["remote", "get-url"]:
            urls = self.push_urls if "--push" in args else self.fetch_urls
            return release.CommandResult(
                tuple(args),
                0,
                ("\n".join(urls) + ("\n" if urls else "")).encode("utf-8"),
                b"",
            )
        return super().run(argv, cwd=cwd, timeout=timeout, stdin=stdin)


class ReleaseStoreTests(unittest.TestCase):
    def test_private_root_has_owned_marker_deny_all_and_atomic_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = release.ReleaseStore(Path(directory) / "releases").initialize()
            self.assertEqual(b"*\n", (store.root / ".gitignore").read_bytes())
            with store.lock(TASK):
                state = store.save(TASK, {"task_id": TASK, "status": "INITIALIZED"})
                self.assertEqual(state["schema"], release.RELEASE_SCHEMA)
                loaded = store.load(TASK)
                self.assertEqual("INITIALIZED", loaded["status"])
                event = store.record_event(TASK, "release_test", {"status": "INITIALIZED"})
                replay = store.record_event(TASK, "release_test", {"status": "INITIALIZED"})
                self.assertFalse(event["idempotent"])
                self.assertTrue(replay["idempotent"])

    def test_task_and_relative_paths_reject_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            store = release.ReleaseStore(Path(directory) / "releases").initialize()
            with self.assertRaises(release.ReleaseBoundaryError):
                store.task_dir("../outside", create=True)
        for value in ("../x", "docs\\x", "/abs", "C:/x"):
            with self.subTest(value=value):
                with self.assertRaises(release.ReleaseAuthorizationError):
                    release._safe_relative(value, "path")

    def test_reparse_check_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = release.ReleaseStore(Path(directory) / "releases").initialize()
            with mock.patch.object(
                release,
                "_reject_reparse_chain",
                side_effect=OSError("synthetic reparse"),
            ):
                with self.assertRaises(release.ReleaseBoundaryError):
                    store.task_dir(TASK, create=True)

    def test_missing_state_and_prior_artifacts_never_reset_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store = release.ReleaseStore(Path(directory) / "releases").initialize()
            controller = release.ReleaseController.__new__(release.ReleaseController)
            controller.store = store
            authorization = SimpleNamespace(
                task_id=TASK,
                authorization_sha256="1" * 64,
                source_head="a" * 40,
                candidate_sha256="b" * 64,
                repository=release.CANONICAL_REPOSITORY,
                base_branch="main",
                base_sha="c" * 40,
                branch="codex/task",
            )
            with store.lock(TASK):
                self.assertEqual("INITIALIZED", controller._state(authorization)["status"])
            damaged = store.task_dir(TASK) / "state.json"
            damaged.write_bytes(b'{"schema":"broken"}\n')
            original = damaged.read_bytes()
            with store.lock(TASK):
                with self.assertRaises(release.ReleaseBoundaryError) as raised:
                    controller._state(authorization)
            self.assertNotIsInstance(raised.exception, release.ReleaseStateNotFound)
            self.assertEqual(original, damaged.read_bytes())

            historical_task = str(uuid.uuid4())
            store.record_event(historical_task, "release_test", {"status": "SAVED"})
            historical = SimpleNamespace(**{**vars(authorization), "task_id": historical_task})
            events_path = store.task_dir(historical_task) / "events.json"
            events_before = events_path.read_bytes()
            with store.lock(historical_task):
                with self.assertRaises(release.ReleaseBoundaryError):
                    controller._state(historical)
            self.assertFalse((store.task_dir(historical_task) / "state.json").exists())
            self.assertEqual(events_before, events_path.read_bytes())

    def test_state_and_events_are_strict_bounded_and_preserved_on_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = release.ReleaseStore(Path(directory) / "releases").initialize()
            state = store.save(TASK, {"task_id": TASK, "status": "INITIALIZED"})
            self.assertEqual(0, state["network_failure_total"])
            state_path = store.task_dir(TASK) / "state.json"
            valid_state = state_path.read_bytes()
            for payload in (
                b'{"schema":"a","schema":"b"}\n',
                b'{"schema":"lads-github-release-state-v1","task_id":"'
                + TASK.encode("ascii")
                + b'","status":"INITIALIZED","ready":false,"value":NaN}\n',
                b'{"schema":"lads-github-release-state-v1","task_id":"'
                + TASK.encode("ascii")
                + b'","status":"FUTURE_STATE","ready":false}\n',
                b"x" * (release.MAX_STATE_BYTES + 1),
            ):
                with self.subTest(payload=payload[:20]):
                    state_path.write_bytes(payload)
                    before = state_path.read_bytes()
                    with self.assertRaises(release.ReleaseBoundaryError):
                        store.load(TASK)
                    with self.assertRaises(release.ReleaseBoundaryError):
                        store.save(TASK, {"task_id": TASK, "status": "INITIALIZED"})
                    self.assertEqual(before, state_path.read_bytes())

            state_path.write_bytes(valid_state)
            with mock.patch.object(
                release,
                "_is_reparse",
                side_effect=lambda path: Path(path).name == "state.json",
            ):
                with self.assertRaises(release.ReleaseBoundaryError):
                    store.load(TASK)
            store.record_event(TASK, "release_test", {"status": "INITIALIZED"})
            events_path = store.task_dir(TASK) / "events.json"
            with mock.patch.object(
                release,
                "_is_reparse",
                side_effect=lambda path: Path(path).name == "events.json",
            ):
                with self.assertRaises(release.ReleaseBoundaryError):
                    store.record_event(TASK, "release_test", {"status": "INITIALIZED"})
            events_path.write_bytes(b'{"version":1,"version":1,"events":[]}\n')
            before = events_path.read_bytes()
            with self.assertRaises(release.ReleaseBoundaryError):
                store.record_event(TASK, "release_test", {"status": "INITIALIZED"})
            self.assertEqual(before, events_path.read_bytes())
            store._atomic_write(
                events_path,
                (
                    release._canonical({"version": 1, "events": []})
                    + "\n"
                ).encode("utf-8"),
            )
            with mock.patch.object(release, "MAX_RELEASE_EVENTS", 1):
                store.record_event(TASK, "release_test", {"status": "READY"})
                before_bound = events_path.read_bytes()
                with self.assertRaises(release.ReleaseBoundaryError):
                    store.record_event(TASK, "release_test", {"status": "SECOND"})
                self.assertEqual(before_bound, events_path.read_bytes())

    def test_network_failure_history_is_bounded_and_total_survives_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            store = release.ReleaseStore(Path(directory) / "releases").initialize()
            state = store.save(TASK, {"task_id": TASK, "status": "INITIALIZED"})
            controller = release.ReleaseController.__new__(release.ReleaseController)
            for index in range(release.MAX_STORED_NETWORK_FAILURES + 2):
                controller._record_network_failure(state, {"operation": "synthetic", "attempt": index})
            self.assertEqual(
                release.MAX_STORED_NETWORK_FAILURES,
                len(state["network_failures"]),
            )
            saved = store.save(TASK, state)
            resumed = store.load(TASK)
            self.assertEqual(saved["network_failure_total"], resumed["network_failure_total"])
            controller._record_network_failure(resumed, {"operation": "synthetic"})
            self.assertEqual(
                saved["network_failure_total"] + 1,
                resumed["network_failure_total"],
            )


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = GitFixture(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_strict_authorization_digest_and_modes(self):
        raw = self.fixture.authorization()
        value = release.parse_authorization(raw)
        self.assertEqual(value.authorization_sha256, release.parse_authorization(value.as_dict()).authorization_sha256)
        forged = {**raw, "authorization_sha256": "f" * 64}
        with self.assertRaises(release.ReleaseAuthorizationError):
            release.parse_authorization(forged)
        raw["candidate_files"][0]["mode"] = "120000"
        with self.assertRaises(release.ReleaseAuthorizationError):
            release.parse_authorization(raw)

    def test_case_colliding_paths_are_rejected(self):
        raw = self.fixture.authorization()
        raw["candidate_files"].append(
            {
                "path": "Docs/new.md",
                    "source_before_sha256": None,
                    "git_before_sha256": None,
                "after_sha256": sha(self.fixture.new_doc),
                "mode": "100644",
            }
        )
        with self.assertRaisesRegex(release.ReleaseAuthorizationError, "case-colliding"):
            release.parse_authorization(raw)

    def test_secret_and_private_path_audit_reports_no_values(self):
        flagged = self.fixture.repo / "safe.txt"
        synthetic = "gh" + "p_" + ("A" * 24)
        flagged.write_text("value=" + synthetic + "\n", encoding="utf-8")
        env_path = self.fixture.repo / ".env"
        env_path.write_text("synthetic=not-a-real-credential\n", encoding="utf-8")
        binary = self.fixture.repo / "asset.bin"
        binary.write_bytes(b"\0synthetic")
        raw = self.fixture.authorization()
        raw["candidate_files"].extend(
            [
                {
                    "path": "safe.txt",
                    "source_before_sha256": None,
                    "git_before_sha256": None,
                    "after_sha256": sha(flagged.read_bytes()),
                    "mode": "100644",
                },
                {
                    "path": ".env",
                    "source_before_sha256": None,
                    "git_before_sha256": None,
                    "after_sha256": sha(env_path.read_bytes()),
                    "mode": "100644",
                },
                {
                    "path": "asset.bin",
                    "source_before_sha256": None,
                    "git_before_sha256": None,
                    "after_sha256": sha(binary.read_bytes()),
                    "mode": "100644",
                },
            ]
        )
        authorization = release.parse_authorization(raw)
        audit = release.audit_changes(self.fixture.repo, authorization)
        self.assertIn("secret_material", audit.risk_flags)
        self.assertIn("private_or_secret_path", audit.risk_flags)
        self.assertIn("unknown_binary", audit.risk_flags)
        serialized = json.dumps(audit.as_dict())
        self.assertNotIn(synthetic, serialized)
        self.assertNotIn("not-a-real-credential", serialized)

    def test_high_risk_flags_include_workflow_and_delete(self):
        workflow = self.fixture.repo / ".github" / "workflows"
        workflow.mkdir(parents=True)
        path = workflow / "ci.yml"
        path.write_text("name: test\n", encoding="utf-8")
        raw = self.fixture.authorization()
        raw["candidate_files"].append(
            {
                "path": ".github/workflows/ci.yml",
                "source_before_sha256": None,
                "git_before_sha256": None,
                "after_sha256": sha(path.read_bytes()),
                "mode": "100644",
            }
        )
        readme_sha = sha((self.fixture.repo / "README.md").read_bytes())
        raw["deletions"] = [
            {
                "path": "README.md",
                "source_before_sha256": readme_sha,
                "git_before_sha256": readme_sha,
            }
        ]
        audit = release.audit_changes(self.fixture.repo, release.parse_authorization(raw))
        self.assertIn("workflow_policy_change", audit.risk_flags)
        self.assertIn("deletion", audit.risk_flags)

    def test_history_rewrite_command_is_high_risk(self):
        script = self.fixture.repo / "unsafe.sh"
        script.write_text("git " + "push --force origin main\n", encoding="utf-8")
        raw = self.fixture.authorization()
        raw["candidate_files"].append(
            {
                "path": "unsafe.sh",
                "source_before_sha256": None,
                "git_before_sha256": None,
                "after_sha256": sha(script.read_bytes()),
                "mode": "100644",
            }
        )
        audit = release.audit_changes(self.fixture.repo, release.parse_authorization(raw))
        self.assertIn("history_rewrite_or_force", audit.risk_flags)

    def test_declared_flags_cannot_waive_critical_findings(self):
        flagged = self.fixture.repo / "safe.txt"
        synthetic = "gh" + "p_" + ("B" * 24)
        flagged.write_text("value=" + synthetic + "\n", encoding="utf-8")
        raw = self.fixture.authorization()
        raw["candidate_files"].append(
            {
                "path": "safe.txt",
                "source_before_sha256": None,
                "git_before_sha256": None,
                "after_sha256": sha(flagged.read_bytes()),
                "mode": "100644",
            }
        )
        audit = release.audit_changes(
            self.fixture.repo,
            release.parse_authorization(raw),
        )
        critical, undeclared = release._audit_review_gaps(
            audit,
            ("secret_material",),
        )
        self.assertIn("credential_or_key_material", critical)
        self.assertEqual((), undeclared)


class GateEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run_root = Path(self.temp.name) / "run"
        for relative in ("source", "builder", "qa/build-1", "qa/build-2"):
            (self.run_root / relative).mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def proof(self, role, workspace):
        mode = "workspace-write" if role == "builder" else "read-only"
        prefix = [
            "codex",
            "--profile",
            f"{role}-deepseek",
            "-C",
            str(workspace),
            "--sandbox",
            mode,
            "--ask-for-approval",
            "never",
            "--model",
            "deepseek-flash",
        ]
        config_sha256 = {
            str(self.run_root / "profiles" / role / "models.json"): sha(
                ("models-" + role).encode("utf-8")
            )
        }
        runtime_home = str(self.run_root / "runtime-homes" / role)
        return {
            "role": role,
            "home_mode": "shared",
            "debug_argv": [
                *prefix,
                "debug",
                "prompt-input",
                "LADS effective permission preflight",
            ],
            "worker_prefix_length": len(prefix),
            "worker_prefix_sha256": sha(release._canonical(prefix).encode("utf-8")),
            "exit_code": 0,
            "stdout_sha256": sha(("stdout-" + role).encode("utf-8")),
            "permissions": {
                "role": role,
                "sandbox_mode": mode,
                "network": "restricted",
                "approval": "never",
                "writable_roots": [str(workspace)] if role == "builder" else [],
            },
            "config_sha256": config_sha256,
            "runtime_home": runtime_home,
            "home_sha256": sha(("home-" + role).encode("utf-8")),
            "authority_sha256": sha(("authority-" + role).encode("utf-8")),
        }

    def worker_receipts(self, roles, statuses):
        receipts = []
        handoff_refs = []
        role_runs = []
        builds = []
        build = 0
        for index, (role, status) in enumerate(zip(roles, statuses), 1):
            if role == "builder":
                build += 1
            if role == "builder":
                stage_build = build
            elif role in {"qa-tester", "reviewer"} and index > 1:
                stage_build = build
            elif role == "reviewer":
                stage_build = 0
            else:
                stage_build = 0
            if role in {"scanner", "reviewer"} and stage_build == 0:
                workspace = self.run_root / "source"
            elif role == "builder":
                workspace = self.run_root / "builder"
            else:
                workspace = self.run_root / "qa" / f"build-{stage_build}"
            proof = self.proof(role, workspace)
            mode = "workspace-write" if role == "builder" else "read-only"
            stage_metadata = {
                "role": role,
                "provider": "lifeos_deepseek",
                "model": "deepseek-flash",
                "sandbox": mode,
                "start_time": "2026-11-04T00:00:00+00:00",
                "end_time": "2026-11-04T00:00:01+00:00",
                "input_tokens": index,
                "output_tokens": index * 2,
                "config_snapshot_sha256": sha(
                    release._canonical(proof["config_sha256"]).encode("utf-8")
                ),
                "home_mode": proof["home_mode"],
                "home_sha256": proof["home_sha256"],
                "authority_sha256": proof["authority_sha256"],
            }
            prefix = proof["debug_argv"][: proof["worker_prefix_length"]]
            raw_stage = {
                "task_id": TASK,
                "role": role,
                "status": status,
                "build": stage_build,
                "attempt": 1,
                "command": [*prefix, "exec", "--json", "--ephemeral", "-"],
                "exit_code": 0,
                "metadata": {
                    **stage_metadata,
                    "preflight_proof": proof,
                    "runtime_home": proof["runtime_home"],
                },
                "report": {"task_id": TASK, "role": role, "status": status},
            }
            relative = f"stages/{index:02d}-{role}-build-{stage_build}.json"
            path = self.run_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            data = (release._canonical(raw_stage) + "\n").encode("utf-8")
            path.write_bytes(data)
            reference = {"path": relative, "sha256": sha(data)}
            handoff_refs.append(reference)
            role_runs.append(raw_stage["metadata"])
            receipts.append(
                {
                    "task_id": TASK,
                    "role": role,
                    "status": status,
                    "provider": "lifeos_deepseek",
                    "model": "deepseek-flash",
                    "stage_ref": dict(reference),
                    "stage_metadata": stage_metadata,
                    "result": SimpleNamespace(exit_code=0),
                }
            )
            builds.append(stage_build)
        self.handoff_refs = handoff_refs
        self.role_runs = role_runs
        self.final_build = max(builds)
        return tuple(receipts)

    def bundle(self, **updates):
        selected_receipts = updates.pop("worker_stage_receipts", None)
        if selected_receipts is None:
            receipts = self.worker_receipts(
                ("scanner", "reviewer", "builder", "qa-tester", "reviewer"),
                ("SCAN_COMPLETE", "APPROVED", "BUILT", "PASS", "APPROVED"),
            )
        else:
            receipts = selected_receipts
        base = SimpleNamespace(
            task_id=TASK,
            run_root=self.run_root,
            candidate_sha256="c" * 64,
            expected_commands=release.unit_qa.STAGE4_QA_COMMANDS,
            worker_stage_receipts=receipts,
            qa_stage_sha256=receipts[-2]["stage_ref"]["sha256"],
            reviewer_stage_sha256=receipts[-1]["stage_ref"]["sha256"],
            qa_receipt={"status": "PASS"},
            reviewer_receipt={"status": "APPROVED"},
            task_spec=mock.Mock(),
            receipts=mock.Mock(),
            handoff={
                "builds": self.final_build,
                "stage_reports": list(self.handoff_refs),
                "role_runs": list(self.role_runs),
            },
        )
        for key, value in updates.items():
            setattr(base, key, value)
        return base

    def rewrite_stage(self, bundle, index, mutate):
        receipt = bundle.worker_stage_receipts[index]
        path = bundle.run_root / receipt["stage_ref"]["path"]
        stage = json.loads(path.read_text(encoding="utf-8"))
        mutate(stage)
        bundle.handoff["role_runs"][index] = stage["metadata"]
        data = (release._canonical(stage) + "\n").encode("utf-8")
        path.write_bytes(data)
        digest = sha(data)
        receipt["stage_ref"]["sha256"] = digest
        reference = next(
            item
            for item in bundle.handoff["stage_reports"]
            if item["path"] == receipt["stage_ref"]["path"]
        )
        reference["sha256"] = digest
        if index == len(bundle.worker_stage_receipts) - 1:
            bundle.reviewer_stage_sha256 = digest
        if index == len(bundle.worker_stage_receipts) - 2:
            bundle.qa_stage_sha256 = digest

    def evidence(self, **updates):
        value = {
            "task_id": TASK,
            "candidate_sha256": "c" * 64,
            "commands": list(release.unit_qa.STAGE4_QA_COMMANDS),
            "qa_review_verified": True,
            "whole_task_complete": False,
            "reviewer_independent": True,
            "simulated": False,
            "acceptance_mode": "independent",
        }
        value.update(updates)
        return value

    def test_ready_boolean_cannot_replace_persisted_evidence(self):
        bundle = self.bundle()
        for field, value in (
            ("qa_review_verified", False),
            ("reviewer_independent", False),
            ("simulated", True),
            ("acceptance_mode", "main-authorized-exception"),
            ("commands", ["caller-pass"]),
        ):
            with self.subTest(field=field):
                with self.assertRaises(release.ReleaseGateError):
                    release._validate_persisted_evidence(
                        {"status": "DELIVERY_READY", "ready": True, "evidence": self.evidence(**{field: value})},
                        bundle,
                    )

    def test_realistic_five_and_eight_stage_histories_are_accepted(self):
        histories = (
            (
                ("scanner", "reviewer", "builder", "qa-tester", "reviewer"),
                ("SCAN_COMPLETE", "APPROVED", "BUILT", "PASS", "APPROVED"),
            ),
            (
                (
                    "scanner",
                    "reviewer",
                    "builder",
                    "qa-tester",
                    "reviewer",
                    "builder",
                    "qa-tester",
                    "reviewer",
                ),
                (
                    "SCAN_COMPLETE",
                    "APPROVED",
                    "BUILT",
                    "FAIL",
                    "CHANGES_REQUESTED",
                    "BUILT",
                    "PASS",
                    "APPROVED",
                ),
            ),
        )
        for roles, statuses in histories:
            with self.subTest(roles=roles):
                receipts = self.worker_receipts(roles, statuses)
                bundle = self.bundle(
                    worker_stage_receipts=receipts,
                    qa_stage_sha256=receipts[-2]["stage_ref"]["sha256"],
                    reviewer_stage_sha256=receipts[-1]["stage_ref"]["sha256"],
                    handoff={
                        "builds": self.final_build,
                        "stage_reports": list(self.handoff_refs),
                        "role_runs": list(self.role_runs),
                    },
                )
                with mock.patch.object(
                    release,
                    "validate_stage_acceptance",
                    return_value=mock.Mock(),
                ) as acceptance:
                    release._validate_worker_receipts(bundle)
                    acceptance.assert_called_once_with(bundle.task_spec, bundle.receipts)

    def test_worker_history_rejects_missing_roles_bad_binding_and_provider(self):
        incomplete = self.worker_receipts(
            ("scanner", "reviewer", "builder", "qa-tester"),
            ("SCAN_COMPLETE", "APPROVED", "BUILT", "PASS"),
        )
        with self.assertRaises(release.ReleaseGateError):
            release._validate_worker_receipts(
                self.bundle(
                    worker_stage_receipts=incomplete,
                    handoff={
                        "builds": self.final_build,
                        "stage_reports": list(self.handoff_refs),
                        "role_runs": list(self.role_runs),
                    },
                )
            )
        wrong_final = list(self.bundle().worker_stage_receipts)
        wrong_final[-2] = {
            **wrong_final[-2],
            "stage_ref": {
                **wrong_final[-2]["stage_ref"],
                "sha256": "f" * 64,
            },
        }
        with self.assertRaises(release.ReleaseGateError):
            release._validate_worker_receipts(
                self.bundle(worker_stage_receipts=tuple(wrong_final))
            )
        bundle = self.bundle()
        bundle.worker_stage_receipts[0]["provider"] = "openai"
        with self.assertRaises(release.ReleaseGateError):
            release._validate_worker_receipts(bundle)

    def test_raw_stage_preflight_and_exact_workspace_guards(self):
        builder = self.bundle()
        self.rewrite_stage(
            builder,
            2,
            lambda stage: stage["metadata"]["preflight_proof"]["permissions"].__setitem__(
                "writable_roots", [str(self.run_root)]
            ),
        )
        with self.assertRaises(release.ReleaseGateError):
            release._validate_worker_receipts(builder)

        qa = self.bundle()
        wrong_qa_workspace = str(self.run_root / "qa" / "build-2")

        def move_qa_to_wrong_build(stage):
            proof = stage["metadata"]["preflight_proof"]
            command_index = stage["command"].index("-C") + 1
            proof_index = proof["debug_argv"].index("-C") + 1
            stage["command"][command_index] = wrong_qa_workspace
            proof["debug_argv"][proof_index] = wrong_qa_workspace
            prefix = proof["debug_argv"][: proof["worker_prefix_length"]]
            proof["worker_prefix_sha256"] = sha(
                release._canonical(prefix).encode("utf-8")
            )

        self.rewrite_stage(
            qa,
            3,
            move_qa_to_wrong_build,
        )
        with self.assertRaises(release.ReleaseGateError):
            release._validate_worker_receipts(qa)

        escalated = self.bundle()
        self.rewrite_stage(
            escalated,
            0,
            lambda stage: stage["metadata"]["preflight_proof"]["permissions"].__setitem__(
                "writable_roots", [str(self.run_root / "source")]
            ),
        )
        with self.assertRaises(release.ReleaseGateError):
            release._validate_worker_receipts(escalated)

        missing = self.bundle()
        self.rewrite_stage(
            missing,
            0,
            lambda stage: stage["metadata"].pop("preflight_proof"),
        )
        with self.assertRaises(release.ReleaseGateError):
            release._validate_worker_receipts(missing)

        changed = self.bundle()
        stage_path = changed.run_root / changed.worker_stage_receipts[0]["stage_ref"]["path"]
        stage_path.write_bytes(stage_path.read_bytes() + b" ")
        with self.assertRaises(release.ReleaseGateError):
            release._validate_worker_receipts(changed)

    def test_forged_qa_or_reviewer_status_is_rejected(self):
        receipts = self.bundle().worker_stage_receipts
        with self.assertRaises(release.ReleaseGateError):
            release._validate_worker_receipts(
                self.bundle(worker_stage_receipts=receipts, qa_receipt={"status": "FAIL"})
            )
        with self.assertRaises(release.ReleaseGateError):
            release._validate_worker_receipts(
                self.bundle(worker_stage_receipts=receipts, reviewer_receipt={"status": "CHANGES_REQUESTED"})
            )

    def test_source_or_candidate_inventory_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            candidate = root / "candidates" / "build-1"
            (source / "docs").mkdir(parents=True)
            (candidate / "docs").mkdir(parents=True)
            (source / "app.js").write_bytes(b"base\n")
            (candidate / "app.js").write_bytes(b"candidate\n")
            (source / "docs" / "STATUS.md").write_bytes(b"old\n")
            (candidate / "docs" / "STATUS.md").write_bytes(b"old\n")
            handoff = {
                "task_id": TASK,
                "task_status": "AWAITING_MAIN_ACCEPTANCE",
                "source_head": "a" * 40,
                "candidate_sha256": "f" * 64,
                "builds": 1,
                "files": [
                    {
                        "path": "app.js",
                        "change": "modified",
                        "before_sha256": sha(b"base\n"),
                        "after_sha256": sha(b"candidate\n"),
                    }
                ],
            }
            (root / "finalhandoff.json").write_text(json.dumps(handoff), encoding="utf-8")
            (root / "state.json").write_text(
                json.dumps(
                    {
                        "task_id": TASK,
                        "state": "AWAITING_MAIN_ACCEPTANCE",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(release.ReleaseGateError, "inventory digest"):
                release._expected_published_files(
                    root,
                    {"documents_receipt": {"documents": {}}},
                    [],
                )


class LocalSourceInventoryTests(unittest.TestCase):
    def test_git_visible_inventory_never_traverses_ignored_private_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            exclude = fixture.repo / ".git" / "info" / "exclude"
            exclude.write_text("work/\nbackups/\n", encoding="utf-8")
            private = fixture.repo / "work" / "acceptance" / "private.bin"
            private.parent.mkdir(parents=True)
            private.write_bytes(b"synthetic private artifact")
            backup = fixture.repo / "backups" / "personal.json"
            backup.parent.mkdir()
            backup.write_bytes(b"synthetic backup")
            seen = []
            original = release._sha256_file

            def tracked_hash(path):
                seen.append(Path(path))
                return original(path)

            with mock.patch.object(release, "_sha256_file", side_effect=tracked_hash):
                inventory = release._git_visible_source_inventory(fixture.repo)
            self.assertIn("app.js", inventory)
            self.assertNotIn("work/acceptance/private.bin", inventory)
            self.assertNotIn("backups/personal.json", inventory)
            relative_seen = [path.relative_to(fixture.repo) for path in seen]
            self.assertFalse(any("work" in part.parts for part in relative_seen))
            self.assertFalse(any("backups" in part.parts for part in relative_seen))

    def test_git_visible_drift_changes_the_exact_inventory_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            first = release._git_visible_source_inventory(fixture.repo)
            (fixture.repo / "app.js").write_bytes(b"const value = 'drifted';\n")
            second = release._git_visible_source_inventory(fixture.repo)
            self.assertNotEqual(first["app.js"], second["app.js"])
            self.assertNotEqual(first, second)


class Stage4UnitGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run_root = Path(self.temp.name) / "run"
        self.candidate = self.run_root / "candidates" / "build-1"
        self.candidate.mkdir(parents=True)
        (self.candidate / "app.js").write_bytes(b"const release = true;\n")
        files = release.qa.source_inventory(self.candidate)
        digest = release.qa.inventory_digest("a" * 40, files)
        body = {
            "schema": release.unit_qa.UNIT_REPORT_SCHEMA,
            "qa_contract_version": 2,
            "task_id": TASK,
            "source_head": "a" * 40,
            "source_inventory_sha256": digest,
            "simulation": False,
            "network_access": False,
            "candidate_unchanged": True,
            "working_copy_unchanged": True,
            "ready": True,
            "suites": [
                {
                    "id": suite,
                    "command": command,
                    "process": {
                        "status": "completed",
                        "exit_code": 0,
                        "timed_out": False,
                        "output_overflow": False,
                        "output_sha256": "b" * 64,
                    },
                    "test_count": 3,
                    "failure_count": 0,
                    "error_count": 0,
                    "skip_count": 0,
                }
                for suite, command in release.unit_qa.UNIT_SUITES
            ],
        }
        report = release.qa.seal_broker_report(body)
        report_path = self.run_root / release.unit_qa.UNIT_REPORT_NAME
        report_path.write_text(json.dumps(report), encoding="utf-8")
        context = {
            "schema": release.unit_qa.UNIT_CONTEXT_SCHEMA,
            "qa_contract_version": 2,
            "task_id": TASK,
            "source_head": "a" * 40,
            "source_inventory_sha256": digest,
            "report_sha256": sha(report_path.read_bytes()),
            "report_receipt_sha256": report["receipt_sha256"],
        }
        (self.run_root / release.unit_qa.UNIT_CONTEXT_NAME).write_text(
            json.dumps(context),
            encoding="utf-8",
        )
        self.verified = release.unit_qa.load_and_verify_unit_report(
            report_path,
            candidate_root=self.candidate,
            task_id=TASK,
            source_head="a" * 40,
            expected_inventory_sha256=digest,
        )

    def tearDown(self):
        self.temp.cleanup()

    def bundle(self, **updates):
        digest = self.verified["evidence_sha256"]
        value = SimpleNamespace(
            expected_commands=release.unit_qa.STAGE4_QA_COMMANDS,
            run_root=self.run_root,
            candidate_dir=self.candidate,
            task_id=TASK,
            source_head="a" * 40,
            candidate_sha256=self.verified["source_inventory_sha256"],
            unit_evidence=self.verified,
            qa_receipt={
                "qa_contract_version": 2,
                "unit_evidence": {"evidence_sha256": digest},
            },
            controller_receipt={"unit_evidence_sha256": digest},
        )
        for key, item in updates.items():
            setattr(value, key, item)
        return value

    def test_release_reverifies_and_binds_actual_unit_proof(self):
        verified = release._validate_stage4_unit_proof(self.bundle())
        self.assertEqual(self.verified["evidence_sha256"], verified["evidence_sha256"])
        with self.assertRaisesRegex(release.ReleaseGateError, "v2 unit contract"):
            release._validate_stage4_unit_proof(
                self.bundle(expected_commands=release.unit_qa.LEGACY_STAGE3_QA_COMMANDS)
            )
        with self.assertRaisesRegex(release.ReleaseGateError, "not bound"):
            release._validate_stage4_unit_proof(
                self.bundle(controller_receipt={"unit_evidence_sha256": "f" * 64})
            )
        (self.candidate / "app.js").write_bytes(b"const release = false;\n")
        with self.assertRaisesRegex(release.ReleaseGateError, "cannot be reverified"):
            release._validate_stage4_unit_proof(self.bundle())


class GitPublicationTests(unittest.TestCase):
    def test_crlf_staging_is_byte_exact_under_autocrlf_true_and_idempotent(self):
        git(self.fixture.repo, "config", "core.autocrlf", "true")
        raw = self.fixture.authorization()
        content = b"const value = 'approved';\r\n"
        (self.fixture.repo / "app.js").write_bytes(content)
        raw["prerequisite_files"][0]["after_sha256"] = sha(content)
        authorization = release.parse_authorization(raw)
        state = self.controller._state(authorization)
        commit = self.controller._stage_and_commit(authorization, state)
        blob = subprocess.check_output(["git", "cat-file", "blob", f"{commit}:app.js"], cwd=self.fixture.repo)
        self.assertEqual(content, blob)
        self.assertEqual(content, (self.fixture.repo / "app.js").read_bytes())
        self.assertEqual("true", git(self.fixture.repo, "config", "core.autocrlf"))
        self.assertEqual("", git(self.controller._worktree_path(authorization), "status", "--porcelain"))
        self.assertNotIn("foreign.txt", self.controller._diff_manifest(self.fixture.base_sha, commit))
        self.assertEqual(commit, self.controller._stage_and_commit(authorization, state))

    def test_active_hooks_block_before_worktree_creation_and_are_preserved(self):
        hook = self.fixture.repo / ".git/hooks/post-checkout"
        hook.write_bytes(b"#!/bin/sh\nexit 99\n")
        hook.chmod(0o755)
        with self.assertRaisesRegex(release.ReleaseBlocked, "Active Git hooks"):
            self.controller._stage_and_commit(self.authorization, self.controller._state(self.authorization))
        self.assertEqual(b"#!/bin/sh\nexit 99\n", hook.read_bytes())
        self.assertFalse(self.controller._worktree_path(self.authorization).exists())

    def test_sample_hooks_are_allowed_without_commit_bypass(self):
        commands = []
        original = self.controller.runner.run
        def recorded(argv, **kwargs):
            commands.append(argv)
            return original(argv, **kwargs)
        with mock.patch.object(self.controller.runner, "run", side_effect=recorded):
            self.controller._stage_and_commit(self.authorization, self.controller._state(self.authorization))
        commit = next(x for x in commands if "commit" in x)
        self.assertNotIn("--no-verify", commit)
        self.assertFalse(any("core.hooksPath=" in str(x) for x in commit))

    def test_published_candidate_overlap_requires_exact_validated_receipt(self):
        raw = self.fixture.authorization()
        original_b = raw["candidate_files"][0]["after_sha256"]
        final_c = sha(self.fixture.new_doc + b"published\n")
        raw["candidate_files"][0]["after_sha256"] = final_c
        authorization = release.parse_authorization(raw)
        document = "docs/new.md"
        body = {"document": document, "before_sha256": original_b,
                "after_sha256": final_c, "event_ids": ["synthetic-published-event"]}
        def receipt(value):
            return {**value, "receipt_sha256": release.delivery._digest({"task_id": TASK, **value})}
        gate = SimpleNamespace(
            bundle=SimpleNamespace(task_id=TASK, source_head=self.fixture.base_sha,
                candidate_sha256="a" * 64,
                changed_files=({"path": document, "old_sha256": None,
                                "new_sha256": original_b, "kind": "added"},)),
            journal_state={"documents_receipt": {"documents": {document: receipt(body)}}},
            current_files={x.path: x.after_sha256 for x in authorization.changes},
        )
        release._validate_authorization_against_gate(authorization, gate)
        for invalid in ({}, {document: receipt({**body, "before_sha256": "e" * 64})},
                        {document: receipt({**body, "after_sha256": "f" * 64})},
                        {document: {**receipt(body), "receipt_sha256": "0" * 64}},
                        {document: receipt({**body, "document": "docs/other.md"})}):
            with self.subTest(invalid=sorted(invalid)):
                gate.journal_state = {"documents_receipt": {"documents": invalid}}
                with self.assertRaises(release.ReleaseAuthorizationError):
                    release._validate_authorization_against_gate(authorization, gate)
        gate.journal_state = {"documents_receipt": {"documents": {document: receipt(body)}}}
        gate.current_files[document] = "d" * 64
        with self.assertRaises(release.ReleaseAuthorizationError):
            release._validate_authorization_against_gate(authorization, gate)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = GitFixture(Path(self.temp.name))
        self.store = release.ReleaseStore(Path(self.temp.name) / "releases").initialize()
        self.raw = self.fixture.authorization()
        self.authorization = release.parse_authorization(self.raw)
        self.controller = release.ReleaseController(
            repo_root=self.fixture.repo,
            run_root=Path(self.temp.name),
            store=self.store,
            transport=FakeTransport(),
            runner=CanonicalRouteRunner(),
        )

    def tearDown(self):
        self.temp.cleanup()

    def state(self):
        with self.store.lock(TASK):
            state = self.controller._state(self.authorization)
            self.controller._fetch_base(self.authorization, state)
            return state

    def test_exact_staging_preserves_foreign_dirty_tree_and_index(self):
        before_index = git(self.fixture.repo, "diff", "--cached", "--name-only")
        state = self.state()
        commit = self.controller._stage_and_commit(self.authorization, state)
        self.assertRegex(commit, r"^[0-9a-f]{40}$")
        self.assertEqual(base_commit := git(self.fixture.repo, "rev-parse", f"{commit}^"), self.fixture.base_sha)
        self.assertTrue(base_commit)
        diff = self.controller._diff_manifest(self.fixture.base_sha, commit)
        self.assertEqual(
            {"app.js": "modified", "docs/new.md": "added"},
            diff,
        )
        self.assertEqual(before_index, git(self.fixture.repo, "diff", "--cached", "--name-only"))
        self.assertTrue((self.fixture.repo / "foreign.txt").is_file())
        self.assertEqual(self.fixture.new_app, (self.fixture.repo / "app.js").read_bytes())

    def test_retry_does_not_duplicate_commit(self):
        state = self.state()
        first = self.controller._stage_and_commit(self.authorization, state)
        second = self.controller._stage_and_commit(self.authorization, self.controller._state(self.authorization))
        self.assertEqual(first, second)
        count = git(self.fixture.repo, "rev-list", "--count", f"{self.fixture.base_sha}..{first}")
        self.assertEqual("1", count)

    def test_wrong_before_hash_is_rejected_before_commit(self):
        raw = self.fixture.authorization(git_app="f" * 64)
        authorization = release.parse_authorization(raw)
        with self.store.lock(TASK):
            state = self.controller._state(authorization)
            with self.assertRaises(release.ReleaseAuthorizationError):
                self.controller._stage_and_commit(authorization, state)

    def test_git_before_absence_and_present_blob_are_unambiguous(self):
        self.assertIsNone(self.controller._base_tree_entry(self.fixture.base_sha, "docs/new.md"))
        mode, digest = self.controller._base_tree_entry(self.fixture.base_sha, "app.js")
        self.assertEqual("100644", mode)
        self.assertEqual(sha(self.fixture.old_app), digest)

        object_id = git(self.fixture.repo, "rev-parse", f"{self.fixture.base_sha}:app.js")
        record = (
            f"100644 blob {object_id}\tapp.js\0".encode("utf-8")
        )

        class BrokenTreeRunner(CanonicalRouteRunner):
            def __init__(self, tree_result=None, blob_result=None):
                super().__init__()
                self.tree_result = tree_result
                self.blob_result = blob_result

            def run(self, argv, *, cwd=None, timeout=30.0, stdin=None):
                args = list(argv)
                if "ls-tree" in args and self.tree_result is not None:
                    return self.tree_result(args)
                if "cat-file" in args and self.blob_result is not None:
                    return self.blob_result(args)
                return super().run(argv, cwd=cwd, timeout=timeout, stdin=stdin)

        malformed_results = (
            lambda args: release.CommandResult(tuple(args), 1, b"", b"failed"),
            lambda args: release.CommandResult(tuple(args), 0, b"", b"", timed_out=True),
            lambda args: release.CommandResult(tuple(args), 0, b"", b"", bounded=True),
            lambda args: release.CommandResult(tuple(args), 0, record + record, b""),
            lambda args: release.CommandResult(
                tuple(args),
                0,
                f"120000 blob {object_id}\tapp.js\0".encode("utf-8"),
                b"",
            ),
        )
        for factory in malformed_results:
            with self.subTest(factory=factory):
                self.controller.runner = BrokenTreeRunner(tree_result=factory)
                with self.assertRaises(release.ReleaseAuthorizationError):
                    self.controller._base_tree_entry(self.fixture.base_sha, "app.js")
        self.controller.runner = BrokenTreeRunner(
            blob_result=lambda args: release.CommandResult(tuple(args), 1, b"", b"failed")
        )
        with self.assertRaises(release.ReleaseAuthorizationError):
            self.controller._base_tree_entry(self.fixture.base_sha, "app.js")

    def test_untracked_source_draft_uses_null_git_before_and_exact_bootstrap_upload(self):
        draft = self.fixture.repo / "engineering" / "draft.py"
        draft.parent.mkdir()
        source_before = b"draft from the immutable Stage 3 source\n"
        source_after = b"draft approved for the remote bootstrap\n"
        draft.write_bytes(source_after)
        raw = self.fixture.authorization()
        raw["candidate_files"].append(
            {
                "path": "engineering/draft.py",
                "source_before_sha256": sha(source_before),
                "git_before_sha256": None,
                "after_sha256": sha(source_after),
                "mode": "100644",
            }
        )
        authorization = release.parse_authorization(raw)
        with self.store.lock(TASK):
            state = self.controller._state(authorization)
            commit = self.controller._stage_and_commit(authorization, state)
        self.assertEqual(
            "added",
            self.controller._diff_manifest(self.fixture.base_sha, commit)[
                "engineering/draft.py"
            ],
        )
        self.assertEqual(
            source_after,
            subprocess.run(
                ["git", "show", f"{commit}:engineering/draft.py"],
                cwd=self.fixture.repo,
                check=True,
                capture_output=True,
            ).stdout,
        )
        self.assertEqual(source_after, draft.read_bytes())

    def test_dirty_source_before_is_bound_separately_from_git_parent(self):
        source_before = "9" * 64
        raw = self.fixture.authorization(
            source_app=source_before,
            git_app=sha(self.fixture.old_app),
        )
        authorization = release.parse_authorization(raw)
        current = {item.path: item.after_sha256 for item in authorization.changes}
        gate = SimpleNamespace(
            bundle=SimpleNamespace(
                task_id=TASK,
                source_head=self.fixture.base_sha,
                candidate_sha256="a" * 64,
                changed_files=(
                    {
                        "path": "docs/new.md",
                        "old_sha256": None,
                        "new_sha256": sha(self.fixture.new_doc),
                        "kind": "added",
                    },
                    {
                        "path": "app.js",
                        "old_sha256": source_before,
                        "new_sha256": sha(self.fixture.new_app),
                        "kind": "modified",
                    },
                ),
            ),
            journal_state={"documents_receipt": {"documents": {}}},
            current_files=current,
        )
        release._validate_authorization_against_gate(authorization, gate)
        wrong = release.parse_authorization(
            self.fixture.authorization(
                source_app="8" * 64,
                git_app=sha(self.fixture.old_app),
            )
        )
        with self.assertRaises(release.ReleaseAuthorizationError):
            release._validate_authorization_against_gate(wrong, gate)
        with self.store.lock(TASK):
            state = self.controller._state(authorization)
            commit = self.controller._stage_and_commit(authorization, state)
        self.assertEqual(
            "modified",
            self.controller._diff_manifest(self.fixture.base_sha, commit)["app.js"],
        )
        self.assertTrue((self.fixture.repo / "foreign.txt").is_file())
        self.assertEqual(self.fixture.new_app, (self.fixture.repo / "app.js").read_bytes())

    def test_pr_creation_is_looked_up_before_duplicate(self):
        state = self.controller._state(self.authorization)
        state["commit_sha"] = "b" * 40
        self.store.save(TASK, state)
        gate = SimpleNamespace(
            bundle=SimpleNamespace(
                expected_commands=("cmd-a",),
                worker_stage_receipts=(
                    {
                        "role": "qa-tester",
                        "status": "PASS",
                        "provider": "lifeos_deepseek",
                        "model": "deepseek-flash",
                    },
                ),
                reviewer_receipt={"status": "APPROVED"},
            ),
            journal_state={"status": "DELIVERY_READY"},
            docs_receipt_sha256="1" * 64,
            index_receipt_sha256="2" * 64,
        )
        first = self.controller._ensure_pr(self.authorization, gate, state)
        second = self.controller._ensure_pr(self.authorization, gate, self.controller._state(self.authorization))
        self.assertEqual(first.number, second.number)
        creates = [
            row
            for row in self.controller.transport.calls
            if row[0] == "POST" and str(row[1]).endswith("/pulls")
        ]
        self.assertEqual(1, len(creates))


class OriginRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = GitFixture(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def controller(self, runner, transport=None):
        return release.ReleaseController(
            repo_root=self.fixture.repo,
            run_root=Path(self.temp.name),
            store=release.ReleaseStore(Path(self.temp.name) / "releases").initialize(),
            transport=transport or FakeTransport(),
            runner=runner,
        )

    def test_canonical_fetch_and_push_routes_are_accepted(self):
        self.controller(CanonicalRouteRunner())._validate_origin_routes()

    def test_multiple_insecure_and_credential_routes_fail_without_echo(self):
        cases = (
            CanonicalRouteRunner(
                fetch_urls=[
                    "https://github.com/dabinglei2-hash/LifeOS.git",
                    "https://github.com/dabinglei2-hash/Other.git",
                ]
            ),
            CanonicalRouteRunner(fetch_urls=["http://github.com/dabinglei2-hash/LifeOS.git"]),
            CanonicalRouteRunner(
                fetch_urls=[
                    "https://user:"
                    + "synthetic-secret"
                    + "@github.com/dabinglei2-hash/LifeOS.git"
                ]
            ),
        )
        for runner in cases:
            with self.subTest(urls=runner.fetch_urls):
                with self.assertRaises(release.ReleaseBlocked) as raised:
                    self.controller(runner)._validate_origin_routes()
                message = str(raised.exception)
                self.assertNotIn("synthetic-secret", message)
                self.assertNotIn("github.com", message)

    def test_legacy_redirect_is_rechecked_for_full_name_and_repository_id(self):
        legacy = release.LEGACY_ORIGIN_URLS.copy()
        legacy_url = sorted(legacy)[0]

        class Repository:
            def __init__(self, *, changing=False):
                self.calls = 0
                self.changing = changing

            def api(self, method, route, body=None):
                self.calls += 1
                return {
                    "full_name": release.CANONICAL_REPOSITORY,
                    "private": False,
                    "visibility": "public",
                    "id": 40 + self.calls if self.changing else 42,
                }

            def graphql(self, query, variables):
                raise AssertionError("not used")

        accepted = self.controller(
            CanonicalRouteRunner(fetch_urls=[legacy_url], push_urls=[legacy_url]),
            transport=Repository(),
        )
        accepted._validate_origin_routes()
        controller = self.controller(
            CanonicalRouteRunner(fetch_urls=[legacy_url], push_urls=[legacy_url]),
            transport=Repository(changing=True),
        )
        with self.assertRaises(release.ReleaseBlocked):
            controller._validate_origin_routes()

    def test_url_rewrite_configuration_is_rejected(self):
        git(
            self.fixture.repo,
            "config",
            "url.https://github.com/dabinglei2-hash/LifeOS.git.insteadOf",
            "https://example.invalid/",
        )
        with self.assertRaises(release.ReleaseBlocked):
            self.controller(CanonicalRouteRunner())._validate_origin_routes()


class GitHubSafetyTests(unittest.TestCase):
    def test_read_retry_is_bounded(self):
        class RateLimited:
            def __init__(self):
                self.calls = 0

            def api(self, method, route, body=None):
                self.calls += 1
                raise release.GitHubTransientError("rate limited", status=429, retry_after=0)

        transport = RateLimited()
        client = release.GitHubClient(transport, max_attempts=3, sleeper=lambda _delay: None)
        with self.assertRaises(release.GitHubTransientError):
            client.get("/repos/example/project")
        self.assertEqual(3, transport.calls)
        self.assertEqual(3, len(client.network_failures))

    def test_canonical_repository_resolution_is_pinned(self):
        class Redirect:
            def api(self, method, route, body=None):
                return {"full_name": "someone/else", "private": False}

            def graphql(self, query, variables):
                raise AssertionError

        with self.assertRaises(release.ReleaseBlocked):
            release.GitHubClient(Redirect()).resolve_repository()

    def test_protection_refusal_has_no_settings_mutation(self):
        transport = FakeTransport()
        client = release.GitHubClient(transport)
        controller = release.ReleaseController.__new__(release.ReleaseController)
        controller.github = client
        authorization = SimpleNamespace(repository=release.CANONICAL_REPOSITORY, base_branch="main")
        with mock.patch.object(
            client,
            "get",
            return_value={
                "enforce_admins": {"enabled": False},
                "required_status_checks": {
                    "strict": True,
                    "contexts": [
                        "LADS CI / ci",
                        "LADS Security / security",
                        "LADS Docs / docs",
                    ],
                },
            },
        ):
            with self.assertRaises(release.ReleaseBlocked) as raised:
                controller._branch_protection(authorization)
        self.assertEqual("AWAITING_APPROVAL", raised.exception.state)
        self.assertFalse(any(call[0] != "GET" for call in transport.calls))

    def test_active_ruleset_can_supply_native_protection(self):
        controller = release.ReleaseController.__new__(release.ReleaseController)
        controller.github = mock.Mock()
        controller.github.get = mock.Mock(
            side_effect=[
                [
                    {
                        "id": 9,
                        "target": "branch",
                        "enforcement": "active",
                    }
                ],
                {
                    "enforcement": "active",
                    "bypass_actors": [],
                    "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
                    "rules": [
                        {"type": "deletion"},
                        {"type": "non_fast_forward"},
                        {
                            "type": "required_status_checks",
                            "parameters": {
                                "strict_required_status_checks_policy": True,
                                "required_status_checks": [
                                    {"context": "LADS CI / ci"},
                                    {"context": "LADS Security / security"},
                                    {"context": "LADS Docs / docs"},
                                    {"context": "LADS / controller"},
                                ],
                            },
                        },
                    ],
                },
            ]
        )
        authorization = SimpleNamespace(
            repository=release.CANONICAL_REPOSITORY,
            base_branch="main",
        )
        value = controller._ruleset_protection(authorization)
        self.assertEqual("ruleset", value["source"])
        self.assertTrue(value["non_fast_forward"])

    @staticmethod
    def _authorization():
        return SimpleNamespace(
            repository=release.CANONICAL_REPOSITORY,
            base_branch="main",
            branch="codex/synthetic-release",
            task_id=TASK,
            candidate_sha256="a" * 64,
        )

    @staticmethod
    def _pr(*, merged=False, merge_commit_sha=None):
        return release.PullRequest(
            7,
            "PR_node_7",
            "https://github.com/dabinglei2-hash/LifeOS/pull/7",
            "open",
            False,
            True,
            "b" * 40,
            "codex/synthetic-release",
            "main",
            merged,
            merge_commit_sha,
        )

    @staticmethod
    def _state():
        return {
            "task_id": TASK,
            "commit_sha": "b" * 40,
            "pr_number": 7,
            "pr_node_id": "PR_node_7",
            "repository_resolution": {"merge_method": "MERGE"},
        }

    @staticmethod
    def _review_context(*, auto=None, pr=None):
        current = pr or GitHubSafetyTests._pr()
        return {
            "id": current.node_id,
            "number": current.number,
            "headRefOid": current.head_sha,
            "baseRefName": current.base_ref,
            "state": "OPEN",
            "isDraft": False,
            "merged": False,
            "reviewDecision": "APPROVED",
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [],
            },
            "autoMergeRequest": auto,
        }

    @staticmethod
    def _check_run(
        name,
        *,
        status="completed",
        conclusion="success",
        run_id=1,
        suite_id=None,
    ):
        return {
            "id": run_id,
            "name": name,
            "head_sha": "b" * 40,
            "status": status,
            "conclusion": conclusion,
            "app": {"slug": "github-actions"},
            "check_suite": {"id": suite_id if suite_id is not None else 90 + run_id},
        }

    @classmethod
    def _workflow_check_client(
        cls,
        *,
        absent=(),
        ambiguous=(),
        workflow_path_mismatch=None,
        workflow_run_mismatch=None,
    ):
        checks = [
            cls._check_run(item.context, run_id=index, suite_id=100 + index)
            for index, item in enumerate(release.REQUIRED_WORKFLOW_CHECKS, start=1)
        ]
        by_context = {item.context: item for item in release.REQUIRED_WORKFLOW_CHECKS}
        workflow_ids = {
            item.context: 500 + index
            for index, item in enumerate(release.REQUIRED_WORKFLOW_CHECKS, start=1)
        }
        suite_ids = {
            item.context: 100 + index
            for index, item in enumerate(release.REQUIRED_WORKFLOW_CHECKS, start=1)
        }
        responses = [{"check_runs": checks}]
        for context in sorted(by_context):
            workflow = by_context[context]
            workflow_path = release.REQUIRED_WORKFLOW_PATHS[context]
            workflow_id = workflow_ids[context]
            responses.append(
                {
                    "id": workflow_id,
                    "node_id": f"W_{workflow_id}",
                    "name": workflow.workflow,
                    "path": (
                        ".github/workflows/unrelated.yml"
                        if context == workflow_path_mismatch
                        else workflow_path
                    ),
                    "state": "active",
                }
            )
            if context in absent:
                responses.append({"total_count": 0, "workflow_runs": []})
                continue
            workflow_run = {
                "id": 2000 + workflow_id,
                "name": workflow.workflow,
                "head_sha": "b" * 40,
                "path": workflow_path,
                "status": "completed",
                "conclusion": "success",
                "workflow_id": workflow_id,
                "check_suite_id": suite_ids[context],
            }
            if context == workflow_run_mismatch:
                workflow_run["head_sha"] = "c" * 40
            workflow_runs = [workflow_run]
            if context in ambiguous:
                workflow_runs.append({**workflow_run, "id": workflow_run["id"] + 1000})
            responses.append(
                {
                    "total_count": len(workflow_runs),
                    "workflow_runs": workflow_runs,
                }
            )
        get = mock.Mock(side_effect=responses)
        controller = release.ReleaseController.__new__(release.ReleaseController)
        controller.github = mock.Mock(get=get)
        return controller, checks, get

    @staticmethod
    def _controller_with_transport(transport):
        controller = release.ReleaseController.__new__(release.ReleaseController)
        controller.github = release.GitHubClient(transport)
        controller.store = mock.Mock()

        def save(value, status, **fields):
            value["status"] = status
            value.update(fields)
            return dict(value)

        controller._save = mock.Mock(side_effect=save)
        controller._branch_protection = mock.Mock(return_value={})
        return controller

    def test_workflow_checks_wait_on_missing_or_pending_and_reject_all_non_success(self):
        controller = release.ReleaseController.__new__(release.ReleaseController)
        controller.github = mock.Mock()
        authorization = self._authorization()
        controller.github.get = mock.Mock(return_value={"check_runs": []})
        state, checks = controller._check_snapshot(authorization, "b" * 40)
        self.assertEqual("WAITING_CI", state)
        self.assertTrue(all(item.get("missing") for item in checks))
        self.assertEqual(1, controller.github.get.call_count)

        pending = [
            self._check_run(item.context, status="in_progress", conclusion=None, run_id=index)
            for index, item in enumerate(release.REQUIRED_WORKFLOW_CHECKS, start=1)
        ]
        controller.github.get = mock.Mock(return_value={"check_runs": pending})
        state, _checks = controller._check_snapshot(authorization, "b" * 40)
        self.assertEqual("WAITING_CI", state)

        for conclusion in ("failure", "skipped", "neutral", "cancelled", "timed_out"):
            with self.subTest(conclusion=conclusion):
                runs = [
                    self._check_run(
                        item.context,
                        conclusion=conclusion if index == 1 else "success",
                        run_id=index,
                    )
                    for index, item in enumerate(release.REQUIRED_WORKFLOW_CHECKS, start=1)
                ]
                controller.github.get = mock.Mock(return_value={"check_runs": runs})
                state, _checks = controller._check_snapshot(authorization, "b" * 40)
                self.assertEqual("CI_FAILED", state)

        spoofed = [
            {
                **self._check_run(item.context, run_id=index),
                "app": {"slug": "synthetic-status-app"},
            }
            for index, item in enumerate(release.REQUIRED_WORKFLOW_CHECKS, start=1)
        ]
        controller.github.get = mock.Mock(return_value={"check_runs": spoofed})
        state, _checks = controller._check_snapshot(authorization, "b" * 40)
        self.assertEqual("WAITING_CI", state)

    def test_workflow_checks_require_exact_workflow_file_ids_and_suite_runs(self):
        controller, checks, get = self._workflow_check_client()
        authorization = self._authorization()

        state, rows = controller._check_snapshot(authorization, "b" * 40)

        self.assertEqual("SUCCESS", state)
        by_context = {
            row["context"]: row["observations"][0]
            for row in rows
        }
        self.assertEqual(
            release.REQUIRED_WORKFLOW_PATHS,
            {
                context: observation["workflow_path"]
                for context, observation in by_context.items()
            },
        )
        run_routes = [
            call.args[0] for call in get.call_args_list if "/runs?" in call.args[0]
        ]
        self.assertEqual(3, len(run_routes))
        for check in checks:
            observation = by_context[check["name"]]
            suite_id = check["check_suite"]["id"]
            self.assertGreater(observation["workflow_id"], 0)
            self.assertGreater(observation["workflow_run_id"], 0)
            self.assertEqual(check["id"], observation["id"])
            self.assertEqual(suite_id, observation["check_suite_id"])
            matching_route = next(
                route
                for route in run_routes
                if f"/workflows/{observation['workflow_id']}/runs?" in route
            )
            self.assertIn(f"head_sha={'b' * 40}", matching_route)
            self.assertIn(f"check_suite_id={suite_id}", matching_route)
        self.assertEqual(7, get.call_count)

    def test_unrelated_workflow_suite_cannot_satisfy_required_contexts(self):
        controller, _checks, _get = self._workflow_check_client(
            absent=tuple(item.context for item in release.REQUIRED_WORKFLOW_CHECKS)
        )

        state, rows = controller._check_snapshot(self._authorization(), "b" * 40)

        self.assertEqual("WAITING_CI", state)
        self.assertTrue(all(row.get("provenance_missing") for row in rows))

    def test_wrong_or_ambiguous_workflow_provenance_fails_closed(self):
        authorization = self._authorization()
        cases = (
            (
                {"workflow_path_mismatch": release.REQUIRED_WORKFLOW_CHECKS[0].context},
                "workflow_identity",
            ),
            (
                {"workflow_run_mismatch": release.REQUIRED_WORKFLOW_CHECKS[1].context},
                "workflow_run_binding",
            ),
            (
                {"ambiguous": (release.REQUIRED_WORKFLOW_CHECKS[2].context,)},
                "ambiguous_workflow_run",
            ),
        )
        for options, expected_error in cases:
            with self.subTest(options=options):
                controller, _checks, _get = self._workflow_check_client(**options)
                state, rows = controller._check_snapshot(authorization, "b" * 40)
                self.assertEqual("CI_FAILED", state)
                self.assertTrue(
                    any(row.get("provenance_error") == expected_error for row in rows)
                )

    def test_exact_controller_status_is_distinct_from_workflow_checks(self):
        controller = release.ReleaseController.__new__(release.ReleaseController)
        controller.github = mock.Mock()
        authorization = self._authorization()
        expected = {
            "context": "LADS / controller",
            "state": "success",
            "description": "LADS machine acceptance " + ("d" * 20),
            "target_url": self._pr().url,
        }
        controller.github.get = mock.Mock(
            return_value={
                "statuses": [
                    {
                        "context": "LADS / controller",
                        "state": "pending",
                    }
                ]
            }
        )
        state, _rows = controller._controller_status_snapshot(
            authorization,
            "b" * 40,
            expected=expected,
        )
        self.assertEqual("WAITING_CI", state)

        controller.github.get = mock.Mock(
            return_value={
                "statuses": [
                    {
                        "context": "LADS / controller",
                        "state": "success",
                        "description": "unbound success",
                        "target_url": self._pr().url,
                    }
                ]
            }
        )
        state, _rows = controller._controller_status_snapshot(
            authorization,
            "b" * 40,
            expected=expected,
        )
        self.assertEqual("CI_FAILED", state)
        state, _rows = controller._controller_status_snapshot(
            authorization,
            "b" * 40,
        )
        self.assertEqual("CI_FAILED", state)

        controller.github.get = mock.Mock(return_value={"statuses": [expected]})
        state, _rows = controller._controller_status_snapshot(
            authorization,
            "b" * 40,
            expected=expected,
        )
        self.assertEqual("SUCCESS", state)

    def test_workflow_wait_retries_missing_or_pending_and_closes_on_failure(self):
        controller = release.ReleaseController.__new__(release.ReleaseController)
        controller.poll_attempts = 2
        controller.poll_interval = 0
        controller.sleeper = mock.Mock()
        controller.store = mock.Mock()
        controller._save = mock.Mock(
            side_effect=lambda value, status, **fields: {
                **value,
                "status": status,
                **fields,
            }
        )
        authorization = self._authorization()
        state = self._state()
        pr = self._pr()
        controller._verify_prerequisites = mock.Mock(return_value=(pr, {}))
        controller._check_snapshot = mock.Mock(
            side_effect=[
                ("WAITING_CI", [{"missing": True}]),
                ("SUCCESS", []),
            ]
        )
        controller._controller_status_snapshot = mock.Mock(
            return_value=("WAITING_CI", [])
        )
        self.assertIs(
            pr,
            controller._wait_for_workflows(authorization, state, pr),
        )
        controller.sleeper.assert_called_once_with(0)

        controller._check_snapshot = mock.Mock(
            return_value=("CI_FAILED", [{"conclusion": "skipped"}])
        )
        with self.assertRaises(release.ReleaseBlocked) as raised:
            controller._wait_for_workflows(authorization, state, pr)
        self.assertEqual("CI_FAILED", raised.exception.state)

    def test_auto_merge_readback_is_exact_and_idempotent(self):
        class Transport:
            def __init__(self):
                self.auto = None
                self.mutations = 0
                self.calls = []

            def api(self, method, route, body=None):
                self.calls.append((method, route, body))
                if method == "GET" and route.endswith("/pulls/7"):
                    pr = GitHubSafetyTests._pr()
                    return {
                        "number": pr.number,
                        "node_id": pr.node_id,
                        "html_url": pr.url,
                        "state": pr.state,
                        "draft": pr.draft,
                        "mergeable": pr.mergeable,
                        "head": {"sha": pr.head_sha, "ref": pr.head_ref},
                        "base": {"ref": pr.base_ref},
                        "merged": pr.merged,
                        "merge_commit_sha": pr.merge_commit_sha,
                    }
                raise AssertionError((method, route))

            def graphql(self, query, variables):
                self.calls.append(("GRAPHQL", query, dict(variables)))
                if "$mergeMethod" in query:
                    self.mutations += 1
                    self.auto = {
                        "enabledAt": "2026-11-05T00:00:00Z",
                        "mergeMethod": variables["mergeMethod"],
                    }
                    return {
                        "data": {
                            "enablePullRequestAutoMerge": {
                                "pullRequest": {
                                    "autoMergeRequest": dict(self.auto),
                                }
                            }
                        }
                    }
                return {
                    "data": {
                        "repository": {
                            "pullRequest": self.context(),
                        }
                    }
                }

            def context(self):
                return GitHubSafetyTests._review_context(auto=self.auto)

        transport = Transport()
        controller = self._controller_with_transport(transport)
        authorization = self._authorization()
        state = self._state()
        pr = self._pr()
        controller._enable_auto_merge(authorization, state, pr)
        controller._enable_auto_merge(authorization, state, pr)
        self.assertEqual(1, transport.mutations)
        self.assertEqual(
            {
                "pull_request_id": "PR_node_7",
                "pull_request_number": 7,
                "head_sha": "b" * 40,
                "base_ref": "main",
                "merge_method": "MERGE",
                "enabled_at": "2026-11-05T00:00:00Z",
            },
            state["auto_merge_evidence"],
        )

    def test_preexisting_auto_merge_without_intent_and_unproven_merge_are_rejected(self):
        class Preexisting:
            def api(self, method, route, body=None):
                pr = GitHubSafetyTests._pr()
                return {
                    "number": pr.number,
                    "node_id": pr.node_id,
                    "html_url": pr.url,
                    "state": pr.state,
                    "draft": pr.draft,
                    "mergeable": pr.mergeable,
                    "head": {"sha": pr.head_sha, "ref": pr.head_ref},
                    "base": {"ref": pr.base_ref},
                    "merged": pr.merged,
                    "merge_commit_sha": pr.merge_commit_sha,
                }

            def graphql(self, query, variables):
                return {
                    "data": {
                        "repository": {
                            "pullRequest": GitHubSafetyTests._review_context(
                                auto={
                                    "enabledAt": "2026-11-05T00:00:00Z",
                                    "mergeMethod": "MERGE",
                                }
                            )
                        }
                    }
                }

        controller = self._controller_with_transport(Preexisting())
        state = self._state()
        pr = self._pr()
        authorization = self._authorization()
        with self.assertRaises(release.ReleaseBlocked) as raised:
            controller._enable_auto_merge(authorization, state, pr)
        self.assertEqual("AUTO_MERGE_EVIDENCE_CONFLICT", raised.exception.reason)

        intended = {
            **self._state(),
            "auto_merge_intent": {
                "pull_request_id": pr.node_id,
                "merge_method": "MERGE",
            },
        }
        with self.assertRaises(release.ReleaseBlocked) as raised:
            controller._enable_auto_merge(authorization, intended, pr)
        self.assertEqual("AUTO_MERGE_EVIDENCE_CONFLICT", raised.exception.reason)

        gate = SimpleNamespace(
            evidence={"qa_sha256": "1" * 64, "reviewer_sha256": "2" * 64},
            docs_receipt_sha256="3" * 64,
            index_receipt_sha256="4" * 64,
        )
        merged = self._pr(merged=True, merge_commit_sha="c" * 40)
        with self.assertRaises(release.ReleaseBlocked) as raised:
            controller._verify_native_merge_evidence(
                authorization,
                gate,
                state,
                merged,
            )
        self.assertEqual("AUTO_MERGE_EVIDENCE_CONFLICT", raised.exception.reason)

    def test_ambiguous_merge_without_premerge_readback_never_claims_success(self):
        class Ambiguous:
            def __init__(self):
                self.merged = False

            def api(self, method, route, body=None):
                pr = GitHubSafetyTests._pr(
                    merged=self.merged,
                    merge_commit_sha="c" * 40 if self.merged else None,
                )
                return {
                    "number": pr.number,
                    "node_id": pr.node_id,
                    "html_url": pr.url,
                    "state": "closed" if self.merged else pr.state,
                    "draft": pr.draft,
                    "mergeable": pr.mergeable,
                    "head": {"sha": pr.head_sha, "ref": pr.head_ref},
                    "base": {"ref": pr.base_ref},
                    "merged": pr.merged,
                    "merge_commit_sha": pr.merge_commit_sha,
                }

            def graphql(self, query, variables):
                if "$mergeMethod" in query:
                    self.merged = True
                    raise release.GitHubTransportError("ambiguous registration")
                return {
                    "data": {
                        "repository": {
                            "pullRequest": GitHubSafetyTests._review_context()
                        }
                    }
                }

        transport = Ambiguous()
        controller = self._controller_with_transport(transport)
        state = self._state()
        authorization = self._authorization()
        pr = self._pr()
        with self.assertRaises(release.ReleaseBlocked) as raised:
            controller._enable_auto_merge(authorization, state, pr)
        self.assertEqual("AWAITING_APPROVAL", raised.exception.state)
        self.assertEqual("AUTO_MERGE_EVIDENCE_CONFLICT", raised.exception.reason)
        self.assertNotIn("MERGED", [call.args[1] for call in controller._save.call_args_list])

    def test_controller_status_post_follows_order_and_resume_does_not_duplicate(self):
        class StatusTransport:
            def __init__(self):
                self.statuses = []
                self.posts = 0

            def api(self, method, route, body=None):
                if method == "GET" and route.endswith("/status"):
                    return {"statuses": list(self.statuses)}
                if method == "POST" and "/statuses/" in route:
                    self.posts += 1
                    self.statuses.append(dict(body))
                    return dict(body)
                raise AssertionError((method, route))

            def graphql(self, query, variables):
                raise AssertionError("not used")

        transport = StatusTransport()
        controller = release.ReleaseController.__new__(release.ReleaseController)
        controller.github = release.GitHubClient(transport)
        state = self._state()
        gate = SimpleNamespace(
            evidence={"qa_sha256": "1" * 64, "reviewer_sha256": "2" * 64},
            docs_receipt_sha256="3" * 64,
            index_receipt_sha256="4" * 64,
        )
        authorization = self._authorization()
        pr = self._pr()

        def save(value, status, **fields):
            value["status"] = status
            value.update(fields)
            return dict(value)

        controller._save = mock.Mock(side_effect=save)
        controller._create_controller_status(authorization, gate, pr, state)
        controller._create_controller_status(authorization, gate, pr, state)
        self.assertEqual(1, transport.posts)
        self.assertEqual("CONTROLLER_STATUS_READY", state["status"])
        self.assertEqual(
            "LADS machine acceptance " + state["controller_evidence_digest"][:20],
            transport.statuses[0]["description"],
        )

    def test_exact_controller_success_without_durable_intent_is_rejected(self):
        controller = release.ReleaseController.__new__(release.ReleaseController)
        controller._save = mock.Mock()
        state = self._state()
        gate = SimpleNamespace(
            evidence={"qa_sha256": "1" * 64, "reviewer_sha256": "2" * 64},
            docs_receipt_sha256="3" * 64,
            index_receipt_sha256="4" * 64,
        )
        authorization = self._authorization()
        pr = self._pr()
        _digest, expected = controller._controller_status_expectation(
            authorization,
            gate,
            pr,
            state,
        )

        class ExactUnexpectedTransport:
            def api(self, method, route, body=None):
                if method == "GET" and route.endswith("/status"):
                    return {"statuses": [expected]}
                raise AssertionError((method, route))

            def graphql(self, query, variables):
                raise AssertionError("not used")

        controller.github = release.GitHubClient(ExactUnexpectedTransport())
        with self.assertRaises(release.ReleaseBlocked) as raised:
            controller._create_controller_status(authorization, gate, pr, state)
        self.assertEqual("AWAITING_APPROVAL", raised.exception.state)
        self.assertEqual("CONTROLLER_STATUS_CONFLICT", raised.exception.reason)
        controller._save.assert_not_called()

    def test_publish_order_arms_native_merge_before_controller_success(self):
        controller = release.ReleaseController.__new__(release.ReleaseController)
        events = []
        pr = self._pr()
        state = self._state()
        authorization = self._authorization()
        gate = SimpleNamespace()
        controller._verify_prerequisites = mock.Mock(
            side_effect=lambda *_args: (events.append("prerequisites"), (pr, {}))[1]
        )
        controller._wait_for_workflows = mock.Mock(
            side_effect=lambda *_args, **_kwargs: (events.append("workflows"), pr)[1]
        )
        controller._enable_auto_merge = mock.Mock(
            side_effect=lambda *_args: (
                events.append("auto-merge"),
                state.update({"auto_merge_evidence": {"enabled_at": "synthetic"}}),
            )
        )
        controller._verify_auto_merge_context = mock.Mock(
            side_effect=lambda *_args: (events.append("auto-readback"), pr)[1]
        )
        controller._create_controller_status = mock.Mock(
            side_effect=lambda *_args: events.append("controller-success")
        )
        controller._poll_merge = mock.Mock(
            side_effect=lambda *_args: (events.append("poll"), pr)[1]
        )
        self.assertIs(
            pr,
            controller._publish_open_pr(authorization, gate, state, pr),
        )
        self.assertEqual(
            [
                "prerequisites",
                "workflows",
                "auto-merge",
                "auto-readback",
                "workflows",
                "controller-success",
                "poll",
            ],
            events,
        )

    def test_final_workflow_recheck_failure_blocks_controller_success(self):
        controller = release.ReleaseController.__new__(release.ReleaseController)
        pr = self._pr()
        state = self._state()
        authorization = self._authorization()
        controller._verify_prerequisites = mock.Mock(return_value=(pr, {}))
        controller._wait_for_workflows = mock.Mock(
            side_effect=[
                pr,
                release.ReleaseBlocked("workflow regression", state="CI_FAILED"),
            ]
        )
        controller._enable_auto_merge = mock.Mock(
            side_effect=lambda *_args: state.update(
                {"auto_merge_evidence": {"enabled_at": "synthetic"}}
            )
        )
        controller._verify_auto_merge_context = mock.Mock(return_value=pr)
        controller._create_controller_status = mock.Mock()
        controller._poll_merge = mock.Mock()
        with self.assertRaises(release.ReleaseBlocked) as raised:
            controller._publish_open_pr(
                authorization,
                SimpleNamespace(),
                state,
                pr,
            )
        self.assertEqual("CI_FAILED", raised.exception.state)
        controller._create_controller_status.assert_not_called()
        controller._poll_merge.assert_not_called()

    def test_controller_ready_resume_skips_pr_arm_and_posts(self):
        controller = release.ReleaseController.__new__(release.ReleaseController)
        events = []
        pr = self._pr()
        state = {
            **self._state(),
            "status": "CONTROLLER_STATUS_READY",
            "auto_merge_evidence": {"enabled_at": "synthetic"},
        }
        controller._verify_prerequisites = mock.Mock(
            side_effect=lambda *_args: (events.append("prerequisites"), (pr, {}))[1]
        )
        controller._verify_auto_merge_context = mock.Mock(
            side_effect=lambda *_args: (events.append("auto-readback"), pr)[1]
        )
        controller._wait_for_workflows = mock.Mock()
        controller._enable_auto_merge = mock.Mock()
        controller._create_controller_status = mock.Mock()
        controller._poll_merge = mock.Mock(
            side_effect=lambda *_args: (events.append("poll"), pr)[1]
        )
        self.assertIs(
            pr,
            controller._publish_open_pr(
                self._authorization(),
                SimpleNamespace(),
                state,
                pr,
            ),
        )
        self.assertEqual(["prerequisites", "auto-readback", "poll"], events)
        controller._enable_auto_merge.assert_not_called()
        controller._create_controller_status.assert_not_called()

    def test_repository_settings_and_selected_merge_method_are_bound_on_resume(self):
        repository = {
            "full_name": release.CANONICAL_REPOSITORY,
            "private": False,
            "visibility": "public",
            "allow_merge_commit": True,
            "allow_rebase_merge": True,
            "allow_squash_merge": False,
        }
        state = {}
        resolution = release.ReleaseController._bind_repository_resolution(
            repository,
            state,
        )
        self.assertEqual("MERGE", resolution["merge_method"])
        state["repository_resolution"] = resolution
        changed = {**repository, "allow_merge_commit": False}
        with self.assertRaises(release.ReleaseConflict):
            release.ReleaseController._bind_repository_resolution(changed, state)


class MergeAndKnowledgeTests(unittest.TestCase):
    def test_real_knowledge_history_envelope_binds_docs_tests_and_index_event(self):
        from lads_knowledge import KnowledgeStore

        store = KnowledgeStore.memory()
        project = store.register_project("Synthetic knowledge envelope")
        store.create_task(
            project["id"],
            "Bind a real release history",
            task_id=TASK,
            assigned_agent="builder",
        )
        commands = tuple(release.unit_qa.STAGE4_QA_COMMANDS)
        qa_sha = "1" * 64
        doc = b"# Current status\n"
        doc_sha = sha(doc)
        event_id = str(uuid.uuid4())
        store.record_change(
            project["id"],
            TASK,
            file_path="docs/STATUS.md",
            change_type="document_update",
            source_ref="docs/STATUS.md",
            source_raw_sha=doc_sha,
            observed_head="a" * 40,
            actual_content_commit_sha=None,
            dirty=True,
            diff_sha=qa_sha,
        )
        for command in commands:
            store.record_test(
                project["id"],
                TASK,
                test_name="LADS prescribed QA command",
                command=command,
                status="passed",
                output_sha=qa_sha,
            )
        store.record_event(
            project["id"],
            TASK,
            "index_synced",
            {"source_head": "a" * 40, "working_content_commit": None},
            event_id=event_id,
        )
        index = {
            "index_event_id": event_id,
            "source_head": "a" * 40,
            "candidate_sha256": "b" * 64,
            "working_content_commit": None,
            "documents": [{"path": "docs/STATUS.md", "raw_sha256": doc_sha}],
            "tests": [{"name": "LADS prescribed QA command"} for _ in commands],
        }
        documents = {
            "docs/STATUS.md": {
                "before_sha256": "c" * 64,
                "after_sha256": doc_sha,
            }
        }
        history = store.reader(project["id"]).get_task_history(TASK, limit=20)
        gate = {
            "bundle": SimpleNamespace(
                task_id=TASK,
                source_head="a" * 40,
                candidate_sha256="b" * 64,
            ),
            "project_id": project["id"],
            "journal_state": {
                "status": "DELIVERY_READY",
                "ready": True,
                "documents_receipt": {"documents": documents},
                "index_receipt": index,
            },
            "evidence": {"qa_sha256": qa_sha},
            "commands": commands,
            "index_receipt": index,
        }
        release._validate_knowledge_receipt(gate, history)
        wrong_project = {**gate, "project_id": str(uuid.uuid4())}
        with self.assertRaises(release.ReleaseGateError):
            release._validate_knowledge_receipt(wrong_project, history)

    def test_actual_merge_must_be_in_base_history(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            git(fixture.repo, "checkout", "-b", "task")
            (fixture.repo / "merged.txt").write_text("merged\n", encoding="utf-8")
            git(fixture.repo, "add", "--", "merged.txt")
            git(fixture.repo, "commit", "-m", "task")
            task_commit = git(fixture.repo, "rev-parse", "HEAD")
            git(fixture.repo, "checkout", "main")
            git(fixture.repo, "merge", "--no-ff", "task", "-m", "merge")
            merge_commit = git(fixture.repo, "rev-parse", "HEAD")
            git(fixture.repo, "push", "origin", "main")
            controller = release.ReleaseController(
                repo_root=fixture.repo,
                run_root=Path(directory),
                store=release.ReleaseStore(Path(directory) / "releases").initialize(),
                transport=FakeTransport(),
                runner=CanonicalRouteRunner(),
            )
            authorization = SimpleNamespace(base_branch="main")
            pr = release.PullRequest(
                7,
                "node",
                "https://github.com/dabinglei2-hash/LifeOS/pull/7",
                "closed",
                False,
                True,
                task_commit,
                "task",
                "main",
                True,
                merge_commit,
            )
            self.assertEqual(merge_commit, controller._verify_merge(authorization, pr))
            absent = release.PullRequest(
                7,
                "node",
                "https://github.com/dabinglei2-hash/LifeOS/pull/7",
                "closed",
                False,
                True,
                task_commit,
                "task",
                "main",
                True,
                "e" * 40,
            )
            with self.assertRaises(release.ReleaseBlocked):
                controller._verify_merge(authorization, absent)

    def test_postmerge_knowledge_failure_preserves_pending_then_retries(self):
        from lads_knowledge import KnowledgeStore

        store = KnowledgeStore.memory()
        project = store.register_project("Synthetic LADS")
        store.create_task(project["id"], "Synthetic release", task_id=TASK, assigned_agent="builder")
        release_store = mock.Mock()
        release_store.save.side_effect = lambda task_id, value: dict(value)
        controller = release.ReleaseController.__new__(release.ReleaseController)
        controller.store = release_store

        def save(state, status, **fields):
            state["status"] = status
            state["ready"] = status == "MERGED"
            state.update(fields)
            return controller.store.save(state["task_id"], state)

        controller._save = save
        authorization = SimpleNamespace(
            task_id=TASK,
            source_head="a" * 40,
            candidate_sha256="b" * 64,
        )
        gate = SimpleNamespace(
            evidence={
                "qa_sha256": "1" * 64,
                "reviewer_sha256": "2" * 64,
            },
            docs_receipt_sha256="3" * 64,
            index_receipt_sha256="4" * 64,
        )
        state = {"task_id": TASK, "commit_sha": "c" * 40, "index_attempts": 0}
        with mock.patch.object(
            KnowledgeStore,
            "record_event",
            side_effect=RuntimeError("synthetic index failure"),
        ):
            with self.assertRaises(release.ReleaseBlocked) as raised:
                controller._postmerge_knowledge(
                    authorization,
                    gate,
                    state,
                    project_id=project["id"],
                    knowledge_store=store,
                    merge_commit_sha="d" * 40,
                    pr_url="https://github.com/dabinglei2-hash/LifeOS/pull/7",
                )
        self.assertEqual("MERGED_INDEX_PENDING", raised.exception.state)
        self.assertEqual("MERGED_INDEX_PENDING", state["status"])
        result = controller._postmerge_knowledge(
            authorization,
            gate,
            state,
            project_id=project["id"],
            knowledge_store=store,
            merge_commit_sha="d" * 40,
            pr_url="https://github.com/dabinglei2-hash/LifeOS/pull/7",
        )
        self.assertEqual("MERGED", result["status"])
        self.assertTrue(result["ready"])
        history = store.reader(project["id"]).get_task_history(TASK, limit=20)
        self.assertEqual("completed", history["items"][0]["state"])
        self.assertTrue(any(item["event_type"] == "release_state_changed" for item in history["items"][0]["events"]))


class CliBoundaryTests(unittest.TestCase):
    def test_help_and_import_do_not_run_git_or_network(self):
        script = Path(__file__).resolve().parents[2] / "scripts" / "lads_github.py"
        process = subprocess.run(
            [sys.executable, "-B", str(script), "--help"],
            cwd=script.parents[1],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertIn("publish", process.stdout)
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("subprocess")):
            import importlib

            import lads_automation.github_release as imported

            importlib.reload(imported)


class WorkflowContractTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def workflow(self, name):
        return (self.root / "tests" / "fixtures" / "legacy-workflows" / name).read_text(
            encoding="utf-8"
        )

    def test_job_names_are_the_exact_required_check_contexts(self):
        expected = {
            "ci.yml": ("LADS CI", "ci", "LADS CI / ci"),
            "security.yml": (
                "LADS Security",
                "security",
                "LADS Security / security",
            ),
            "docs-check.yml": ("LADS Docs", "docs", "LADS Docs / docs"),
        }
        for filename, (workflow_name, job_id, job_name) in expected.items():
            with self.subTest(filename=filename):
                text = self.workflow(filename)
                self.assertRegex(text, rf"(?m)^name: {re.escape(workflow_name)}$")
                self.assertRegex(
                    text,
                    rf"(?ms)^  {re.escape(job_id)}:\n    name: {re.escape(job_name)}$",
                )
        contexts = {item.context for item in release.REQUIRED_CHECKS}
        self.assertEqual(
            {
                "LADS CI / ci",
                "LADS Security / security",
                "LADS Docs / docs",
                "LADS / controller",
            },
            contexts,
        )

    def test_pwa_build_uses_unique_contained_output_and_current_playwright(self):
        ci = self.workflow("ci.yml")
        self.assertIn(
            'output=".ci-pwa-dist-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}-$$"',
            ci,
        )
        self.assertIn('test ! -e "$output"', ci)
        self.assertIn('node scripts/build-pwa.mjs "$output"', ci)
        self.assertIn('node scripts/check-pwa.mjs "$output"', ci)
        self.assertIn("playwright@1.64.1", ci)
        self.assertNotIn("playwright@1.55.0", ci)

    def test_browser_uses_the_isolated_repository_with_regenerated_pwa_assets(self):
        ci = self.workflow("ci.yml")
        build = ci.split("      - name: Build and verify PWA in an isolated copy", 1)[1]
        build = build.split("      - name: Run synthetic browser and JSON regression", 1)[0]
        browser = ci.split("      - name: Run synthetic browser and JSON regression", 1)[1]
        browser = browser.split("      - name: Run loopback notes API contract", 1)[0]
        self.assertIn(
            'echo "LADS_PWA_TEST_ROOT=$temporary/source/repository" >> "$GITHUB_ENV"',
            build,
        )
        self.assertIn('cd "$temporary/source/repository"', build)
        self.assertIn('cd "$LADS_PWA_TEST_ROOT"', browser)
        self.assertLess(
            browser.index('cd "$LADS_PWA_TEST_ROOT"'),
            browser.index("node tests/browser/lads-regression.cjs"),
        )

    def test_security_audits_python_and_runner_only_npm_tooling_fail_closed(self):
        security = self.workflow("security.yml")
        self.assertIn("python -m pip_audit -r requirements-lads.txt", security)
        self.assertIn(
            "npm install --prefix \"$audit\" --package-lock-only --ignore-scripts playwright@1.64.1",
            security,
        )
        self.assertIn('npm audit --prefix "$audit" --audit-level=low', security)
        self.assertIn("set -euo pipefail", security)

    def test_docs_status_supports_truthful_states_and_requires_live_pass_evidence(self):
        docs_check = self.workflow("docs-check.yml")
        self.assertIn("(PENDING|BLOCKED|PASS)", docs_check)
        for marker in (
            r"PR: https://github\.com/dabinglei2-hash/LifeOS/pull/",
            "LADS CI / ci: success",
            "LADS Security / security: success",
            "LADS Docs / docs: success",
            "LADS / controller: success",
            "Merged: true",
            "Merge commit SHA: [0-9a-f]{40}",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, docs_check)
        document = (self.root / "docs" / "LADS_GITHUB_AUTOMATION.md").read_text(
            encoding="utf-8"
        )
        self.assertRegex(document, r"(?m)^## Live GitHub E2E: (PENDING|BLOCKED|PASS)$")


class SyntheticFixtureBoundaryTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def controller_audit(self, root: Path, path: str) -> release.ChangeAudit:
        data = (root / path).read_bytes()
        authorization = SimpleNamespace(
            changes=(
                SimpleNamespace(path=path, after_sha256=sha(data)),
            ),
            deletions=(),
            binary_assets=(),
        )
        return release.audit_changes(root, authorization)

    def workflow_scan(self, path: str, data: bytes) -> bool:
        import contextlib
        import io
        import textwrap
        workflow = (self.root / "tests/fixtures/legacy-workflows/security.yml").read_text(
            encoding="utf-8"
        )
        match = re.search(r"(?ms)^          python - <<'PY'\n(.*?)^          PY\s*$", workflow)
        self.assertIsNotNone(match)
        code = compile(textwrap.dedent(match[1]), "extracted-security-workflow", "exec")
        with tempfile.TemporaryDirectory() as directory:
            def fake_run(argv, **kwargs):
                if argv[:2] == ["git", "diff"]:
                    return SimpleNamespace(stdout="A\t" + path + "\n", returncode=0)
                self.assertEqual(["git", "show"], argv[:2])
                return SimpleNamespace(stdout=data, returncode=0)

            with mock.patch.dict(
                os.environ,
                {
                    "BASE_SHA": "a" * 40,
                    "HEAD_SHA": "b" * 40,
                    "GITHUB_STEP_SUMMARY": str(Path(directory) / "summary.md"),
                },
            ), mock.patch("subprocess.run", side_effect=fake_run), contextlib.redirect_stdout(
                io.StringIO()
            ):
                try:
                    exec(code, {})
                except SystemExit:
                    return False
                return True

    def test_known_exemptions_bind_exact_path_and_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, (path, expected_digest, rule) in enumerate(
                sorted(release.KNOWN_AUDIT_EXEMPTIONS)
            ):
                data = (self.root / path).read_bytes()
                self.assertEqual(expected_digest, sha(data))
                original = root / path
                original.parent.mkdir(parents=True, exist_ok=True)
                original.write_bytes(data)
                positive = self.controller_audit(root, path)
                self.assertNotIn(
                    rule,
                    {
                        finding.code
                        for finding in positive.findings
                        if finding.severity == "critical"
                    },
                )

                copied_path = (
                    f"tests/automation/copied_{index}_{Path(path).name}"
                )
                copied = root / copied_path
                copied.parent.mkdir(parents=True, exist_ok=True)
                copied.write_bytes(data)
                copied_audit = self.controller_audit(root, copied_path)
                self.assertIn(
                    rule,
                    {
                        finding.code
                        for finding in copied_audit.findings
                        if finding.severity == "critical"
                    },
                )

                original.write_bytes(data + b"\n# changed\n")
                changed_audit = self.controller_audit(root, path)
                self.assertIn(
                    rule,
                    {
                        finding.code
                        for finding in changed_audit.findings
                        if finding.severity == "critical"
                    },
                )

    def test_controller_and_workflow_reject_unrelated_critical_content(self):
        cases = (
            (
                "safe/credential.py",
                "api_" + "key: " + ("C" * 24),
                "credential_or_key_material",
            ),
            (
                "safe/history.py",
                "git " + "reset --" + "hard",
                "history_rewrite_or_force_command",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path, text, rule in cases:
                data = text.encode("utf-8")
                selected = root / path
                selected.parent.mkdir(parents=True, exist_ok=True)
                selected.write_bytes(data)
                audit = self.controller_audit(root, path)
                self.assertIn(
                    rule,
                    {
                        finding.code
                        for finding in audit.findings
                        if finding.severity == "critical"
                    },
                )
                self.assertFalse(self.workflow_scan(path, data))

    def test_extracted_security_scanner_matches_exemptions_and_drift(self):
        for index, (path, expected_digest, rule) in enumerate(
            sorted(release.KNOWN_AUDIT_EXEMPTIONS)
        ):
            data = (self.root / path).read_bytes()
            self.assertEqual(expected_digest, sha(data))
            copied_path = f"tests/automation/copied_{index}_{Path(path).name}"
            with self.subTest(path=path, case="exact"):
                self.assertTrue(self.workflow_scan(path, data))
            with self.subTest(path=path, case="copied"):
                self.assertFalse(self.workflow_scan(copied_path, data))
            with self.subTest(path=path, case="changed"):
                self.assertFalse(self.workflow_scan(path, data + b"\n# changed\n"))


if __name__ == "__main__":
    unittest.main()
