"""Opt-in actual file/CLI tests, synthetic sources only; no pytest/unittest discovery.

Run from any cwd: <LADS venv python> -B tests/knowledge/file_acceptance.py
All artifacts are retained in a NEW private LifeOS-LADS/acceptance directory.
This does not validate any real model/API, user database, or deployed service.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from lads_knowledge import FileStore, KnowledgeReader, KnowledgeError
from lads_knowledge.database import default_home, RELATIONAL_TABLES
from test_indexer import synthetic_handoff


def main() -> int:
    base = default_home().parent / "acceptance" / ("file-suite-" + str(uuid.uuid4()))
    base.mkdir(parents=True, exist_ok=False)
    (base / ".gitignore").write_bytes(b"*\n")
    home, repo = base / "database", base / "source"
    repo.mkdir()
    (repo / "docs").mkdir()
    doc = repo / "docs/PRD.md"
    doc.write_bytes(b'Synthetic committed alphaorigin.\n<!-- lads:decision {"id":"fixture-decision","title":"Synthetic decision","status":"accepted"} -->\n<!-- lads:issue {"id":"fixture-issue","title":"Synthetic issue","status":"open","severity":"low"} -->\n')
    def git(*args):
        run = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", timeout=30)
        if run.returncode:
            raise AssertionError("Synthetic Git fixture failed")
        return run.stdout.strip()
    git("init")
    git("add", "--", "docs/PRD.md")
    git("-c", "user.name=LADS Synthetic Fixture", "-c", "user.email=lads-fixture@invalid.example", "commit", "-m", "test: synthetic knowledge source")
    commit = git("rev-parse", "HEAD")
    checks = []
    def cli(*args, expected=0, selected=home):
        run = subprocess.run([sys.executable, "-B", "-m", "lads_knowledge", "--home", str(selected), *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=90)
        if run.returncode != expected:
            raise AssertionError(f"CLI {args[0]} exit {run.returncode}: {run.stderr[:400]}")
        return json.loads(run.stdout) if run.stdout.strip().startswith("{") else run.stdout
    def snapshot(store):
        with store._engine.connect() as connection:
            return {name: sorted([dict(row) for row in connection.exec_driver_sql("SELECT * FROM " + name).mappings()], key=lambda row: json.dumps(row, sort_keys=True)) for name in sorted(RELATIONAL_TABLES)}
    try:
        missing = base / "missing"
        cli("--help", selected=missing)
        cli("search", "--project", str(uuid.uuid4()), "--query", "x", expected=2, selected=missing)
        assert not missing.exists()
        checks.append("missing reads/help do not create DB")
        cli("init")
        pid = cli("register-project", "--name", "Synthetic file suite", "--repo", str(repo))["id"]
        index_args = ("index-docs", "--project", pid, "--repo", str(repo), "--document", "docs/PRD.md")
        cli(*index_args)
        first = cli("search", "--project", pid, "--query", "alphaorigin")["items"][0]
        assert first["actual_content_commit_sha"] == commit
        assert cli(*index_args)["updated_chunks"] == 0
        checks.append("real Git provenance and incremental no-op")
        doc.write_bytes(b"Synthetic changed betaworking.\n")
        cli(*index_args)
        changed = cli("search", "--project", pid, "--query", "betaworking")["items"][0]
        assert changed["id"] == first["id"] and changed["dirty"] and changed["actual_content_commit_sha"] is None
        doc.rename(repo / "docs/PRD.preserved.md")
        cli(*index_args)
        assert cli("search", "--project", pid, "--query", "betaworking")["count"] == 0
        checks.append("real edit/removal, stable ID and FTS cleanup")
        handoff, stages = synthetic_handoff()
        handoff["source_head"] = commit
        report_dir = base / handoff["task_id"]
        (report_dir / "stages").mkdir(parents=True)
        for name, raw in stages.items():
            (report_dir / name).write_bytes(raw)
        (report_dir / "finalhandoff.json").write_text(json.dumps(handoff), encoding="utf-8")
        args = ("index-handoff", "--project", pid, "--repo", str(repo), "--file", str(report_dir / "finalhandoff.json"), "--summary", "Synthetic local file acceptance; no API executions")
        imported = cli(*args)
        assert imported["failed_tests"] == 1 and imported["runs"] == 8
        assert cli(*args)["status"] == "unchanged"
        assert cli("search", "--project", pid, "--query", "failed")["count"] > 0
        checks.append("hashed synthetic reports, failed case retrieval and deduplication")
        with FileStore.open(home) as store:
            before = snapshot(store)
            assert all(before[name] for name in RELATIONAL_TABLES)
            backup = store.backup()
            with backup.restore("restored.sqlite3") as restored:
                assert snapshot(restored) == before
                assert restored.reader(pid).search_project_knowledge("failed")["count"] > 0
            try:
                backup.restore("knowledge.sqlite3")
            except KnowledgeError:
                pass
            else:
                raise AssertionError("Existing alias was overwritten")
            checks.append("all ten nonempty tables and FTS restored exactly to new alias")
            target = home / "raced.sqlite3"
            original_open = os.open
            def create_race(path, flags, *args, **kwargs):
                if Path(path) == target:
                    target.write_bytes(b"other synthetic writer")
                return original_open(path, flags, *args, **kwargs)
            with patch("lads_knowledge.backup.os.open", side_effect=create_race):
                try:
                    backup.restore(target.name)
                except FileExistsError:
                    pass
                else:
                    raise AssertionError("Concurrent destination was overwritten")
            assert target.read_bytes() == b"other synthetic writer"
            checks.append("actual concurrent restore target race preserves other file")
        with KnowledgeReader.open(home, project_id=pid) as reader:
            try:
                reader._connection.execute("DELETE FROM projects")
            except sqlite3.OperationalError:
                pass
            else:
                raise AssertionError("Read-only connection accepted writes")
        checks.append("physical SQLite read-only enforcement")
        stores = [FileStore.open(home), FileStore.open(home)]
        def write(number):
            identity = str(uuid.uuid5(uuid.NAMESPACE_URL, str(base) + str(number)))
            return stores[number % 2].create_task(pid, "Synthetic concurrent task " + str(number), task_id=identity)["id"]
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                assert len(set(pool.map(write, range(6)))) == 6
        finally:
            for store in stores:
                store.close()
        checks.append("two actual independent file writers preserve six tasks")
        result = {"status": "PASS", "checks": checks, "home": str(home), "synthetic_only": True, "model_api_tested": False}
    except Exception as error:
        result = {"status": "FAIL", "checks_completed": checks, "home": str(home), "error": type(error).__name__ + ": " + str(error)[:1200]}
    (base / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**result, "report": str(base / "report.json")}, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
