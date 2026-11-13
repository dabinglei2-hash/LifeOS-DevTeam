# Life OS 长期开发规则

适用范围：本项目及其子目录。用户本次明确授权优先；遇到数据、功能或架构冲突，先解释影响与可选方案，等待决定。默认用中文沟通。

## 1. 每次开始

1. 先读 [产品需求](docs/PRD.md)、[实际架构](docs/ARCHITECTURE.md)、[当前进度](docs/STATUS.md)，再读本次涉及的代码。聊天和旧报告只能提供线索，不能代替代码证据。
2. 检查 `git status --short`、当前分支、相关历史；不得覆盖、暂存或提交与本次任务无关的未提交工作。
3. 检查相关源码备份、JSON 备份机制和已知测试。代码备份、Git 提交都不等于浏览器数据备份；不能核实真实 JSON 备份时须说明，不能假定已安全备份。
4. 修改已有源码前建立带时间标识的独立副本和清单，保留旧备份。仅创建新文档时可通过修改前清单、Git 状态及修改后校验保护原文件，无须复制整站。
5. 明确本次范围、依据和验证方式。先做小范围、可回滚的改动；发现已有实现先复用，不重复建站或迁移框架。

## 2. 数据与业务底线

- 禁止清空、重置或用合成数据覆盖真实 localStorage、IndexedDB、记录和个人资料；禁止演示数据自动初始化。
- 不改已有 ID、记录日期、历史正文、金额含义、分类快照、存储键及 JSON 格式；新增字段也可能被严格校验拒绝，必须先审阅兼容方案并获确认。
- 不擅自调整日期范围、统计公式、舍入、分页或排序规则。收支差额为收入减支出，金额为 USD；日报关联明细不能再次累加。
- 保留五个主入口、隐藏页面、来源返回和浏览器前进/后退；不得为改 UI 删除学习、任务、收支、统计、妙想、个人空间或备份功能。
- 复用现有保存、冲突检查、事务式写入、导入预览及确认；不自动覆盖导入冲突。localStorage 多键回滚不等于数据库事务，不得宣称绝对原子性。
- 不删除、移动、覆盖已有 JSON 和源码备份；不上传个人记录、连接银行、索要密码、引入依赖、发布或部署，除非用户明确授权。
- 真实数据只在必要且明确授权时操作；自动化测试只用独立浏览器上下文和合成记录，不连接日常浏览器配置。

## 3. UI 与实现

- 手机优先，延续黑白灰、克制圆角与阴影。复用 `styles.css` 变量、`ui-icons.js` 图标、`ui-system.js` 公共交互；参考细则见 [UI-SYSTEM.md](UI-SYSTEM.md)。
- 普通控件至少 44×44 CSS px；保留焦点、无障碍名称、选中反馈、减少动态效果支持和安全区预留。
- 保留手机固定底部导航及桌面导航；不靠缩小字体、隐藏操作或伪造未实现入口压缩页面。
- 新功能先检查 `app.js` 和相关 helper；不要平行建立另一套存储、日报或路由。无法确认的行为标注“待确认”。
- 发现 JavaScript 错误，记录触发步骤、文件、行号和调用栈，暂停相关改动后修复根因；不要用空 catch 或屏蔽日志掩盖错误。

## 4. 结束、测试与文档

1. 每次结束执行与变更相称的测试：文档任务检查内容、链接及修改范围；功能任务检查语法、受影响流程和必要回归。不能运行的项目明确记录原因，不称“通过”。
2. 涉及数据操作时验证新增、原 ID 编辑、确认删除、刷新、重复保存、统计及 JSON 导出/恢复；涉及 UI 时检查手机、短屏、桌面、底部遮挡和控制台。
3. 历史脚本可能依赖本机路径、端口和固定日期，先审阅再运行；不得把合成 JSON 导入真实来源，也不以旧截图替代当前验证。
4. 每次任务更新 `docs/STATUS.md`：日期、范围、文件、实际测试/未测项、证据、风险、后续事项。保留历史变更记录，当前摘要保持简短。
5. 架构、数据、路由、构建或备份机制获准变更时同步更新 `docs/ARCHITECTURE.md`；需求或产品决定变化时更新 `docs/PRD.md`。
6. 结束前检查 Git 差异；用中文汇报结果、备份位置、验证和限制。未获授权不提交、不推送、不部署；用户要求等待时停止下一阶段开发。

