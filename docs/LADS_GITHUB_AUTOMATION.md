<!-- LADS_STAGE4_MAIN_LOCAL_CLOSE_20261112 -->
## 2026-11-12 主控本地验收完成，独立发布门禁保留

用户选择“完成主控验收，保留独立发布门禁”。主控任务 `62f32c0f-bc9a-56a0-a8ac-8242e0926c7a` 已完成工程198、知识库38、LADS89及浏览器14项实际回归。模式为 `main-authorized-exception`、`reviewer_independent=false`；本轮收尾未提交、推送、新建PR、合并或部署。修复仍在本地，阶段4本次发布为 BLOCKED，待有效独立最终审批。现有 PR3/PR4 的真实合并历史保留，PR4 合并后的 CI `43078398605` 失败尚未由远端修复版本复验。[完整记录](LADS_STAGE4_ACCEPTANCE_REPORT.md)

# LADS GitHub 自动化运行说明

2026-11-11 当前链路已实际验收，下方旧快照按记录时点理解。

## Live GitHub E2E: PASS

- PR: https://github.com/dabinglei2-hash/LifeOS/pull/3
- LADS CI / ci: success
- LADS Security / security: success
- LADS Docs / docs: success
- LADS / controller: success
- Merged: true
- Merge commit SHA: 2cecda7fc44d5a5458d08759d5540c3c665f876d

任务 `944c3ccd-603c-4f23-af8e-d4e9de4dd0d9`，提交 `4cd141dc2ee1c839d05bf80eb2ac3a068ffc8b01`。原生 Auto-merge 在控制器成功状态之前登记，实际合并与私有知识回写完成；同一授权 resume 幂等，合并后 main 三工作流再次通过。完整记录见 [阶段 4 验收报告](LADS_STAGE4_ACCEPTANCE_REPORT.md)。

当前实现包含精确来源/回执/文件门禁、只读 QA 可信执行适配、三个 workflow 来源绑定、失败关闭及受保护原生合并。CI 浏览器必须使用现有构建步骤产生的隔离副本，保留 Service Worker 完整性和离线断言。普通任务已有用户授权，主控按全部门禁自动处理，高风险与部署保留单独授权边界。

## 历史实现与验收快照

此前 Live GitHub E2E 声明为 PENDING，原始内容保留于历史版本和下方记录。


更新日期：2026-11-04

本文描述 Stage 4 已落地的工程实现和运行边界。它不表示真实 GitHub 端到端发布已完成；当前单元测试只使用注入的 GitHub transport。

此前状态声明已由上述真实结果更新。

## Stage 4 repair clarifications

### 2026-11-04 LADS4 hardening

- `ReleaseStateNotFound` 只表示精确受控 state 文件缺失。任务目录已有 events/staging/worktree/manifest/evidence，或 state 损坏、超限、未知、重复键、非有限数、reparse 时，控制器保留原字节并停止，不写 `INITIALIZED`。
- `state.json` 与 `events.json` 分别做 lexical、symlink/reparse、regular-file、大小、UTF-8、duplicate-key、nonfinite、shape 和 bounded-count 校验；写入前重验旧文件。事件上限、`network_failures` 尾部上限和跨 resume 单调 `network_failure_total` 均已实现。单文件 atomic replace 不声称多文件事务。
- 每次 base fetch、`ls-remote`、push、push reconciliation 和 post-merge fetch 前均重验全部 effective fetch/push URL。必须各恰有一条 canonical HTTPS LifeOS 或已审计旧 HTTPS `-` 路由；旧路由重查 canonical full_name，并比较可用的 repository ID。多路由、insecure、凭据、任意 host、URL rewrite 和 origin 重定向均 generic fail closed，不输出 URL。
- Git-before 使用 successful、bounded、unambiguous 的 literal `ls-tree`：零记录才证明 absent；present 必须恰一条 regular `blob`，mode 为 `100644/100755`，随后严格读取 object 并校验大小和 SHA-256。timeout、超限、malformed 或任何无关 `cat-file` 失败都不再解释为 absent。
- Worker receipt 用 stage ref 在 `bundle.run_root` 下 strict-contained 回读原始 stage，一次读取重算 SHA，并同时绑定 receipt 与 handoff stage ref/role_runs。原始 task/role/status/build、DeepSeek metadata 与 normalized metadata 一致；`BASE.validate_preflight_proof` 之外再绑定真实 workspace、单 `-C`、单 sandbox/approval flag、Builder 精确 writable root 和 read-only 角色空 roots。真实 5/8 阶段及返工顺序保持。
- Builder 实际测试为 automation `164 tests / OK / exit 0`、knowledge `38 tests / OK / exit 0`。sealed QA verifier、Scanner 复扫、最终 Reviewer 和真实 GitHub E2E 仍需后续独立角色执行；本节点不声称 PR/CI/merge/deploy 成功。

