"""Bounded collectors and browser projections over engineering-only sources."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from pathlib import Path

from .store import MonitorStore, ROLES, clean, safe_path, utcnow
from .telemetry import PROJECT_REPOSITORIES, SnapshotService
from lads_knowledge.queries import KnowledgeReader

SECTIONS = {"agents", "tasks", "cost", "qa", "github", "knowledge", "settings"}
STATES = {"planned": "PENDING", "active": "BUILDING", "testing": "TESTING", "review": "REVIEWING",
          "blocked": "BLOCKED", "failed": "FAILED", "completed": "COMPLETED", "cancelled": "CANCELLED"}


def project_rows(service, project, sql, params=None):
    registry = service._load_registry()
    result, errors = [], []
    for key in (("lifeos", "devteam") if project == "all" else (project,)):
        reader = None
        try:
            if registry is None:
                raise ValueError("Registry unavailable")
            reader = KnowledgeReader.open(registry.knowledge_home(), "dual-repository.sqlite3", project_id=registry.project(key).project_id)
            values = {"project_id": registry.project(key).project_id, **(params or {})}
            for row in reader._rows(sql, values):
                result.append({"project": key, **{k: clean(v, 800) if isinstance(v, str) else v for k, v in row.items()}})
        except Exception as error:
            errors.append({"project": key, "code": type(error).__name__})
        finally:
            if reader:
                reader.close()
    return result, errors


def process_alive(pid, created):
    if not pid or not created:
        return None
    try:
        if os.name == "nt":
            from .manager import _process_creation_epoch
            observed = _process_creation_epoch(pid)
            expected = (int(created) - 116444736000000000) / 10_000_000
            return observed is not None and abs(observed - expected) < .01
        stat = Path(f"/proc/{pid}/stat")
        return stat.exists() and stat.read_text().split()[21] == created
    except (OSError, ValueError):
        return None


def agent_status(row, now=None):
    if row is None:
        return "UNKNOWN"
    kind = row["event_type"]
    if kind in {"AGENT_ERROR", "AGENT_TASK_FAILED"}:
        return "ERROR"
    if kind in {"AGENT_STOPPED", "AGENT_TASK_COMPLETED"}:
        return "OFFLINE"
    age = ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(row["timestamp"])).total_seconds()
    if age > 20:
        return "OFFLINE"
    alive = process_alive(row.get("process_id"), row.get("process_created"))
    return "BUSY" if alive else ("OFFLINE" if alive is False else "UNKNOWN")


class MonitorService:
    def __init__(self, snapshot=None, home=None, *, github=True):
        self.snapshot_service = snapshot or SnapshotService()
        self.home = Path(home) if home else self.snapshot_service.root.parent / "LADS-Workspace" / "control-center-data"
        self.store = MonitorStore(self.home)
        self.stop_event, self.thread = threading.Event(), None
        self.github_enabled = github

    def start(self):
        safe_path(self.home / "telemetry.enabled.json").write_text('{"schema":1}', encoding="utf-8")
        self.store.ingest_spool()
        def collect():
            elapsed = 300
            while not self.stop_event.is_set():
                try:
                    self.store.ingest_spool()
                    if self.github_enabled and elapsed >= 60:
                        self.collect_github()
                        elapsed = 0
                except Exception as exc:
                    try:
                        self.store.error("COLLECTOR_" + type(exc).__name__)
                    except Exception:
                        pass
                elapsed += 2
                self.stop_event.wait(2)
        self.thread = threading.Thread(target=collect, daemon=True, name="control-center-collector")
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=25)
        self.store.engine.dispose()

    def collect_github(self):
        # Fixed public repository allowlist; shell=False; no token in argv, output, cache or logs.
        fields = "name url pushedAt isPrivate autoMergeAllowed defaultBranchRef{name target{... on Commit{oid committedDate messageHeadline statusCheckRollup{state}}}} pullRequests(first:10 orderBy:{field:UPDATED_AT direction:DESC}){totalCount nodes{number title state url updatedAt headRefOid autoMergeRequest{enabledAt}}}"
        query = "query{" + " ".join(f'{key}:repository(owner:"dabinglei2-hash",name:"{repo.split("/")[1]}"){{{fields}}}' for key, repo in PROJECT_REPOSITORIES.items()) + " rateLimit{remaining resetAt}}"
        environment = {k: v for k, v in os.environ.items() if "DEEPSEEK" not in k.upper() and "OPENAI" not in k.upper()}
        environment.update(GH_PROMPT_DISABLED="1", GIT_TERMINAL_PROMPT="0")
        try:
            result = subprocess.run(["gh", "api", "graphql", "-f", "query=" + query], capture_output=True,
                                    encoding="utf-8", errors="replace", timeout=20, env=environment, shell=False,
                                    creationflags=0x08000000 if os.name == "nt" else 0)
            if result.returncode or len(result.stdout) > 200000:
                raise ValueError("GitHub request failed")
            payload = json.loads(result.stdout)
            if payload.get("errors") or not isinstance(payload.get("data"), dict):
                raise ValueError("GitHub response error")
            for key in PROJECT_REPOSITORIES:
                item = payload["data"].get(key)
                if not item or item.get("isPrivate") is not False:
                    raise ValueError("Unexpected repository visibility")
                self.store.put_cache("github:" + key, item)
            self.store.put_cache("github-health", {"status": "known", "rate_limit": payload["data"].get("rateLimit")})
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            # Keep last success alongside current error; no fake successful refresh.
            self.store.put_cache("github-health", {"status": "error", "code": type(exc).__name__})
            self.store.error("GITHUB_READ_FAILED")

    def agents(self, project):
        latest = self.store.latest_agents(project, trusted_only=True)
        history, errors = project_rows(self.snapshot_service, project,
            "SELECT role,model_name,status,started_at,completed_at FROM agent_runs WHERE project_id=:project_id ORDER BY started_at DESC LIMIT 50")
        items = []
        for role in ("gpt-controller", "scanner", "reviewer", "builder", "qa-tester"):
            runs = [row for row in latest if row["agent_id"] == role]
            active = [row for row in runs if agent_status(row) == "BUSY"]
            row = (active or runs or [None])[0]
            old = next((h for h in history if h["role"] == role), {})
            items.append({"agent_id": role, "provider": "openai" if role == "gpt-controller" else "deepseek",
                "status": agent_status(row), "sample": row, "active_runs": len(active), "history": old,
                "api_connectivity": "historical-success" if old.get("status") in {"completed", "success", "APPROVED"} else "UNKNOWN",
                "note": "20秒心跳过期或进程身份不符即离线；任务结束的工作进程显示离线。GPT桌面主脑未接心跳，保持未知。"})
        return {"items": items, "errors": errors}

    def tasks(self, project, task_id=None, limit=50, offset=0):
        where = " AND id=:task" if task_id else ""
        rows, errors = project_rows(self.snapshot_service, project,
            "SELECT id,requirement_summary,state,assigned_agent,created_at,updated_at,completed_time,commit_sha,pr_ref,test_result,review_result,error_reason FROM dev_tasks WHERE project_id=:project_id" + where + " ORDER BY updated_at DESC LIMIT :limit OFFSET :offset",
            {"task": task_id, "limit": limit, "offset": offset})
        for row in rows:
            row["lifecycle"] = "COMPLETED" if row["state"] == "completed" else STATES.get(row["state"], "UNKNOWN")
            row["priority"], row["branch"] = "UNKNOWN", None
        recent = self.store.rows(project=project, task_id=task_id, limit=200, trusted_only=True)
        by_id = {(row["project"], row["id"]): row for row in rows}
        seen = set()
        for event in recent:
            if event["event_type"] != "TASK_EVENT":
                continue
            key = (event["project"], event["task_id"])
            if key in seen:
                continue
            seen.add(key)
            row = by_id.get(key)
            if row is None and offset == 0:
                row = {"id": event["task_id"], "project": event["project"], "requirement_summary": "运行任务（工程知识库尚未登记）", "priority": "UNKNOWN"}
                rows.insert(0, row)
            if row is not None:
                row["live_state"] = event["payload"].get("state")
                row["live_updated_at"] = event["timestamp"]
                row["lifecycle"] = row["live_state"] or row.get("lifecycle", "UNKNOWN")
        timeline = self.store.rows(project=project, task_id=task_id, limit=200, trusted_only=True) if task_id else []
        historical = []
        if task_id:
            historical, _ = project_rows(self.snapshot_service, project,
                "SELECT id,event_type,occurred_at FROM task_events WHERE project_id=:project_id AND task_id=:task ORDER BY occurred_at DESC LIMIT 100", {"task": task_id})
        return {"items": rows, "events": timeline, "historical_events": historical, "errors": errors,
                "offset": offset, "limit": limit, "note": "旧知识库状态不被推断成MERGED；优先级与分支未记录时为未知。时间线仅包含工程事件元数据。"}

    def costs(self, project, agent="", model="", task_id=None, since="", until="", limit=50, offset=0):
        clauses, params = [], {"limit": limit, "offset": offset}
        for value, clause, key in ((agent, " AND a.role=:agent", "agent"), (model, " AND u.model_name=:model", "model"),
                                  (task_id, " AND u.task_id=:task", "task"), (since, " AND julianday(u.created_at)>=julianday(:since)", "since"),
                                  (until, " AND julianday(u.created_at)<julianday(:until)", "until")):
            if value:
                clauses.append(clause)
                params[key] = value
        rows, errors = project_rows(self.snapshot_service, project,
            "SELECT u.id,u.task_id,u.run_id,u.model_name,u.input_tokens,u.output_tokens,u.created_at,a.role FROM model_usage u LEFT JOIN agent_runs a ON a.project_id=u.project_id AND a.id=u.run_id WHERE u.project_id=:project_id" + "".join(clauses) + " ORDER BY u.created_at DESC LIMIT :limit OFFSET :offset", params)
        # Explicitly bounded page totals, not a claim of complete provider billing.
        prices = self.pricing()
        for row in rows:
            row.update(provider="deepseek" if "deepseek" in row["model_name"].lower() else "UNKNOWN",
                       cached_tokens=None, request_id=None, usage_source="engineering-knowledge/model_usage",
                       cost=None, cost_status="UNKNOWN")
            row["estimated_cost"] = estimate(row, prices.get("prices", []))
        aggregates, aggregate_errors = project_rows(self.snapshot_service, project,
            "SELECT a.role,u.model_name,strftime('%Y-%m-%d',u.created_at,'+8 hours') AS day,count(*) AS usage_records,"
            "sum(u.input_tokens) AS input_tokens,sum(u.output_tokens) AS output_tokens,count(u.input_tokens) AS input_known_rows,"
            "count(u.output_tokens) AS output_known_rows FROM model_usage u LEFT JOIN agent_runs a ON a.project_id=u.project_id AND a.id=u.run_id WHERE u.project_id=:project_id"
            + "".join(clauses) + " GROUP BY a.role,u.model_name,day ORDER BY day DESC LIMIT 200", params)
        return {"items": rows, "errors": errors, "offset": offset, "limit": limit,
                "input_tokens_page": sum(row["input_tokens"] or 0 for row in rows),
                "output_tokens_page": sum(row["output_tokens"] or 0 for row in rows),
                "live_usage": [row for row in self.store.rows(project=project, limit=200, trusted_only=True) if row["event_type"] == "MODEL_USAGE"],
                "actual_cost": None, "cost_status": "UNKNOWN", "pricing": prices, "aggregates": aggregates,
                "aggregate_errors": aggregate_errors,
                "note": "分页Token合计只计已返回字段；实时usage单列，未与历史重复相加。GPT桌面用量、账单及缺失字段未知。配置价格也不能成为实际账单。"}

    def pricing(self):
        path = self.snapshot_service.root / "config" / "control-center-pricing.json"
        try:
            safe_path(path)
            if path.stat().st_size > 20000:
                raise ValueError("Invalid pricing")
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("schema") != 1 or not isinstance(value.get("prices"), list) or len(value["prices"]) > 100:
                raise ValueError("Invalid pricing schema")
            for item in value["prices"]:
                if set(item) != {"model", "provider", "effective_from", "currency", "input_per_million", "cached_per_million", "output_per_million", "source"}:
                    raise ValueError("Invalid price fields")
                if item["currency"] != "USD" or item["provider"] not in {"deepseek", "openai"} or not item["source"].startswith("https://"):
                    raise ValueError("Invalid price source")
                if datetime.fromisoformat(item["effective_from"]).tzinfo is None:
                    raise ValueError("Invalid price time")
                for key in ("input_per_million", "cached_per_million", "output_per_million"):
                    value_decimal = Decimal(str(item[key]))
                    if not value_decimal.is_finite() or not 0 <= value_decimal <= 100000:
                        raise ValueError("Invalid price")
            return value
        except (OSError, ValueError, KeyError, TypeError, InvalidOperation):
            return {"schema": 1, "prices": [], "status": "NOT_CONFIGURED"}

    def qa(self, project, task_id=None, limit=50, offset=0):
        extra = " AND task_id=:task" if task_id else ""
        params = {"task": task_id, "limit": limit, "offset": offset}
        tests, errors = project_rows(self.snapshot_service, project,
            "SELECT id,task_id,test_name,status,summary,output_sha,started_at,completed_at FROM test_runs WHERE project_id=:project_id" + extra + " ORDER BY started_at DESC LIMIT :limit OFFSET :offset", params)
        reviews, _ = project_rows(self.snapshot_service, project,
            "SELECT id,review_result,test_result,error_reason,updated_at FROM dev_tasks WHERE project_id=:project_id AND review_result IS NOT NULL ORDER BY updated_at DESC LIMIT 50")
        issues, _ = project_rows(self.snapshot_service, project,
            "SELECT id,title,severity,status,source_ref FROM known_issues WHERE project_id=:project_id AND status='active' ORDER BY updated_at DESC LIMIT 30")
        counts, _ = project_rows(self.snapshot_service, project,
            "SELECT status,count(*) AS count FROM test_runs WHERE project_id=:project_id" + extra + " GROUP BY status", params)
        total = sum(item["count"] for item in counts)
        passed = sum(item["count"] for item in counts if item["status"] == "passed")
        failed = sum(item["count"] for item in counts if item["status"] == "failed")
        return {"items": tests, "reviews": reviews, "issues": issues, "errors": errors, "offset": offset, "limit": limit,
                "summary": {"run_records": total, "passed": passed, "failed": failed, "excluded": total-passed-failed,
                            "pass_rate": round(passed/(passed+failed)*100, 1) if passed+failed else None},
                "note": "统计单位为test_runs记录；summary为既有脱敏摘要，原始失败日志不向浏览器开放。无独立证据不视为独立QA。"}

    def github(self, project):
        health = self.store.get_cache("github-health")
        rows = []
        for key in (("lifeos", "devteam") if project == "all" else (project,)):
            cached = self.store.get_cache("github:" + key)
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["sampled_at"])).total_seconds() if cached else None
            rows.append({"project": key, "repository": PROJECT_REPOSITORIES[key], "snapshot": cached,
                         "status": "unknown" if not cached else ("stale" if age > 120 or not health or health["data"]["status"] != "known" else "known")})
        return {"items": rows, "connection": health, "note": "服务端每60秒只读GitHub API；保留最后成功快照，失败/过期明确标识。CI汇总不等于独立发布门禁已满足。"}

    def knowledge(self, project, q=""):
        registry = self.snapshot_service._load_registry()
        items = []
        for key in (("lifeos", "devteam") if project == "all" else (project,)):
            reader = None
            try:
                if registry is None:
                    raise ValueError("Registry unavailable")
                reader = KnowledgeReader.open(registry.knowledge_home(), "dual-repository.sqlite3", project_id=registry.project(key).project_id)
                tables = reader._rows("SELECT count(*) AS count FROM sqlite_master WHERE type='table'")[0]["count"]
                migration = reader._rows("SELECT version_num FROM alembic_version")[0]["version_num"]
                count = reader._rows("SELECT count(*) AS count,max(indexed_at) AS indexed_at FROM knowledge_items WHERE project_id=:project_id AND status='active'", {"project_id": registry.project(key).project_id})[0]
                search = reader.search_project_knowledge(q or "LADS", limit=10)
                backups = sorted((registry.knowledge_home() / "backups").glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
                items.append({"project": key, "status": "known", "database": str(registry.knowledge_home() / "dual-repository.sqlite3"),
                    "table_count": tables, "migration": migration, **count, "search": search,
                    "latest_backup": datetime.fromtimestamp(backups[0].stat().st_mtime, timezone.utc).isoformat() if backups else None,
                    "search_verified": True})
            except Exception as exc:
                items.append({"project": key, "status": "error", "error_code": type(exc).__name__})
            finally:
                if reader:
                    reader.close()
        return {"items": items, "query": q, "note": "只读打开既有工程库，检索限制10条；备份时间只来自实际备份文件，不代表已验证恢复。"}

    def settings(self):
        keys = {}
        for role, name in (("scanner", "DEEPSEEK_SCANNER_API_KEY"), ("reviewer", "DEEPSEEK_REVIEWER_API_KEY"),
                           ("builder", "DEEPSEEK_BUILDER_API_KEY"), ("qa-tester", "DEEPSEEK_QA_API_KEY")):
            present = bool(os.environ.get(name))
            if os.name == "nt" and not present:
                try:
                    import winreg
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                        present = bool(winreg.QueryValueEx(key, name)[0])
                except OSError:
                    pass
            keys[role] = present
        return {"backend": "ONLINE", "read_only": True, "listen": "127.0.0.1", "database": str(self.store.path),
                "schema_version": 1, "log_directory": str(self.home / "logs"), "keys_configured": keys,
                "github": self.store.get_cache("github-health"), "system_events": self.store.errors(),
                "monitoring": "host-outbox / 5s heartbeat / 20s expiry / 2s ingestion",
                "note": "密钥仅显示是否存在；此检查不发模型请求，不代表API有效。主脑未上报时状态未知。没有写入型HTTP接口。"}

    def section(self, name, project="all", **params):
        if name not in SECTIONS or project not in {"all", "lifeos", "devteam"}:
            raise ValueError("Invalid section")
        handlers = {"agents": lambda: self.agents(project), "tasks": lambda: self.tasks(project, params.get("task_id"), params["limit"], params["offset"]),
                    "cost": lambda: self.costs(project, params.get("agent", ""), params.get("model", ""), params.get("task_id"), params.get("since", ""), params.get("until", ""), params["limit"], params["offset"]),
                    "qa": lambda: self.qa(project, params.get("task_id"), params["limit"], params["offset"]),
                    "github": lambda: self.github(project), "knowledge": lambda: self.knowledge(project, params.get("q", "")), "settings": self.settings}
        return {"section": name, "sampled_at": utcnow(), "project": project, **handlers[name]()}


def estimate(row, prices):
    """Never changes raw usage; missing cached usage precludes a differential-cache estimate."""
    applicable = [price for price in prices if price["model"] == row["model_name"] and price["provider"] == row["provider"]
                  and datetime.fromisoformat(price["effective_from"]) <= datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))]
    if not applicable or row["input_tokens"] is None or row["output_tokens"] is None:
        return None
    price = max(applicable, key=lambda item: datetime.fromisoformat(item["effective_from"]))
    cached = row.get("cached_tokens")
    if cached is None and Decimal(str(price["cached_per_million"])) != Decimal(str(price["input_per_million"])):
        return None
    cached = cached or 0
    if cached > row["input_tokens"]:
        return None
    total = (Decimal(row["input_tokens"] - cached) * Decimal(str(price["input_per_million"])) +
             Decimal(cached) * Decimal(str(price["cached_per_million"])) +
             Decimal(row["output_tokens"]) * Decimal(str(price["output_per_million"]))) / 1000000
    return {"amount": str(total), "currency": "USD", "status": "ESTIMATED", "price_effective_from": price["effective_from"], "price_source": price["source"]}