## 5. 文档分工

- PRD：产品要做什么、边界和验收；提案不等于批准实施。
- ARCHITECTURE：当前代码如何工作；未来方案必须与现状分开。
- STATUS：已核实的进度、证据、问题和下一步；区分本次测试、历史测试及未验证描述。
- 既有 README、报告和备份保留为使用说明/历史证据。有矛盾先核对当前代码并说明，不静默改写历史。


## 2026-11-02 Codex 开发团队配置

实际仓库：`D:\CODEX\Life OS`；`D:\CODEX\gerenshenghuoguanliwangzhan` 为指向该目录的链接。复用现有仓库，不创建 LifeOS 副本。CLI 实测版本为 `0.165.0-alpha.1.2`。

配置位于 `.codex/config.toml` 与 `.codex/agents/{scanner,reviewer,builder}.toml`。采用独立文件的 name、description、developer_instructions 和 agents.enabled/max_concurrent_threads_per_session；模型继承会话，不固定模型、不增加业务依赖。

主控流程：记录 Git 基线及未跟踪文件 → Scanner 收集证据 → Reviewer 审查方案 → 重大决定交用户确认 → Builder 按范围实现和测试 → Reviewer 审查实际 Diff → 主控核验并中文交付。只有 Builder 写入，禁止多个写入者同时操作同一工作区。审批必须绑定 HEAD、工作区基线和具体 diff；发生变化重新审查。

统一交接：任务ID、仓库绝对路径、HEAD、工作区基线、目标、允许/禁止文件、用户授权及方案审批、证据路径与行号、发现和严重度、实际测试/未测项、风险、下一角色。主控负责传递完整交接，不把 Reviewer 的技术意见当作用户授权。

权限落实：官方明确子 Agent 继承父会话，Agent TOML 无法覆盖 sandbox_mode/approval_policy。Scanner 和 Reviewer 仅允许从独立只读父会话启动：`codex -C "D:\CODEX\Life OS" --sandbox read-only --ask-for-approval never`，在其中明确调用 scanner 或 reviewer。Builder 使用独立 workspace-write 会话，且与审查阶段串行。不得使用绕过权限参数；禁用可写外部连接器。普通 Git worktree 不是只读隔离。本次当前桌面会话权限为 unrestricted，不能用于声称 Scanner/Reviewer 已获得只读隔离；主控必须先核实新会话有效权限，无法核实时停止派发只读角色。当前仅完成配置落盘，未声称端到端 Agent 调用或 Windows 只读执行实测通过。

本次仅开发流程配置及文档追加，业务、数据库、依赖和密钥不变；未提交、推送、部署。保留原有未提交内容。官方依据：https://learn.chatgpt.com/docs/agent-configuration/subagents 。

## 2026-11-02 混合模型开发团队（优先于上方旧模型继承说明）

GPT/Codex 是唯一主控，保留现有 OpenAI 登录。Scanner 使用 deepseek-flash；Reviewer/Builder 使用 deepseek-v4-pro。三者只能通过用户级独立 DeepSeek profile 工作进程启动，禁止派给继承 GPT Provider 的原生子 Agent，禁止静默回退 GPT。角色指令见 `.codex/worker-prompts/`，路由与验收见 [模型路由](docs/CODEX_MODEL_ROUTING.md)。

复杂任务：主控规划 → DeepSeek Scanner → 主控方案 → DeepSeek Reviewer → 主控/用户决策 → 独立 worktree DeepSeek Builder → Scanner 复扫 → Reviewer 审 Diff → 主控验收。只有 Builder 修改业务源码；Scanner/Reviewer 必须验证只读。Reviewer 状态为 APPROVED / CHANGES_REQUESTED / BLOCKED，最多三轮返工，BLOCKED 立即交主控。技术批准不是推送、部署或迁移授权。