以下约束是本轮修复后的准确定义，若与后续旧摘要冲突，以本节为准：

- Stage 3 worker 历史按实际顺序保存，不固定为四次调用。正常流程为 Scanner、计划 Reviewer、Builder、QA、最终 Reviewer 五次；每次返工追加 Builder、QA、Reviewer。所有实际调用的 task/provider/model/stage ref 都逐项校验，最终记录必须是 QA PASS 后的 Reviewer APPROVED。
- Knowledge receipt 校验真实 `get_task_history()` envelope：顶层 `project_id`、`task_id`、`content_trust` 与唯一 task item 必须同时绑定；events、tests、changes 从该 item 读取，文档 `raw_sha256`/`source_raw_sha`、测试 command/status/output SHA 与 `index_synced` event ID 必须一致。
- 发布门禁的当前仓库清单只来自 `BASE.source_paths` 的 tracked 加 nonignored untracked 文件；每个路径再验证 containment、reparse/symlink、regular file 和精确 SHA。`qa.source_inventory` 只用于不可变 Stage 3 source/candidate 归档，不遍历真实 worktree 的 `work/`、`backups/` 或私有产物。
- authorization 分别保存 `source_before_sha256` 与 `git_before_sha256`。前者绑定 Stage 3 source bundle 的本地基线，后者绑定远端 base 的 Git blob；新 Git 文件的 Git-before 为 null 时仍可拥有已存在的本地草稿 source-before。after SHA 继续逐字节绑定。
- 发布器按仓库实际 settings 选择 `MERGE`、`REBASE` 或 `SQUASH`，顺序固定并持久化。当前仓库应选择 `MERGE`；resume 时 settings 或选择变化即停止。无可用方法进入 `AWAITING_APPROVAL`，不修改仓库设置、不 direct merge、不使用 `--admin` 或 force。
- required check contexts 是 `LADS CI / ci`、`LADS Security / security`、`LADS Docs / docs` 和 `LADS / controller`。前三者来自 workflow job 的显式 `name`，不是仅显示在 UI 的 workflow 前缀。
- CI 在每次运行唯一、且位于临时复制仓库内的未存在 PWA 输出路径构建和检查；不会修改调用方 checkout 或既有 dist。浏览器依赖固定 `playwright@1.64.1`，runner-only npm tooling 与 pinned Python requirements 都必须实际审计；registry/audit 不可验证即失败。

`Live GitHub E2E` 可以是 `PENDING`、`BLOCKED` 或 `PASS`。只有真实执行并提供 PR URL、四项 required check success、`Merged: true` 和 40 位 merge commit SHA 后才能写 `PASS`；本地 fake transport、规划或未执行状态不得写 PASS。

本 Builder 未执行真实 `gh`、GitHub REST/GraphQL 写入、push、PR、auto-merge 或知识库生产更新。控制器仍需在核验精确 Diff、Action commit pin、仓库保护和工作流设置后，另行授权一个非敏感文档任务完成真实端到端验收。任何本地 mock 结果都不能被报告为 GitHub API 成功。

## 组件

- `lads_automation/github_release.py`：标准库发布状态机、Stage 3 门禁重验、私有无 `.git` staging、隔离 Git worktree、精确索引校验、GitHub REST/GraphQL 传输和 post-merge 知识收据。
- `scripts/lads_github.py`：薄 argparse JSON CLI；`--help` 和导入不运行 Git、网络或模型。
- `.github/workflows/ci.yml`：自动化和知识单元套件、JavaScript 语法、隔离 PWA 构建、合成浏览器/JSON 回归和 loopback API 合同。
- `.github/workflows/security.yml`：完整 before/after Diff 的路径、二进制和凭据模式审计，以及现有 Python pin 的依赖审计。
- `.github/workflows/docs-check.yml`：仅检查本次变更文档的本地相对链接，并验证当前自动化文档没有遗漏固定事件和真实状态声明。

## 本地硬门禁

发布只能从真实 Stage 3 `DELIVERY_READY` journal 开始，并且必须同时满足：

1. 精确 Stage 3 handoff、`main-ack.json`、source/candidate inventory 和候选 SHA-256。
2. 独立 `qa-tester` 的实际 `PASS` command executions，以及位于其后的最终 Reviewer `APPROVED`。
3. scanner、reviewer、builder、qa-tester 四个独立 DeepSeek CLI worker 收据；角色、provider、model 和 stage SHA 均须匹配现有 Stage3Runner 产物。
4. 当前仓库 HEAD、Git 顶层、source/candidate bytes，以及精确 publisher document before/after 收据。
5. document completion events、index receipt、只读知识库 task history、QA 命令、文档 raw hash 和 index event ID 全部一致。

