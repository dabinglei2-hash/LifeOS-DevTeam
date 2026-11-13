# LADS 工程知识数据库 V1

<!-- LADS3_OPERATIONAL_CONTINUATION_20261104 -->
## 2026-11-04 Stage 3 delivery/index extension

- `KnowledgePublisher` remains the only Stage 3 index path. `ACCEPT/RETRY` require an explicitly configured knowledge home, DB alias, and project ID; they never open or initialize the default engineering DB implicitly.
- Durable coordinator stages are imported only after MAIN-ACK. Document publication closes new events, then index writes preserve DB rows on failure and retain a retry reason. An index event never represents a source commit: `working_content_commit` remains `NULL`.
- Tests for this orchestration remain memory/mock in unittest discovery. Actual SQLite rollback/retry lives in the isolated file acceptance script and remains host-executed evidence.

更新：2026-11-03。**功能已实现并通过 GPT 主控本地验收；本轮独立 DeepSeek Scanner/Reviewer/QA 复核待验。** DeepSeek CLI 实际返回 HTTP 503 后，用户明确批准本轮主控实现工程代码和测试；此例外不更改未来默认团队分工。

本库属于开发系统，不属于 Life OS 产品用户数据库。没有连接用户财务、健康、浏览器存储、Notion 日志库或产品 JSON 备份。没有新增网站 API、自动上传、MCP 服务、Hook 或正式部署。

<!-- lads:decision {"id":"engineering-knowledge-local-v1","title":"工程知识与用户业务数据隔离","status":"accepted","text":"工程事实采用独立本地 SQLite、FTS5、SQLAlchemy 和 Alembic。Git 与正式文档是事实来源，知识库只作可追溯检索，不向来源写回。"} -->
<!-- lads:issue {"id":"stage2-independent-review-pending","title":"阶段2独立 DeepSeek 审查与 QA 待验","status":"open","severity":"medium","text":"本轮用户授权主控实现；主控单元及文件验收不等于独立四角色验收。服务恢复后，对精确代码摘要重新审查，不追认历史失败。"} -->

## 实现与存储位置

- 实现：[lads_knowledge](../lads_knowledge/__init__.py)，索引器 [indexer.py](../lads_knowledge/indexer.py)，CLI [__main__.py](../lads_knowledge/__main__.py)。
- 已验证运行时：Python 3.14.2、SQLite 3.50.4、FTS5 trigram、SQLAlchemy 2.0.55、Alembic 1.20.1；版本固定于 [requirements-lads.txt](../requirements-lads.txt)。使用独立虚拟环境，不改网站技术栈。
- 默认 Windows 目录：`%LOCALAPPDATA%/LifeOS-LADS/knowledge/`，数据库 `knowledge.sqlite3`；POSIX 默认 `~/.local/share/lifeos-lads/knowledge/`，POSIX 实机本轮未测。
- 当前库已经初始化；当前工作树的 project ID 见本机 `work/lads-stage2-20261103-032849/resume-20261103-175348/live-store.json`。工作树根路径只以 SHA-256 指纹绑定，不在业务记录中保存用户绝对路径。
- 数据库、备份和真实运行证据不提交 Git。目录使用独立归属标记与仅包含 `*` 的私有 `.gitignore`。本机用户目录本身是 Git 仓库：仅固定 LADS 私有命名空间可位于这个宽泛祖先下，并实际检查未被跟踪且已被忽略；项目 Git 工作树、静态目录、Notion bridge、重解析路径仍拒绝。没有修改用户目录或项目现有 `.gitignore`。

## 十张表与关系

| 实体 | 内容 |
| --- | --- |
| projects | UUID、名称、显式根指纹、创建/更新时间 |
| dev_tasks | 复合 project/task 主键、需求、状态、Agent、源 Commit、PR、测试/审查结果、错误、完成时间 |
| task_events | 事件 UUID、任务归属、类型、受限 JSON、发生时间 |
| agent_runs | 角色、模型、构建槽/尝试、状态、时间、Token、错误 |
| code_changes | 文件、变更类型、源/差异摘要、任务/run 关联和来源 |
| test_runs | 测试、命令、结果、摘要、run 关联和时间 |
| architecture_decisions | 显式决定、状态、来源与时间 |
| known_issues | 显式问题、严重度、状态、来源与时间 |
| knowledge_items | 规范来源 ID、正文块、原始/脱敏摘要、Commit、活动状态 |
| model_usage | 唯一 run 用量；未知 Token/成本为 NULL，不推测账单 |

子表以复合外键约束 project/task/run 归属；run 的 project/task/role/build/attempt 唯一，usage 每 run 一条。时间为有时区的 UTC ISO-8601。表、列、约束、索引和 FTS 定义有精确验证；`application_id=0x4C414453` 只证明应用族，不证明结构安全。

冻结迁移：`0001` 建立十表，`0002` 增加 FTS5 和维护触发器。文件接口只升级，不提供文件降级；内存降级/再升级和文件 0001→0002 已验证。未知或损坏结构在只读检查后拒绝，不自动修复或覆盖。

