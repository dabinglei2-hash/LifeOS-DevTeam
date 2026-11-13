<!-- LADS3_FOCUSED_LIVE_ACCEPTANCE_20261104 -->
## 2026-11-04 两项真实验收与重现

本轮原生回合和独立四角色 UI 均通过；完整证据、边界见 [验收报告](LADS_STAGE3_ACCEPTANCE_REPORT.md)。主控 native Pre/Post/Stop 与外部 DeepSeek worker_completed 各司其职，外部 CLI 不触发原生 SubagentStop。

```powershell
$LadsPython = 'C:/Users/Administrator/AppData/Local/LifeOS-LADS/runtime/kb-py314-20261103-032849/Scripts/python.exe'
$NativeProbeId = 'native-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
& $LadsPython -B tests/acceptance/lads-native-hooks.py --namespace $NativeProbeId --timeout 360 --execute-live
$UiProbeId = 'ui-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
& $LadsPython -B tests/acceptance/lads-stage3-live.py --work-namespace $UiProbeId --timeout 600 --execute-live --credential-source windows-user
```

上述重现会实际调用模型，只在获得测试授权后运行；模块导入/--help 不发请求。原生测试通过 LADS_HOOK_ACCEPTANCE_NAMESPACE 选用自己的已拥有日志，保留沙箱所需 LOCALAPPDATA，避免改动默认工程任务。

交接恢复必须复核实际文件 wire 清单、候选摘要、原 stage SHA 与最终审批；本轮恢复未重复模型调用或改写回执。直接项目 JSON Hook 发现仍需 scripts/lads_session.py 兼容入口和正常信任流程；不保证桌面会话热加载。

以下保留此前快照；与本节冲突的旧 PENDING/BLOCKED 只代表当时状态。

<!-- LADS3_MAIN_ACCEPTANCE_20261104 -->
实际 Hook 命令进程 smoke 已通过（合成 stdin、独立私有日志），覆盖风险拒绝、失败状态收集、外部进程去伪装和有界 Stop；不替代原生模型自动触发。正式知识索引的备份/新别名恢复、18 文档增量、重复索引与来源检索也已通过，见当前验收记录。