调用方提供的 `ready` 或 `approved` 布尔值不构成门禁。`DELIVERY_READY` 仓库漂移只允许等于 publisher 收据声明的精确 before/after 字节视图；其他漂移、模拟 evidence、主控例外或非独立 Reviewer 均失败关闭。

## 控制器授权

`authorization` 是控制器生成并审查的 JSON，schema 为 `lads-github-release-authorization-v1`。它绑定：

- `TASK_ID`、Stage 3 source HEAD、candidate SHA-256；
- canonical 仓库 `dabinglei2-hash/LifeOS`、`main` 和精确 `base_sha`；
- 严格 lowercase branch slug、demand、summary 和 reason；
- `candidate_files` 与 `prerequisite_files` 的精确 path、before SHA-256、after SHA-256 和 Git regular mode；
- 单独列出的 `deletions`，默认不存在；
- 显式 `binary_assets`，未知二进制不能通过；
- 已声明的 `risk_flags`。

Stage 1–3 的既有未提交前置工程文件必须在 `prerequisite_files` 中逐文件列出。禁止对当前脏树做 blanket stage。授权自身的 canonical SHA-256 会写入私有 release state，后续重启必须与首次完全一致。

## 私有状态与可恢复性

默认状态根为 `%LOCALAPPDATA%/LifeOS-LADS/releases`。根目录有 owner marker、deny-all `.gitignore`，任务目录必须使用 canonical UUID；每次操作拒绝 symlink/reparse、逃逸路径和大小写冲突。`state.json` 使用原子 JSON 替换，`events.json` 只保存工程元数据，跨进程任务锁覆盖整个发布动作。

主要状态包括：

- `INITIALIZED`
- `LOCAL_GATE_VERIFIED`
- `COMMIT_INTENT`
- `COMMITTED`
- `PUSH_INTENT`
- `PUSHED`
- `PR_INTENT`
- `PR_OPEN`
- `CONTROLLER_STATUS_INTENT`
- `AUTO_MERGE_INTENT`
- `AUTO_MERGE_ENABLED`
- `WAITING_CI`
- `CI_FAILED`
- `AWAITING_APPROVAL`
- `MERGED_INDEX_PENDING`
- `MERGED`

状态文件在 commit、push、PR、controller status 和 native auto-merge 前先持久化 intention。重启时读取实际分支 commit、remote task branch 和 exact head/base PR，并与其 reconciliation；同名但不同 commit、相同 head/base 的多个 PR、closed 未合并 PR、base 漂移或冲突均进入 `BLOCKED`，不会覆盖。最多四次 Builder 属于上游 LADS 规则，Stage 4 自身不自动发起返工。

`AWAITING_APPROVAL` 可带 `AUTO_MERGE_UNSUPPORTED` substatus；当仓库未启用原生 auto-merge 时不得退化为立即 merge。

## Git 与 GitHub 边界

1. 验证 `git rev-parse --show-toplevel` 精确等于授权仓库，fetch 后 `origin/main` 必须等于授权 `base_sha`。
2. 将批准字节复制到私有 owned staging，再从精确 base SHA 创建 detached worktree。
3. 只 overlay 授权路径，对每个 path 独立执行 `git add -- <path>`；删除只能来自单独授权。
4. 检查 index mode 只能是 `100644` 或 `100755`，拒绝 `120000`、冲突、未暂存内容和额外文件。
5. 只 commit 一次，commit 必须包含 `TASK_ID`、父提交等于 base，完整 `base..commit` Diff 必须等于授权 manifest；commit blob 的 SHA-256 也逐路径重验。
6. 只 push `codex/<TASK_ID 前 8 位>-<slug>` 任务分支，不做 force push、不 push `main`、不使用 `--admin`。
7. 创建 PR 前按 exact head/base 查询 `state=all` PR，覆盖 closed/merged；只有不存在时才 create，模糊 mutation 先 reconciliation，不盲目重试。

调用方 dirty tree 与 index 不被修改。私有 worktree 和 staging 不自动删除，便于恢复与审计。

## PR、保护与检查

PR body 只包含 TASK_ID、demand、summary、精确路径、实际测试命令、独立 DeepSeek 审阅状态、controller acceptance、文档/知识收据和风险。它明确说明 `LADS / controller` 是机器验收，不是 GitHub human review；不包含 prompt、测试 stdout、个人数据、凭据或本地绝对用户路径。

普通发布要求现有 `main` 保护/规则满足：

- enforce administrators；
- 当前 head 必须通过 required checks；
- 禁止 force push 和 branch deletion；
- 保护 `ci`（LADS CI / ci）；
- 保护 `security`（LADS Security / security）；
- 保护 `docs`（LADS Docs / docs）。
- 保护 `LADS / controller` 机器验收 context。