当前缺少 DEEPSEEK_API_KEY，API、工具和完整协作均未验收；停止模型测试与角色派发。旧 `scripts/lifeos-team.py` 是上一轮未验收的 GPT 继承适配器，不用于新流程。此次主控仅按用户明确的配置任务准备无密钥档案、角色文本和文档，不代表 DeepSeek Builder 已执行。恢复前先核实本机版本及档案，勿使用一键安装改写主配置。


### 2026-11-02 密钥与验证状态更正

三个 profile 现分别读取 DEEPSEEK_SCANNER_API_KEY / DEEPSEEK_REVIEWER_API_KEY / DEEPSEEK_BUILDER_API_KEY。官方 API 和独立 CLI 的三个角色个体工具/权限、合成人工交接已实测；上文“缺少密钥”仅保留为历史。完整自动调度器、三轮状态机和 MCP 未验收；仍禁止原生 GPT 子 Agent 或静默 GPT 兜底。新会话先核实用户级环境变量有效，Windows 工具优先明确使用 PowerShell；见 docs/DEEPSEEK-VALIDATION-20261102.md，不能将历史配置检查等同于生产全链路验收。


### 2026-11-02 最新模型决定（覆盖旧 Pro 分配）

按用户授权，Scanner/Reviewer/Builder 均通过各自独立 profile 使用 deepseek-flash（当前官方映射 DeepSeek-V4.1-Flash）；GPT/OpenAI仍为唯一主控。Reviewer/Builder旧deepseek-v4-pro仅作历史，独立密钥、只读/写权限、禁止GPT回退规则不变。新的五步合成人工调度链路已实测通过，见 docs/DEEPSEEK-FLASH-ACCEPTANCE-20261102.md。生产自动调度、三轮自动返工及MCP仍未验收，不可宣称一键系统已完成。

<!-- LADS_STAGE1_CONTINUE_20261102_START -->
## 2026-11-02 LADS Stage 1 独立进程编排规则

正常 live 任务只允许经 `scripts/lads_orchestrator.py --task-file` 启动；离线和 argv 检查可使用 `scripts/lifeos-deepseek.py <role> --check`。四角色分别使用 `scanner-deepseek`、`reviewer-deepseek`、`builder-deepseek`、`qa-tester-deepseek` 用户级 profile、`deepseek-flash` 和各自专用环境变量；禁止原生子 Agent、GPT 回退、rollout/私人会话读取及自动整合。

Builder 只能在 `work/lads/runs/<task>/builder` 独立 Git worktree 写精确 allowlist；QA 只读复制候选且不含 `.git`，PASS 必须有规定测试的实际 command_execution、completed 和 exit 0。正式 worker 前必须用同一 executable/cwd/profile/argv 前缀和严格子环境执行实际权限探针；权限块缺失、重复、歧义、过宽或非 never/restricted 即 `BLOCKED`。`turn.completed` 必须是最后事件，stage 证据逐次持久化，QA 历史测试不覆盖，清理前验证 UUID runroot/Builder/父链/子项 reparse containment。候选、scope 门禁、严格 final JSON、最多 4 次 Builder 及所有限制以 [编排器说明](docs/LADS_ORCHESTRATOR.md) 为准。真实四角色 live 与主控实际 CLI 权限探针仍 `LIVE PENDING`；技术流程不能替代用户授权、真实数据保护或生产安全验收。
<!-- LADS_STAGE1_CONTINUE_20261102_END -->


<!-- LADS1_ACTUAL_BLOCKED_20261103 -->
## 2026-11-03 LADS 当前运行门禁：BLOCKED