原生加载/信任依据：[OpenAI Hooks 文档](https://learn.chatgpt.com/docs/hooks)。本机实测直接项目发现与会话配置存在差异，不以文档支持事件代替实际触发证据。

## 2026-11-04 操作入口与实测

核心主控验收通过，详见 [验收报告](LADS_STAGE3_ACCEPTANCE_REPORT.md)。普通团队入口 `scripts/lads_stage3.py RUN/MAIN-ACK/ACCEPT/RETRY` 保持独立审核；本轮显式主控 API 不能被自动 fallback 选择。

```powershell
$LadsPython = 'C:/Users/Administrator/AppData/Local/LifeOS-LADS/runtime/kb-py314-20261103-032849/Scripts/python.exe'
& $LadsPython -B -m unittest discover -s tests/automation -v
& $LadsPython -B -m unittest discover -s tests/knowledge -v
& $LadsPython -B scripts/lads_stage3.py --help
python -B scripts/lads_session.py --check
python -B scripts/lads_session.py
```

七个 Skills 各具 name/description、适用场景、步骤、安全、输出及验收，已实际发现。新建工程知识索引前必须确认文档审查与测试依据，保留源 SHA、任务和实际 observed HEAD。文档失败阻止交付，索引失败保留重试载荷；本轮在实际私有文件/SQLite 环境验证了两条失败恢复路径。

剩余：原生模型回合自动触发探针超时；直接项目 JSON Hooks 发现为 0，采用显式会话配置兼容加载并正常信任；外部进程不触发原生 SubagentStop。产品真实多用户/AI 权限和 iPhone 实机未通过或未实现，不包含在主控核心 PASS 里。

以下保留此前记录；与本节冲突的旧状态按历史快照理解。

<!-- LADS3_OPERATIONAL_CONTINUATION_20261104 -->
## 2026-11-04 Stage 3 operational continuation

- `lads_automation/rpc.py` now provides a serial, credential-free app-server JSON-RPC executor. It performs the verified `initialize`/`initialized` handshake plus `command/exec`, sends a non-empty pinned argv array with `env` overrides, preserves only allowlisted runtime metadata including original `PATH`/`CODEX_HOME`, and enables the Windows sandbox service at process start. It forces workspace-write with no network, contains cwd/writable roots below one owned acceptance root, bounds output/time, and verifies Windows process-tree cleanup without silent timeout acceptance.
- `scripts/lads_stage3.py` now has operational `RUN`, `MAIN-ACK`, `ACCEPT`, and `RETRY` commands over the existing coordinator, journal, `AutomationEngine`, `DocumentPublisher`, and `KnowledgePublisher`. `RUN` stops before integration; MAIN-ACK is a controller action, not deploy/push/merge authorization.
- The base manifest keeps the two immutable unittest commands. `Stage3Runner.contract_manifest()` materializes the positional sealed-broker verifier before coordinator state/plan/review, and `Stage3Runner` accepts only the resulting exact three-command contract. QA no longer expands commands inside `worker_call`. Missing, timed out, nonzero, source-drifted, or unsealed receipts remain failures.
- `ACCEPT` imports all actual model-role stage metadata into bounded `worker_completed` receipts with timestamps, provider/model/tokens, and stage refs. A fresh index-failure `RETRY` accepts only the exact publisher before/after document state and matching journal completion events bound to the same task and MAIN-ACK.
- `FileJournal.resolve_session()` now fails closed when any task state is unreadable, emits only a bounded count/reason, and PreToolUse denies rather than guessing a task. Browser cleanup now awaits Playwright `browser.close()` separately from callback-based HTTP server close.
- The notes API suite calls the actual `server/notes_server.py` routes with an injected synthetic Notion client. It is not the removed browser fake-echo check. Product account authentication and AI tool runtime remain `NOT_IMPLEMENTED`.
- Actual automation/knowledge unittest discovery is the only model-QA proof for this infrastructure build. File, browser, app-server, backend HTTP, MAIN-ACK finalization, and the opt-in live synthetic UI workflow remain host-broker/live items until their retained receipts say otherwise. Native hooks remain uninstalled/untrusted.
- The current recovery has one allowed-scope conflict: the allowed staged helper differs from formal-before, while `engineering/lads-protocol-fix/install-map.json` is outside the 45-path allowlist and still binds the older helper hash. The protocol-guard automation test therefore remains failed until the controller resolves that map path under separate authorization.

<!-- LADS3_MAIN_BLOCKED_20261104 -->
## 2026-11-04 主控验收：LADS 阶段 3 BLOCKED

**阶段 3 未完成，已停止自动返工。** 累计四次构建/三次返工，独立 QA 的 automation 75 项与 knowledge 38 项通过，但最终 Reviewer 仍为 `CHANGES_REQUESTED`。浏览器关闭 Promise/回调接口混用、任务状态损坏被静默跳过仍是阻塞。

正式工作树仅有已受审 Foundation 与非保护路径协议候选；Part B 37 文件及七 Skill/原生 Hooks/执行器仍在隔离候选中，未安装、未激活、未索引。三只读角色的真实拒写探针、PWA/15 路由和视口基线通过，不代表 CRUD/JSON 恢复、完整 UI 团队链路或原生 Hook trust 通过。实际子任务使用 DeepSeek；GPT 主控配置未变。无业务数据变更、提交、推送、部署或自动合并。

具体文件、证据、剩余运行层工作及备份见 [阶段 3 验收记录](LADS_STAGE3_ACCEPTANCE_REPORT.md)。下方是保留的历史记录，不代表本次最终验收。

# LADS 工程自动化 Foundation

更新：2026-11-03。本文只描述 Life OS 的工程开发自动化，不是产品用户数据库、个人资料、健康/财务记录、浏览器存储或 Notion 数据说明。

<!-- LADS3_FOUNDATION_BUILD3_20261103 -->
## Build 3 Reviewer 修正

- 文档事件在 QA/Reviewer 验证后仍可登记，但一旦进入 `DOCS_PENDING`、`DOCS_FAILED` 或更后阶段即关闭；`BLOCKED` 不能再次进入文档或索引阶段。
- 每个文档写入先保存包含 before/after SHA、event IDs 和 receipt SHA 的 started/completed 记录；重试可识别“已写入但未记账”的单个文档，同时拒绝覆盖写入后发生的用户编辑。文件与 SQLite 仍不宣称跨系统原子性。
- 索引失败保留原 reason、收据、证据和 attempt；重试复用同一事件 ID/payload，避免成功提交后状态未落盘造成重复 event、document section 或 knowledge row。
- Hook 持久化必须同时解析到已注册 task/session，记录 session 哈希而非原始 session；重复 Stop 通过幂等事件最多产生一次 continuation。`worker_completed` 等控制器域事件不会进入 native `SubagentStop` 路径。
- `DocumentPublisher` 继续使用精确文档 allowlist、仓库指纹、私有写入前备份、旧 SHA 检查和原子单文件替换；本节不等于真实文件/权限/恢复 smoke 已通过。
- Builder 实测两条规定命令均为 exit 0：automation 25/25，knowledge 38/38；独立 QA 仍需重跑，且该结果不覆盖 Part B 或真实 I/O。

<!-- LADS3_FOUNDATION_BUILD2_20261103 -->
## Build 2 Reviewer 修正

- `AutomationEngine.sync_documents()` 现在在公共入口直接执行文档失败次数上限；`sync`、`retry` 和直接 Python 调用共享同一有界门禁。达到上限后进入 `BLOCKED`，不会继续追加失败记录。
- `PreToolUse` 命令检查新增重定向、PowerShell 文件写入命令、`sqlite3` 变更语句及代码型敏感写模式；凭据文件和既有私密业务数据库路径的明确写入返回 `deny`。仅引用敏感路径但未识别为写入的未知命令返回 review context。`echo`/`printf` 的纯引号夹具文本不按写入拒绝。
- 新增三个行为测试方法覆盖命令型敏感写入、引号夹具误报、直接 `sync_documents()` 重试上限。该修正的独立规定 QA 和最终 Reviewer 复核仍待后续角色执行；本文不把 Builder 内存探针写成 QA PASS。

## 当前边界

- 任务 `1c432bf8-ae19-4148-aef4-ed6f51ce7cc7`、Build 1 实现事件驱动文档同步、知识索引同步、证据门禁和纯 Hook 处理器。
- 实现只使用本地 Python 标准库与既有 Stage 2 知识库；没有 HTTP、MCP、server、cron、隐藏 LLM 调用、依赖安装或网络访问。
- 没有安装或信任 native Hooks，没有运行浏览器 QA，没有 team-bridge/Skills，也没有做真实 UI 演示。这些属于 Part B，状态仍为 `PENDING`。
- 不自动提交、推送、合并、部署、迁移或整合。`DELIVERY_READY` 只表示机器门禁已满足，不是最终用户验收。

## 稳定 Python API

- `lads_automation.state.MemoryJournal`：QA 用内存日志，语义与文件日志一致。
- `lads_automation.state.FileJournal.create()/open()`：仅使用 `%LOCALAPPDATA%/LifeOS-LADS/automation`，POSIX 回退到 `~/.local/share/lifeos-lads/automation`。合成文件测试只能显式使用 `acceptance_namespace`，其目录位于 `LifeOS-LADS/acceptance/<namespace>`。
- `lads_automation.evidence.TaskSpec`：固定 `task_id`、`source_head`、`candidate_sha256`、允许修改路径和不可变 QA 命令。
- `load_controller_receipts(controller_path, containment_root)`：只通过 controllers 指定的有界哈希引用加载 QA、Reviewer 和 controller 文件。
- `validate_stage_acceptance(spec, receipts, simulation=False)`：验证 QA 实际 `command_execution`、非零/跳过/缺失、同一候选、独立最终 Reviewer `APPROVED`。`simulation=True` 仅用于单元夹具，返回证明继续标记 `simulated=true`。
- `AutomationEngine(journal, publisher, knowledge)`：执行 `verify`、`record_event`、`sync`、`retry` 和派生状态查询。
- `MemoryPublisher`：内存文档发布。`DocumentPublisher(repo_root, backup_root=..., registered_repository_ref=...)`：真实仓库的窄追加发布器；备份必须位于固定 LADS 私有命名空间。
- `KnowledgePublisher(store, project_id, repo_root)`：要求已存在且已绑定的知识库项目，不自动初始化或绑定。
- `handle_hook(payload, journal=None)`：纯 Hook 处理器；实际配置安装和信任留待 Part B。

## 工作流门禁

正常状态顺序：

```text
TASK_CREATED -> QA_REVIEW_VERIFIED -> DOCS_PENDING -> DOCS_SYNCED
             -> INDEX_PENDING -> DELIVERY_READY
```

失败状态为 `QA_FAILED`、`REVIEW_FAILED`、`DOCS_FAILED`、`INDEX_FAILED` 或 `BLOCKED`。测试/复核失败不能进入文档阶段；文档失败不能进入索引；索引失败保留受限重试载荷和尝试次数，最多 3 次自动重试，超过后阻塞。

`ready` 由已验证证据、文档回执、索引回执和未提交工作内容状态派生，不接受调用方直接提供。文档和索引失败重试都限定为最多 3 次自动尝试；文档上限位于公共同步入口，不依赖调用方先经过 `retry()`。

## 固定事件路由

| 事件 | 唯一目标文档 |
| --- | --- |
| `requirements_changed` | `docs/PRD.md` |
| `architecture_changed` | `docs/ARCHITECTURE.md` |
| `data_model_changed` | `docs/DATA_MODEL.md` |
| `agent_rules_changed` | `docs/AI_AGENT_SPEC.md` |
| `toolchain_changed` | `docs/CODEX_TECH_STACK_AND_TOOLING.md` |
| `workflow_changed` | `docs/CODEX_WORK_ENVIRONMENT.md` |
| `task_state_changed` | `docs/DEVELOPMENT_PROGRESS.md` |
| `release_changed` | `docs/CHANGELOG.md` |
| `issue_found` | `docs/KNOWN_ISSUES.md` |
| `task_delivered` | `docs/TASK_HISTORY.md` |

语义产品事件还须携带目标文档的显式审批摘要，且该摘要必须等于发布前的乐观 SHA。自动化只追加带任务、事件、证据引用的窄记录，不生成或改写产品需求正文。

## 日志与发布语义

- 每个任务使用 UUID 目录、`state.json` 与 `events.json`；单文件采用同目录临时文件加 `os.replace`。文件与 SQLite 之间、多个文档之间都没有跨文件原子性承诺。
- 事件 ID 幂等；相同 ID、不同类型或载荷会冲突。未知 session 不写日志。任务锁有时间上限。
- 文档发布先做乐观旧 SHA 检查，再做私有独立备份和 SHA 清单，然后只追加结构化记录。重试若看到完整事件标记则幂等；若用户并发编辑使旧 SHA 不一致，则保留用户内容并失败。
- `KNOWN_ISSUES.md` 和 `TASK_HISTORY.md` 仅在这两个固定路径缺失时创建标题。其他固定文档缺失不会被自动创建。
- `Working content commit` 始终为 `NULL`；`observed_head` 单独记录。不能把未提交文档误称为已提交结果。

## 知识索引

`lads_knowledge.indexer.DEFAULT_DOCS` 从 14 项扩展为恰好 17 项，新增：

- `docs/KNOWN_ISSUES.md`
- `docs/TASK_HISTORY.md`
- `docs/LADS_AUTOMATION.md`

没有 SQLAlchemy 模型或 Alembic schema 变更。真实同步复用 `KnowledgeIndexer.sync_documents()`；内存 QA 可复用同一 `index_documents()` 完整快照。文档、任务事件、typed test metadata 与 document change metadata 在外层 `KnowledgeStore.batch()` 内提交；任一步失败整体回滚。

## CLI

```powershell
python -B -m lads_automation --help
python -B -m lads_automation create-task --help
python -B -m lads_automation status --task-id <uuid>
python -B -m lads_automation record-event --help
python -B -m lads_automation sync --help
python -B -m lads_automation retry --help
python -B -m lads_automation hook
```

CLI 不处理 API key，不执行任意 SQL 或 shell。`hook` 从 stdin 读取至多 64 KiB JSON；输出保持有界。

## Hook 安全边界

已验证的官方事件名为 `PreToolUse`、`PostToolUse`、`SubagentStop` 和 `Stop`。纯处理器可以拒绝明显的 `git reset --hard`、`git clean`、push/force、deployment/merge、直接浏览器存储清除和凭据/业务数据库写入；命令型写入覆盖 shell 重定向、常见 PowerShell 写入命令及 `sqlite3` 变更语句。该匹配仍是已知模式防护，不是完整命令解释器或 sandbox。

这些是补充防护，不是完整 sandbox 或 native tool coverage。`PostToolUse` 只保存退出状态/结果摘要哈希，不保存命令、stdout/stderr、对话、prompt 或 `last_assistant_message`。未知退出状态记为 `unknown`。`SubagentStop` 只记录 native 事件；独立 DeepSeek CLI 不会伪造该事件。`Stop` 对不完整交付最多请求一次有界 continuation，随后仅记录 warning。

## Part B 明确待办

- 浏览器 QA runner 与真实 UI 演示。
- team-bridge/Skills 联动。
- native Hooks 的精确配置审查、安装与 trust。
- FileJournal 实际文件权限/并发/恢复测试。
- DocumentPublisher 真实仓库文件与私有备份 smoke。
- 真实现有知识库绑定、索引和冲突恢复 smoke。
- Part B 结构化 QA receipt broker；不得把当前单元证明描述为浏览器通过。

Stage 2 独立 Reviewer/QA 待验事实继续保留；本 foundation 的自动化 QA 不能替代 Stage 2 全量复核。