`LADS / controller` 是 commit status，绑定 exact commit、QA SHA、Reviewer SHA、document receipt、index receipt 和 evidence digest。发布控制器从不修改保护、不冒充人工 review、不关闭 review thread、不使用 admin merge。

只有 exact PR head SHA 等于已提交 manifest、PR 非 draft、无冲突、无未解决阻塞 thread、reviewDecision 不是 `CHANGES_REQUESTED`，且 required checks 全部 true success 时，才调用 GraphQL `enablePullRequestAutoMerge`。required check 的 `skipped`、`neutral`、`cancelled`、`timeout` 或 failure 都不算成功。GitHub pending 保持 `WAITING_CI`，失败进入 `CI_FAILED`；轮询次数有限。

## Post-merge 与知识索引

PR API 的 `merged=true` 不是最终结论。控制器还要确认 `merge_commit_sha`，fetch base，并验证该 merge commit 是 remote base 的祖先。只有两步都成功才进入 post-merge 知识写入。

知识更新沿用现有 `KnowledgeStore` API：任务枚举只在合法的 `active/completed` 路径内到达 `completed`；commit、PR、测试和 review 写现有列；release 大写状态写入同 task 的幂等 `release_state_changed` event，不新增 schema、不迁移数据库。Git merge 成功后若知识写入失败，状态保留为 `MERGED_INDEX_PENDING` 并按上限重试；不回滚 Git、不删除已成功事件。

## Event coverage

文档检查要求当前发布文档保留以下事件覆盖说明：

- `requirements_changed`
- `architecture_changed`
- `data_model_changed`
- `agent_rules_changed`
- `toolchain_changed`
- `workflow_changed`
- `task_state_changed`
- `release_changed`
- `issue_found`
- `task_delivered`

语义文档事件仍需现有 DocumentPublisher 的 before/after receipt；普通发布不得伪造 semantic approval。

## CI 安全

三个 workflow 只使用 `pull_request` 和 push 到 `main`，没有 `pull_request_target`。顶层权限固定 `contents: read`，checkout 使用 `persist-credentials: false`，每个 job 有 timeout，Action 使用 commit SHA pin。Workflow 不读取仓库 secrets，不执行 PR title/body，不写仓库或发布 artifact 以外的数据。

PWA build 在 runner 临时复制目录执行，因为现有 build 会写 `sw.js`。Playwright 和浏览器安装在 runner temp，浏览器/API/JSON 证据只来自合成上下文，并上传到有保留期限的 CI artifact。真实账号认证和生产 AI 仍未实现；loopback notes API 合同会显式输出 `NOT_IMPLEMENTED`，不把占位能力写成通过。

工作流本身是本次明确授权的 CI bootstrap。未来普通发布若改动 workflow、Action 或保护策略，授权必须包含 `workflow_policy_change`；删除需单独授权并触发 `deletion` 风险；secret/key、数据库迁移、身份认证和 production deployment 默认等待控制器批准。未知二进制必须通过 exact path + SHA-256 asset allowlist。

## 验证边界

本阶段单元测试使用独立临时 Git 仓库和注入的 fake GitHub transport，覆盖精确 staging、dirty tree/index 保留、commit/PR 去重、source/evidence 漂移、traversal/reparse、伪造 ready/QA/review、高危和合成凭据模式、CI failure、网络重试上限、保护拒绝、unsupported native auto-merge、真实 merge ancestry，以及 post-merge knowledge retry。

这些测试不证明：

- 真实 GitHub 仓库 canonical redirect、保护、rules 或 auto-merge 设置已符合要求；
- 真实 workflow checks 已在 GitHub runner 通过；
- 真实 PR 已合并；
- iPhone、真实多用户认证、生产通知、AI 或部署已实现。

仓库仍由 GPT/OpenAI controller 统一决策。DeepSeek worker 不提交、不 push、不整合、不部署；控制器必须在独立复核后执行最终授权动作。


## 2026-11-05 本地工程修复，尚未启用发布

主控按本轮用户明确例外完成修复，本地测试不冒充独立 QA。候选文档 A→B 与真实 publisher B→C 以精确 receipt 兼容；每次逐文件 add 使用进程命令参数 `core.autocrlf=false`，原始 blob 哈希仍严格；不再通过空 hooksPath/no-verify 绕过钩子，存在活动 hook 时阻断；固定合成样本仅按精确路径与原始 SHA 豁免凭据形状检查。控制器与提取的 security 脚本均验证负向案例。

真实四角色合成任务已通过（八次独立 DeepSeek 调用、两次构建、QA 失败后返工通过）；这不等于全部发布工程改动的独立审查。保留上述 Live GitHub E2E: PENDING；本轮没有执行发布。详见 [工程修复](LADS_ENGINEERING_REPAIR_20261105.md)。
