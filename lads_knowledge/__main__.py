"""Local engineering knowledge CLI. Explicit initialization, no credential handling."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .backup import FileBackup
from .database import (FileStore, HEAD_REVISION, database_path, validate_alias, validate_home,
                       _assert_private_namespace)
from .indexer import KnowledgeIndexer, DEFAULT_DOCS, repository_ref, _json, _read_regular
from .queries import KnowledgeReader
from .safety import KnowledgeError, normalize_uuid


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # Do not echo unknown arguments, which could accidentally contain a credential.
        raise KnowledgeError("Invalid command arguments; use --help")


def parser() -> argparse.ArgumentParser:
    result = SafeParser(description="LADS 工程知识库；不读写 Life OS 用户业务数据")
    result.add_argument("--home", type=Path, help="私有工程目录，默认位于 LOCALAPPDATA/LifeOS-LADS/knowledge")
    result.add_argument("--alias", default="knowledge.sqlite3")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="显式初始化独立工程库")
    register = commands.add_parser("register-project")
    register.add_argument("--name", required=True)
    register.add_argument("--repo", type=Path, required=True)
    bind = commands.add_parser("bind-project")
    bind.add_argument("--project", required=True)
    bind.add_argument("--repo", type=Path, required=True)
    create = commands.add_parser("create-task")
    create.add_argument("--project", required=True)
    create.add_argument("--summary", required=True)
    create.add_argument("--task")
    for name in ("index-docs", "index-git", "index-pr", "index-handoff", "search", "task-history", "related-changes", "issues", "decisions"):
        sub = commands.add_parser(name)
        sub.add_argument("--project", required=True)
        if name in {"index-docs", "index-git", "index-handoff"}:
            sub.add_argument("--repo", required=True, type=Path)
        if name == "index-docs":
            sub.add_argument("--document", action="append", help="仅限正式 allowlist 的显式子集")
        if name == "index-git":
            sub.add_argument("--limit", type=int, default=30)
        if name == "index-pr":
            sub.add_argument("--snapshot", required=True, type=Path)
            sub.add_argument("--repository", required=True, help="已核实的 owner/repository")
        if name == "index-handoff":
            sub.add_argument("--file", required=True, type=Path)
            sub.add_argument("--summary", required=True)
        if name in {"search", "task-history", "related-changes", "issues", "decisions"}:
            sub.add_argument("--limit", type=int, default=10)
        if name == "search":
            sub.add_argument("--query", required=True)
            sub.add_argument("--kind")
        if name == "task-history":
            sub.add_argument("--task", required=True)
        if name == "related-changes":
            sub.add_argument("--task")
            sub.add_argument("--commit")
            sub.add_argument("--query")
        if name in {"issues", "decisions"}:
            sub.add_argument("--status")
        if name == "issues":
            sub.add_argument("--severity")
    commands.add_parser("backup", help="SQLite 在线快照，非产品 JSON 备份")
    restore = commands.add_parser("restore", help="只恢复到尚不存在的新别名")
    restore.add_argument("--backup-name", required=True)
    restore.add_argument("--target-alias", required=True)
    return result


def _existing(home, alias):
    selected = validate_home(home)
    _assert_private_namespace(selected)
    if not database_path(selected, validate_alias(alias)).is_file():
        raise KnowledgeError("Database is not initialized; run init explicitly")
    return selected


def execute(args) -> dict:
    name = args.command
    if name == "init":
        with FileStore.open(args.home, args.alias) as store:
            return {"status": "initialized", "home": str(store.home), "alias": store.alias, "revision": HEAD_REVISION}
    if hasattr(args, "project"):
        normalize_uuid(args.project)
    home = _existing(args.home, args.alias)
    readers = {"search", "task-history", "related-changes", "issues", "decisions"}
    if name in readers:
        with KnowledgeReader.open(home, args.alias, project_id=args.project) as reader:
            if name == "search":
                return reader.search_project_knowledge(args.query, limit=args.limit, kind=args.kind)
            if name == "task-history":
                return reader.get_task_history(args.task, limit=args.limit)
            if name == "related-changes":
                return reader.get_related_changes(task_id=args.task, commit_sha=args.commit, query=args.query, limit=args.limit)
            if name == "issues":
                return reader.get_known_issues(status=args.status, severity=args.severity, limit=args.limit)
            return reader.get_architecture_decisions(status=args.status, limit=args.limit)
    if name == "restore":
        if Path(args.backup_name).name != args.backup_name or "\\" in args.backup_name or not args.backup_name.endswith(".sqlite3"):
            raise KnowledgeError("Select a controlled backup filename")
        backup_path = home / "backups" / args.backup_name
        manifest_name = backup_path.with_suffix(".manifest.json").name
        manifest = _json(_read_regular(backup_path.parent, manifest_name, 64 * 1024))
        with FileBackup(home, backup_path, manifest).restore(args.target_alias) as restored:
            return {"status": "restored_to_new_alias", "alias": restored.alias, "revision": HEAD_REVISION}
    with FileStore.open(home, args.alias) as store:
        if name == "register-project":
            binding = repository_ref(args.repo)
            project = store.register_project(args.name, root_ref=binding)
            if project["root_ref"] is None:
                raise KnowledgeError("Existing project is rootless; use bind-project explicitly")
            return project
        if name == "bind-project":
            return store.bind_project_root(args.project, repository_ref(args.repo))
        if name == "create-task":
            return store.create_task(args.project, args.summary, task_id=args.task)
        if name == "backup":
            backup = store.backup()
            return {"status": "backup_created", "path": str(backup.path), "manifest": backup.validate()}
        indexer = KnowledgeIndexer(store, args.project, documents=getattr(args, "document", None) or DEFAULT_DOCS)
        if name == "index-docs":
            return indexer.sync_documents(args.repo)
        if name == "index-git":
            return indexer.sync_git(args.repo, limit=args.limit)
        if name == "index-pr":
            return indexer.sync_pr_snapshot(args.snapshot, repository=args.repository)
        if name == "index-handoff":
            return indexer.sync_handoff(args.repo, args.file, requirement_summary=args.summary)
    raise KnowledgeError("Unsupported command")


def main(argv=None) -> int:
    try:
        args = parser().parse_args(argv)
        payload = execute(args)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    except (KnowledgeError, OSError, ValueError, TypeError, KeyError) as error:
        message = str(error) if isinstance(error, KnowledgeError) else "Invalid or unavailable engineering source"
        print(json.dumps({"status": "error", "error_type": type(error).__name__, "message": message}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