## 写入与只读边界

`KnowledgeStore.batch()` 使用 RLock、线程局部共享连接及 `BEGIN IMMEDIATE`；嵌套操作不独立提交。任一子操作失败使整批回滚，即使调用者吞掉子异常也不能提交。SQLite 外键、busy_timeout 与事务控制并发；两个独立文件写入者已实际验证。

`bind_project_root(project_id, root_ref)` 显式一次绑定：空→值、同值幂等、异值冲突。根指纹为 `repositories/` 加 `SHA256(os.path.normcase(str(Path(root).resolve(strict=True))).replace(chr(92), '/').encode('utf-8'))`。实际读取前还验证精确 Git top-level、边界和重解析点，不能只相信调用者传来的根指纹。

内部只读接口绑定一个 project：

- `KnowledgeReader.search_project_knowledge(query, limit=10, ...)`
- `get_task_history(task_id, limit=10)`
- `get_related_changes(task_id/commit_sha/query, limit=10)`
- `get_known_issues(limit=10, severity/status, ...)`
- `get_architecture_decisions(limit=10, status, ...)`

文件 Reader 使用 SQLite URI `mode=ro` 和 `query_only=ON`；未初始化的读取不会创建目录、数据库或迁移。返回上限 1–20 项、snippet 600 字符、总 JSON context 8,000 字节；超出时明确 `truncated=true`。三字符及以上使用 FTS5 trigram，一至二字符用转义字面 LIKE，返回真实 mode。来源内容标为引用数据，不作为执行指令；不自动向模型发送检索结果。本接口不是产品多用户认证系统。

## 索引来源与同步规则

正式文档固定为：`AGENTS.md`、`README.md`、`docs/` 下 PRD、ARCHITECTURE、STATUS、DEVELOPMENT_PROGRESS、CODEX_WORK_ENVIRONMENT、CODEX_TECH_STACK_AND_TOOLING、DATA_MODEL、AI_AGENT_SPEC、CHANGELOG、CODEX_MODEL_ROUTING、LADS_ORCHESTRATOR、LADS_KNOWLEDGE_DB 共 14 份。可选显式子集，不递归发现文件。拒绝凭据、用户数据、备份、日志、vendor、sources、数据库以及链接文件；实际符号链接和硬链接拒绝已测。

- **文档**：每份最多 512 KiB UTF-8，整体脱敏后以 6,000 字符/300 重叠分块，最多 128 块；首块用规范路径，后续 `.chunks/000002` 等为逻辑块 ID，返回 source_file/chunk_number。重复同步不改写；编辑保留已有块 ID，缩短/缺失/拒绝来源会 tombstone 多余旧块并移除 FTS。整批根、路径、声明完整性或身份冲突失败时不清空旧索引。
- **来源版本**：`observed_head` 与 `actual_content_commit_sha` 分开。只有完整文档文本与该提交 blob 相等才归属，CRLF↔LF 是唯一允许的行尾等价；原始字节 SHA-256 另存且不改原文件。其他编辑/未跟踪内容记 dirty=true、实际内容 Commit=NULL。保留未提交来源，不把其伪装成 HEAD 内容。
- **Git**：本地固定参数、限时限量读取 SHA/日期/标题/文件路径；默认最近 30、最多 100 个提交。不保存作者邮箱或原始 diff；每提交最多 200 个路径且限制正文预算，截断标记 files_truncated。窗口外旧条目不因未返回而删除。
- **PR**：只读本地规范化元数据快照，每次最多 100 条；仓库、URL、字段和时间校验，无 Token、正文、评论或静默删除。新采集需授权的 GitHub connector/CLI 在外部产生快照；索引器不联网。本轮导入的是 2026-11-02 已获取的 0 条 PR 快照，非空 PR 仅合成单元测试，未声称实时 GitHub 同步。
- **LADS 报告**：选择任务 UUID 目录中的 finalhandoff.json，核验全部 stages 路径、字节摘要和任务绑定，并验证调用者明确选择的注册仓库可解析 source_head。报告没有 repo 字段，不凭空推断；这不是对缺失仓库元数据的密码学证明。仅入库角色/模型/provider/时间/用量/结果/变更等白名单，忽略 output_tail、原始 stdout、对话、preflight/config 内容。同任务不可变快照完全相同才幂等；摘要或请求冲突整批拒绝。
- **构建与状态**：旧模型要求 build>=1，报告的 original_build 在存储槽中加 1，原始 build/attempt/provider 另存 agent_stage 事件；不更改原报告。task.commit_sha 记录源基线，不冒充结果已提交。AWAITING_MAIN_ACCEPTANCE 归为 review，完成时间保持 NULL，不推断 GPT 已验收。source_ref 中 handoffs/... 是逻辑证据 ID，导入回执提供真实源文件及原始摘要。
- **决定/问题**：仅解析围栏外独立一行的严格 `lads:decision`/`lads:issue` JSON 注释；代码围栏中的例子无效，普通文字不自动升级成审批。重复 ID 冲突回滚；移除标记后状态 inactive、历史保留，默认查询隐藏 inactive。

