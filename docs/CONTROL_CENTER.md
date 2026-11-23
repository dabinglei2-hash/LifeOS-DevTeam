# LADS Control Center Stage 1

日期：2026-11-23。任务是只实现 Control Center Stage 1 的功能性总览，不声称完成八阶段路线图。状态必须分开理解：

- 来源：DeepSeek Builder初稿，首轮任务因超时/临时Git树被BLOCKED；主控修复Windows MIME、基础依赖和venv子PID归属，所有失败记录保留。
- 主控已实际执行：隔离venv安装、React typecheck/build、合成telemetry测试、API Host/Origin/只读边界、桌面/窄屏浏览器与Windows启动/检查/重复启动/停止。公开回执须区分这些主控测试与后续独立QA。
- 独立最终验收和GitHub交付：以本轮机器回执与Git历史为准，不以本文自述替代审批。
- 产品业务数据/浏览器存储未访问。页面仅读取真实工程元数据，不创建监控数据库、心跳或假账单。

## 安全审计与边界

- 仅使用既有两个工程项目的 `ProjectRegistry`、`KnowledgeReader.open(knowledge_home, "dual-repository.sqlite3", project_id=...)` 与现有 SQLAlchemy/SQLite 契约。
- 固定只读表：`dev_tasks`、`agent_runs`、`model_usage`、`test_runs`、`knowledge_items`。所有项目表查询均带 `project_id=:project_id`，最近任务最多 20 条。
- 不读取 task payload、日志、diff、凭据路径或产品业务数据。摘要与模型名再次经过 `lads_knowledge.safety.sanitize_text`，并在响应前限长。
- `model_usage` 每次按项目汇总一次，再从独立 `test_runs` 聚合 QA；不通过多表 join 重复累计 Token。null 保持 unknown，不转换为零。
- “今日”固定为 Asia/Shanghai 的显式 `+08:00` 日历边界。完成使用 `completed_time`；失败/阻塞使用 `updated_at`，因为现有表没有独立的失败时间字段。
- QA 通过率是 `test_runs` 中 `passed / (passed + failed)`，排除其他/未知状态，不声称等于单测案例数。
- 成本始终为 UNKNOWN；`cost_usd` 不作为账单估计。Agent 当前状态始终为 UNKNOWN；历史模型和最后运行记录仅来自实际 `agent_runs`，不代表在线。
- 知识库计数及最近索引时间来自 `knowledge_items.indexed_at`。Git 仅执行固定本地命令 `rev-parse HEAD`、`branch --show-current`、`remote get-url origin`，不 fetch；远端状态固定 UNKNOWN。
- 数据库打不开或查询失败时返回 generic error code 与 null 指标，不伪装成成功的零值。不创建监测数据库、迁移、缓存、SSE 或写入适配器。

## 本地运行边界

运行时根固定为 `D:/CODEX/LADS-Workspace/control-center-data`。Python venv、前端工作区、构建产物、PID/启动时间/实例状态以及 stdout/stderr 日志均在该目录，不在源码工作树。服务固定绑定 `127.0.0.1`，FastAPI 关闭 docs/OpenAPI，无 CORS wildcard，并拒绝异常 Host、外部 Origin 及 API 的 cross-site/same-site `Sec-Fetch-Site`。

安全入口：

```powershell
pwsh -NoProfile -File scripts/start-control-center.ps1 -LocalPort 8765 -NoBrowser
pwsh -NoProfile -File scripts/check-control-center.ps1 -LocalPort 8765
pwsh -NoProfile -File scripts/stop-control-center.ps1 -LocalPort 8765
```

首次显式安装依赖时，start 增加 `-InstallDependencies`；它创建/使用外部 venv、执行固定 requirements 安装，并在外部 frontend 执行 `npm ci --ignore-scripts` 和构建。普通启动只检查依赖与锁文件；源码前端目录不得出现 `node_modules` 或 `dist`。

启动前检查端口，绝不静默复用或杀死无关服务；重复启动只复用 health、PID、进程创建时间、instance ID 全部匹配的已托管服务。停止先核对真实 health 与精确进程身份，再终止该 owned PID，不按进程名清理，也不删除数据库或用户文件。管理器与启动脚本移除 API key/token/secret/password 类环境变量。

## 来源与限制

- 默认 registry：`config/project-registry.json`，base 为仓库父目录，workspace 为同级 `LADS-Workspace`。
- API：只读 `GET /api/health`、`GET /api/overview?project=all|lifeos|devteam`；非法过滤返回 422。
- UI：React 19 + TypeScript + Vite，无新增 UI 框架；同一 overview 页面每 15 秒刷新一次快照，这是 snapshot refresh，不是 Agent heartbeat。
- 另外 7 个导航项仅标记 Phase 2–8，不生成假页面或假控制。
- 该本地服务没有认证与授权层，仅适用于 single-user loopback。不得绑定 LAN 或公网，也不得把本机代理头当作身份来源。

## Stage 1 变更清单

`control_center/__init__.py`、`control_center/telemetry.py`、`control_center/api.py`、`control_center/server.py`、`control_center/manager.py`、`control_center/frontend/index.html`、`control_center/frontend/tsconfig.json`、`control_center/frontend/vite.config.ts`、`control_center/frontend/src/main.tsx`、`control_center/frontend/src/App.tsx`、`control_center/frontend/src/styles.css`、`scripts/start-control-center.ps1`、`scripts/stop-control-center.ps1`、`scripts/check-control-center.ps1`、`tests/automation/test_control_center.py`、`docs/CONTROL_CENTER.md`、`docs/PRD.md`、`docs/ARCHITECTURE.md`、`docs/STATUS.md`、`docs/DEVELOPMENT_PROGRESS.md`、`docs/CHANGELOG.md`。

修改前备份回执：`D:/CODEX/LifeOS-DevTeam/work/lads/runs/5f992691-77b9-403b-9dd7-c2a59c11a8b8/backups/build-1/manifest.json`。现有五份核心文档当时哈希与备份一致；新增文件在 manifest 中记录为 absent。

## 后续阶段（未实现）

- 阶段2：Agent真实心跳和进程状态。
- 阶段3：任务事件持久化与实时任务时间线。
- 阶段4：Token与有来源的成本统计。
- 阶段5：QA用例结果、报告详情及Reviewer监控。
- 阶段6：GitHub双仓库远端API监控。
- 阶段7：完整知识库监控与只读检索页。
- 阶段8：全模块集成验收、文档与GitHub交付。

当前只有总览可操作，其他栏目清晰标为后续，系统设置也待后续。未知Agent状态不作在线推断，历史Token并非完整账单。Windows venv重定向器的实际服务PID必须以实例健康响应、直接父PID和创建时间共同核验，不接受任意健康响应PID。资源MIME显式设定，不依赖Windows注册表。停止只处理匹配的owned PID，无状态重复停止不更改进程。

技术依据：[React构建工具说明](https://react.dev/learn/build-a-react-app-from-scratch)、[Vite文档](https://vite.dev/guide/)、[FastAPI本地服务说明](https://fastapi.tiangolo.com/deployment/manually/)。