Windows 本地 CodexSandboxOffline 已由只读 CIM 核实 Lockout=True；近期 C、D 盘实际 Worker 的 CreateProcessWithLogonW 报 1909/1326。停止新的 live Worker/登录重试，直到受支持的沙箱账户与启动环境修复并复验。严禁 unrestricted、关闭沙箱或静默切 GPT 绕过；不得复制 sandbox 凭据、改密码/账户策略或解锁账号，除非用户另行授权。主控仅追加阻断事实，未接管 DeepSeek Builder 业务源码。

已集成的14文件、70项本地测试和实际权限预检不代表八调用验收成功。冻结 per-attempt CODEX_HOME 与共享 Windows 沙箱执行账户的兼容性未完成验证。后续先读 docs/LADS-STAGE1-REPORT.md；此前正常 live 入口说明受本门禁限制。未操作业务数据、提交、推送或部署。


<!-- LADS_SYNTHETIC_ACCEPTANCE_CURRENT_20261103 -->
## 2026-11-03 LADS 合成流程主验收（当前成功）

任务 `9ea064c1-ab38-4dd6-b4b5-95f36a08b6fb` 完成真实八阶段调用并获 GPT 合成验收；此事实仅取代相关 PENDING/BLOCKED 门禁，历史不删、不追认失败。GPT/OpenAI `gpt-6-astra` 为主控，四角色独立 DeepSeek CLI 用 `deepseek-flash`。受保护配置由 Builder 写 ignored 候选，Scanner/Reviewer 批准精确 SHA，主控经授权备份后原字节安装并测试；worker 隔离不放宽。批准不等于提交、推送或部署。

## 2026-11-04 Stage 3 opt-in event and broker rule

Stage 3 uses explicit controller commands and treats DeepSeek `worker_completed` events as controller-domain process completion, never native `SubagentStop`. A credential-free host broker owns writable isolated copies, process lifetime, browser contexts, and private artifacts; the read-only model QA runs the two prescribed unittest commands plus the mandatory sealed-report verifier. N/A cannot hide a failed browser suite, rejected evidence cannot become completion, and delivery remains closed until actual QA, final review, document receipt, and index receipt gates pass.

## 2026-11-04 LADS 阶段 3 当前执行补充

本轮用户明确授权 GPT 主控直接实现、测试和修复工程层，DeepSeek 审查可选；回执必须标为 main-authorized-exception、reviewer_independent=false。该例外只适用于本轮，未来默认四角色独立工作进程与禁止静默 GPT 回退继续生效。

阶段 3 团队任务使用 scripts/lads_stage3.py RUN 的密封 QA/MAIN-ACK/ACCEPT/RETRY 链路。主控验收不得把 Builder 自述当测试证据。只有真实变更才发相应文档事件；索引失败保留重试状态，不声明交付成功。知识库只含工程事实，不含业务数据。

原生 Hook 兼容启动器为 python -B scripts/lads_session.py，保留正常 /hooks 信任；定义变更须重新审查。当前桌面热加载、原生模型触发和独立团队 UI 尚未通过；不得声称全自动团队已完成验收。实际结果先读 docs/LADS_STAGE3_ACCEPTANCE_REPORT.md。

## 2026-11-04 原生 Hook 与独立团队补验（当前事实）

两项此前未通过的验收现已实际通过，证据见 docs/LADS_STAGE3_ACCEPTANCE_REPORT.md。本机 OpenAI/gpt-6-astra 原生 Pre/Post/Stop 完成真实回合；DeepSeek 四角色八调用/两构建完成独立 UI QA、返工、最终批准和文档/索引交付。不要继续把旧 PENDING 当现状，也不要把合成 UI 验收扩称为全部业务或全部工程 Diff 已被独立审查。

后续复杂任务继续按独立 DeepSeek 工作进程流程执行；本轮主控工程例外不自动推广。原生会话用 scripts/lads_session.py 正常加载/信任，外部 CLI 完成用 worker_completed，不伪造 SubagentStop。保持实际权限预检、不可变候选/审批绑定、私有测试、MAIN-ACK 和失败关闭，不自动提交推送部署。