脱敏是已知 Token/Bearer/私钥/邮箱/电话模式，不是通用 PII 或医疗数据过滤器。UUID/SHA 身份不按电话号码误拒绝。禁止向本库导入真实个人生活记录，工程资料也须先审阅来源。

## 使用

在项目根目录、已安装 [requirements-lads.txt](../requirements-lads.txt) 的独立 Python 环境中执行。当前 Windows 已验证环境：

```powershell
$kbPython = "$env:LOCALAPPDATA\LifeOS-LADS\runtime\kb-py314-20261103-032849\Scripts\python.exe"
$kbRepo = (Get-Location).Path
& $kbPython -B -m lads_knowledge --help
& $kbPython -B -m lads_knowledge init
$kbProject = (& $kbPython -B -m lads_knowledge register-project --name "Life OS 当前工作树" --repo $kbRepo | ConvertFrom-Json).id
& $kbPython -B -m lads_knowledge index-docs --project $kbProject --repo $kbRepo
& $kbPython -B -m lads_knowledge index-git --project $kbProject --repo $kbRepo --limit 30
& $kbPython -B -m lads_knowledge search --project $kbProject --query "备份" --limit 5
& $kbPython -B -m lads_knowledge issues --project $kbProject --status open
& $kbPython -B -m lads_knowledge decisions --project $kbProject --status accepted
```

其他入口：`create-task`、`bind-project`、`task-history`、`related-changes`、`index-pr`、`index-handoff`，各自 `--help` 查看参数。默认选中 knowledge.sqlite3；`--home`/`--alias` 必须放在子命令前。除 init 外所有命令要求已有库，不隐式创建空库。错误退出 2，并且不回显非法参数值。

PR 快照格式为对象，字段严格为 repository、repository_id、observed_at、complete、items；每项为 number/title/state/url/head_sha/base_sha/merge_sha/created_at/updated_at。选择 pr-metadata*.json，再通过 `index-pr --snapshot ... --repository owner/repo` 导入。无字段的旧 API 输出须先在外部规范化，不索要 API 密钥。

## 独立备份与恢复

```powershell
$kbBackup = (& $kbPython -B -m lads_knowledge backup | ConvertFrom-Json)
& $kbPython -B -m lads_knowledge restore --backup-name (Split-Path -Leaf $kbBackup.path) --target-alias restored.sqlite3
```

SQLite 在线 backup 位于 home/backups，带 SHA-256、版本、application_id manifest，检查结构、FK、integrity 与 FTS；不复制正在写入的裸库。恢复只创建新的受控 alias，以 O_EXCL 独占创建，已有文件、损坏或外国库拒绝；不替换当前库，不宣称跨文件绝对原子性。恢复后可用全局 `--alias restored.sqlite3` 读取。私有文件不是加密保险箱；Windows 继承 ACL、其他管理员访问和独立沙箱文件 QA 尚未完成系统级验收。

## 当前验证与限制

- 主控单元/静态：38/38（22 个原核心 +16 个索引器用例），全部 memory/static，无产品数据。
- 主控实际文件/CLI：9/9，包含三项不同任务：持久化历史；真实历史合成任务 9ea064c1 的 8 次 Agent 执行、QA 退出码 1→0；合成 Git 文档编辑/缺失的增量同步。
- 可版本管理的 [file_acceptance.py](../tests/knowledge/file_acceptance.py)：8/8，实际文件/CLI/Git、十张非空表逐字段恢复、FTS、恢复创建竞态、两个文件写入者。显式运行，不混入只读 Agent 的内存测试；所有输入都是合成工程资料，不证明模型连通性。
- 另外：现版核心文件验证 5/5；实际来源符号链接/硬链接/错误工作树拒绝 3/3；原 Phase A 内存/文件迁移证据保留。
- 测试先前失败均保留：PR 来源类型/UUID 电话误判、用户目录 Git 祖先规则及 CRLF 提交归属已修复；不追认旧 BLOCKED 或 503 调用。
- 本轮不是四角色验收，独立 DeepSeek 审查/QA 待验；真实非空 PR、POSIX 实机、生产多用户隔离、真实浏览器 JSON 恢复、自动 orchestrator/Hook/MCP 联动均未验证或未实现。网站业务文件、原用户数据、备份格式和部署未修改。

```powershell
& $kbPython -B -m unittest discover -s tests/knowledge -v
& $kbPython -B tests/knowledge/file_acceptance.py
```

本机完整证据：`work/lads-stage2-20261103-032849/resume-20261103-175348/`（Git 忽略）；文件测试在 `%LOCALAPPDATA%/LifeOS-LADS/acceptance/` 保留，不自动删除。代码修改前备份为 `backups/lads-stage2-resume-20261103-175559/`，包含独立 SHA 清单。旧 Phase A 文档原件在该备份中保存；此文取代旧“Phase B PENDING”的当前描述，失败/审批历史仍在原报告和 STATUS 中。
