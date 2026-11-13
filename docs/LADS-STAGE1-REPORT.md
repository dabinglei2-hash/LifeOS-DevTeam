# LADS Stage 1 交付报告

更新：2026-11-02。状态：**LADS1-REPAIR1 ENGINEERING TESTED/REVIEW PENDING；ACTUAL CLI PREFLIGHT AND FULL LIVE PENDING**。QA 两个角色文件已由主控按审查哈希安装；本报告不把 Builder 自测、API 200 或历史角色验收等同于完整 live orchestrator 通过。

## 范围

实现 stdlib 编排器、qa-tester 独立配置/提示/schema、确定性测试和文档；扩展 DeepSeek helper 的 QA profile/key 映射。复用现有未提交源码基线，不重建项目，不修改业务存储、真实数据、依赖、主配置或远程仓库。

## 关键行为

- UUID task 与 HEAD、非忽略源码内容/状态基线、manifest hash 绑定。
- Scanner/Reviewer/QA read-only，Builder 独立 Git worktree；候选与 QA 快照位于 ignored `work/lads/`。
- QA 候选 digest、实际 prescribed command execution、退出码和快照前后哈希校验。
- 严格最后一条 assistant JSON、角色/状态绑定、正常 exit 0 和无原生 child/spawn；`turn.completed` 必须唯一且是绝对最后事件。
- QA FAIL 仍交 final Reviewer；最多 4 次 Builder；阻断、越界、漂移、超时和权限/格式错误 fail closed。
- Windows 超时与正常退出后清理遗留后代进程，不依赖可能被环境策略拒绝的 `taskkill` 成功返回。
- 每次 worker 调用前用相同可执行文件/cwd/profile/argv 前缀和严格子环境执行实际 `debug prompt-input`；解析唯一有效权限块，验证 read-only 无 writable root 或 Builder 精确 workspace root，以及 network/approval/temp 覆盖后 fail closed。
- 子环境只允许运行目录/home/PATH/TLS/proxy、必要 `CODEX_HOME` 和当前角色唯一密钥；每次调用持久化脱敏 bounded stage JSON、命令/退出码/输出尾/metadata/输出与文件哈希，QA 测试跨 build 保留。
- UUID runroot、expected Builder、父链和全部子项 reparse 检查先于递归清理；状态/事件/stage 路径同样强制 runroot containment。

## 实际本地验证

最终 `python -B -m unittest discover -s tests/lads -v`：30 项通过、0 失败，耗时约 32.9 秒。Python 语法编译、schema `json.tool` 解析及 orchestrator/helper 两个 `--help` 均 exit 0。非法 timeout、缺失 manifest、两个无参数 CLI 均按预期 exit 2。历史首跑为 22 通过、1 失败：timeout 后代进程仍存活；根因为本环境拒绝 `taskkill`（Access denied）。已改为标准库 PID 树枚举与终止，并增加“父进程正常退出后清理后代”回归，专项和全量复测通过。

历史阻塞已关闭：主控已把审查通过的 `.codex/agents/qa-tester.toml`（`c232e6…`）与 `.codex/worker-prompts/qa-tester.md`（`7c4b84…`）精确安装，本轮没有修改这两个受保护文件。它们仍只描述角色，实际权限由用户级 profile、helper argv 和运行前权限探针证明。

## LADS1-REPAIR1 增量

- 输入绑定：Reviewer `CHANGES_REQUESTED`/修复计划 APPROVED，HEAD `aea9e758…`，14 路径现值与基线一致，139 个非允许基线项失配 0。
- 备份：`work/lads-bootstrap/backups/pre-repair1-20261102-190545261/` 保存修改前 14 文件字节和哈希；`pyc-hashes.json` 先记录 3 个精确编译产物。
- 清理：仅在 leaf/non-reparse/workspace containment/哈希四项复核后逐文件删除 Reviewer 列出的 3 个 `.pyc`；未删除目录、glob 结果或用户缓存。
- 测试：最终 `python -B -m unittest discover -s tests/lads -v` 为 38/38 通过、0 skipped；覆盖实际 fixture 正反例、same-prefix/环境预检、终态追加事件反例、stage 保留与脱敏、外部路径、junction 父链/内部 junction 与 stage 父目录哨兵。语法使用 `compile(source,...)`，schema JSON 解析通过。
- 本 Builder 未运行 live Codex/API。实际 CLI/profile 权限探针及完整四角色 live 仍由主控在复审后执行；缺失证明必须 `BLOCKED`，不能以单测替代。

## 未测与限制

- 真实四角色 API、profile、CLI 权限和完整 live 状态流：`LIVE PENDING`，由主控在 Diff 审查后执行；Builder 沙箱不调用 API/Codex。当前 fixture/parser/argv/环境/失败分支测试不等同于实际 CLI 探针通过。
- QA 专用 API 与独立只读 unittest/写入拒绝已有主控侧历史证据，但本轮未重新调用，不能替代完整 orchestrator live 结果。
- 浏览器、移动设备、多用户、真实用户 JSON、生产并发、OS-level per-file ACL 和主机级安全隔离未测。junction 检查与删除之间的 TOCTOU 不能由 Python 检查消除。
- Scope 是停止后全树哈希门禁；候选不自动采用、合并、提交、推送或部署。
## 2026-11-02 audit evidence repair

Status: `LADS1 AUDIT EVIDENCE REPAIR ENGINEERING TESTED/REVIEW PENDING`; actual
four-role CLI preflights, DeepSeek route/tool provenance, the complete live
synthetic workflow, Windows host enforcement, and real-data safety remain
`LIVE PENDING`.

- Failure stages now persist direct command, actual exit code, bounded/redacted
  output tail, and stdout SHA-256 for preflight, timeout, nonzero-exit,
  bounded-output, launch, and malformed-report failures. Transient errors retain
  the same evidence in their stage mapping.
- Successful preflight proof is persisted per role call with sanitized
  same-prefix debug argv, exit code, stdout hash, validated permissions and roots,
  and config hashes. Live `RealWorker` execution fails closed when that proof is
  absent.
- QA raw command events are used only for runtime validation. Typed QA commands
  and report/control-plane fields are canonicalized to the exact prescribed
  Python `-B` commands and redacted before state, stage, final handoff, Reviewer
  prompt, approval, or rework feedback. Secret-like test commands are rejected
  during manifest loading.
- Stage `output_tail` is a separate bounded diagnostic capture. It intentionally
  may include up to 4000 generic-redacted stderr/stdout characters, including
  command wrappers. Redaction is pattern/current-environment-key based, not a
  universal private-content filter. A full raw transcript is not saved.
- Local tests use mocked `RealWorker` process results for the four requested
  failure classes and synthetic `not-a-real` sentinels. The existing sentinel
  test had an EMPTY `output_tail`, so it proves canonicalization/redaction of
  typed and control-plane fields only, not that command wrappers are absent from
  an actual captured tail.
- Local engineering verification: the complete existing Python unittest path
  under `tests/lads` and source compilation without bytecode are rerun before
  handoff. `python -B -m unittest discover -s tests/lads -v` completed with
  50/50 passing in 39.572 seconds; compile-only checks covered 10 Python files
  and left `pyc_count=0`.
- No live CLI/API, credential, real user data, dependency, commit, merge, push,
  deployment, or source integration was performed.

## V2 plan and stage evidence follow-up (2026-11-02)

Status: `V2 ENGINEERING TESTED / TARGETED REVIEW PENDING`; the new live run
remains `LIVE PENDING`. This follow-up was implemented by the sole Builder after
explicit main authorization and is not an automated fifth rework.

The stage envelope now persists a successful role's exact sanitized `report`
object together with `report_sha256 = SHA-256(canonical(stage.report))`.
`report_sha256` retains its established meaning and is not a hash of a raw
model message; raw reports/transcripts are unavailable to this control plane and
are not stored. Failed pre-report stages use `null`. The worker report schema
and `finalhandoff.stage_reports` `path`/`sha256` references are unchanged.
Legacy stage files remain untouched, including their historical hash values.

`output_tail` now means the true final 4000 Unicode characters after the complete
existing redaction pipeline. It is redacted before tail selection, respects the
existing `MAX_CAPTURE` and final redaction bounds, never appends a marker beyond
the 4000-character limit, and does not retain a raw transcript. Regression tests
cover a sentinel in the true suffix and a secret beginning at the former cutoff.

All role invocations carry explicit `PHASE=...` prompts. The plan stage binds
source HEAD/digest, the proposed task, exact allowlist, immutable tests, required
QA/final flow, and explicitly calls the baseline unfinished. A non-approved
plan persists its safe report and stops before Builder with `builds=0`.
`--demo` recognizes only the transparent initial-negative build; final review
has no demo exemption. QA `FAIL` requires final Reviewer
`CHANGES_REQUESTED`, and QA `PASS` acceptance still requires `APPROVED`.

The preserved first real blocked artifact is task
`9bb69d0f-37ad-44c6-9457-b4a426dc7fde` at source HEAD
`d192d3c39450af2bdba2b14b0c87a33f946c29e5`, with terminal status `BLOCKED`
and `builds=0`. Its stage files predate the sanitized `report` field, so the
exact underlying rejection reason is unavailable and is not reconstructed.

Engineering verification for this follow-up: `python -B -m unittest discover -s
tests/lads -v` passed 55/55 tests; compile-only checks covered 7 Python source
and test files; repository `.pyc` count remained 0; `git diff --check` was
clean. No live Codex/API child call, live demo, dependency installation,
credential access, real-data operation, commit, push, deployment, or source
integration was performed by this Builder. Targeted Scanner and Reviewer
verification, followed by a separately authorized live run, remain pending.

## Combined approved compatibility implementation (2026-11-02)

Status: `COMBINED ENGINEERING TESTED / SCANNER-REVIEWER PENDING`; actual fresh
four-role CLI operation remains `LIVE PENDING`.

The approved QA terminal mapping and trust/handoff V2 plans were implemented in
one sole-Builder job while preserving the existing overall-14 ceiling and the
six-file allowlist: the helper, core orchestrator, orchestrator tests, this
report, the LADS orchestrator guide, and an append-only STATUS entry. The
pre-change timestamp backup is
`work/lads-bootstrap/backups/pre-combined-approved-20261102-225758328/`; its six
source copies were SHA-verified against the source files.

Implemented behavior includes: typed terminal `failed` command events from the
observed CLI protocol; aggregate QA `FAIL` semantics for mixed suites; strict
Git-root/backlink/common-dir validation with per-root ephemeral trust overlays;
all-termination configuration drift checks preserving original safe process
evidence; run-level main-config raw-SHA gates; and bounded/redacted Builder/QA
handoffs. The Windows demo task now explicitly names PowerShell/pwsh, the one
unchanged Python `-B` test command, and the three-file synthetic source boundary.

Verification is local and synthetic. After the final no-Git coverage
adjustment, `python -B -m unittest discover -s tests/lads -v` passed 63/63
tests in 59.343 seconds. Source compilation without bytecode, JSON/schema
checks, `git diff --check`, exact six-file scope, and final hashes are rerun
before handoff. No live
Codex/API/child call, live demo, credential access, real user data operation,
dependency installation, commit, push, deployment, merge, or source integration
was performed.

Preserved historical artifacts remain: task
`9bb69d0f-37ad-44c6-9457-b4a426dc7fde` is `BLOCKED` with `builds=0` at source
HEAD `d192d3c39450af2bdba2b14b0c87a33f946c29e5`; task
`05059ec5-5e91-47d0-b1cf-709c920635af` is `BLOCKED` with `builds=1`. The
existing `main-config-drift-analysis.json` remains the evidence for the main
config drift only; the exact actor is unknown and no manual global rollback is
claimed. The current main default route is unchanged. Fresh host verification,
global raw-SHA stability, effective permissions, real model routing, and
Scanner/Reviewer re-review remain pending.
## Frozen per-role CODEX_HOME implementation (2026-11-03)

Status: `ADAPTER ENGINEERING TESTED / SCANNER-REVIEWER PENDING`; complete live
acceptance remains `PENDING`.

This sole-Builder change implemented the Reviewer-approved isolated-home plan
at HEAD `aea9e758baac88c310319f34a77f94f2364b0c11` under the existing exact-six
implementation ceiling: `scripts/lifeos-deepseek.py`,
`scripts/lads_orchestrator.py`, `tests/lads/test_orchestrator.py`,
`docs/LADS_ORCHESTRATOR.md`, this report, and an append-only STATUS entry. The
other eight paths in the existing 14-path LADS ceiling were not edited. The
pre-change timestamp backup and parseable Python-generated manifest are under
`work/lads-bootstrap/backups/pre-frozen-home-adapter-20261102-235856879/`;
all six copied files matched their pre-change source hashes.

The adapter binds `RealWorker` to an already validated task UUID run root and
creates a fresh UUID runtime home for every role attempt. It rejects an
existing, escaping, aliased, or reparse-chain target and forbids user-home
equality, original-`CODEX_HOME` equality, and existing auth/config/session alias
cases. Per the approved clarification, a new managed subtree inside an explicit
Git worktree is allowed even when the worktree itself is under the user home;
no general user-home write or source-data copy is introduced.

The four original profiles/catalogs remain static route authority. Their route
and hashes are checked, while runtime files are generated from a strict field
whitelist. Only the selected catalog is copied. The generated base and role
profile pin `deepseek-flash`, `lifeos_deepseek`, the exact DeepSeek responses
endpoint and role key reference, read-only/workspace-write mode,
`approval_policy="never"`, `notify=[]`, `windows.sandbox="elevated"`, disabled
web/apps/plugins/native agents, and no hooks/MCP. Git roots are persisted as
real trusted project tables; hooks fail closed and discovered project MCP names
are disabled.

The helper check/command surfaces accept an explicit home only for offline or
managed invocation and do not mutate `os.environ`. Child environments are
copies that replace only `CODEX_HOME` with the generated home while retaining
one current-role key. The exact same environment and argv prefix feed
preflight and worker execution. Proof includes the runtime-home path,
home/config hash material, original-authority hash, and actual read-only or
exact Builder writable-root permission evidence.

The bind-time authority inventory and every runtime/project/prompt/helper/
executable file are checked before preflight and on success, launch failure,
timeout, nonzero exit, malformed output, and preflight failure. The independent
orchestrator raw-SHA guard for the original main config remains unchanged.
Tests cover minimal secret-free generation, four role/mode bindings, real TOML
trust, catalog-only copying, no auth/session/env-value copy, hooks/MCP
containment, fresh/contained/non-reparse homes, user-home-descendant allowance,
parent-environment isolation, same-prefix debug/exec, runtime tamper, and
original-authority tamper.

The earlier host prototype is `LIMITED` evidence for one disposable Builder
call only: `work/lads-bootstrap/frozen-home-prototype-result.json` records
`canary_written=true`, `main_unchanged=true`,
`runtime_hashes_unchanged=true`, and `auth_file_created=false`. It does not
prove the full eight-call role sequence, real permission enforcement for every
role, retries/rework, route provenance, or production safety.

Historical blocked artifacts remain unchanged:

- `9bb69d0f-37ad-44c6-9457-b4a426dc7fde`, first real run, `BLOCKED`,
  `builds=0`, source HEAD `d192d3c39450af2bdba2b14b0c87a33f946c29e5`, reason
  `plan review did not approve`.
- `05059ec5-5e91-47d0-b1cf-709c920635af`, second real run, `BLOCKED`,
  `builds=1`, source HEAD `833f388b8968d9d1ca3a6e2fbe0d7382955d7891`, reason
  `builder profile/prompt/executable changed during run`.
- `ed68049a-a127-4fd0-89c8-5aa96f309f1f`, third real run, `BLOCKED`,
  `builds=1`, source HEAD `8928aaf686303b2f1c12043981124055fd9f7a52`, with
  `builder configuration drift detected: config.toml changed` despite the
  command-local trust overlay.

No live Codex/API child was invoked by this Builder. No credential content,
real user record, browser data, dependency, source backup, Git history, commit,
push, deployment, merge, or integration was touched. Fresh independent
Scanner/Reviewer review of the six-file diff, then a separately authorized
fresh eight-call host run, remain required.

Finalization (2026-11-03): the prior 900-second Builder process is recorded as
interrupted and incomplete, not as a successful delivery. Its 69/69 result
preceded the last guard patch. After the guard was rechecked before `exec` and
its focused tests passed, the one final complete suite run passed 70/70 tests
with no failures or skips in 93.812 seconds. Scanner/Reviewer review and the
fresh host live eight-call acceptance remain `PENDING`.


<!-- LADS1_ACTUAL_BLOCKED_20261103 -->
## 2026-11-03 主控实际验收：BLOCKED

本节是当前开发团队状态，优先于此前 LIVE PENDING/运行入口说明。主控仅记录已观察证据，未接管 DeepSeek Builder 的业务源码工作。当前 C 盘活动 worktree 已人工集成审查批准的 14 个文件；用户原有修改保留，132 个范围外基线文件 SHA 全部未变，未提交、推送、部署或操作浏览器真实数据。

当前代码本地测试通过：C 盘实际运行 `python -B -m unittest discover -s tests/lads -v`，70/70、0 errors/failures/skips、74.478 秒；D 盘 Builder 最终 70/70、93.812 秒。冻结配置四角色实际 debug 权限预检在 C、D 均通过，三只读角色 read-only、Builder 唯一 workspace-write，network restricted、approval never。以上不能替代真实八次角色调用验收。

真实完整流程未通过。C 盘任务 `b721233a-be64-4c7d-98b7-04aad2290c81` 在 Scanner 启动工具时失败，CreateProcessWithLogonW 2147942522；D 盘任务 `ea2d318c-d625-47e0-9fab-687952d0eb5d` 也在 Scanner 失败，Windows 1909/1326。最后独立 Reviewer 同样无法启动 PowerShell/Node，结论 BLOCKED。主控只读 CIM 查询确认本地 `CodexSandboxOffline`：Enabled、Lockout=True、Status=Degraded；`CodexSandboxOnline` 未锁定。未尝试解锁、改密码、修改 ACL 或放宽沙箱。

**在 Windows 沙箱账户修复并验证之前，停止新的 live Worker 启动和登录重试。不得改用 unrestricted、关闭沙箱、静默换 GPT 或伪报完整验收。** 单个冻结 HOME 的 Builder 原型曾成功创建合成 canary，且用户/运行时配置哈希不变、auth.json 未生成；它仅证明该次组件实验，不证明多 HOME 下共享 Windows 沙箱执行账户的凭据兼容性。每角色/每次调用独立 CODEX_HOME 的 Windows 实用性尚未验收，必须重新审查共享 OS 执行账户/凭据缓存的耦合；不得读取、复制 sandbox 密码或 API 密钥来绕过问题。

主控默认配置仍为 GPT/OpenAI；四角色代码固定 DeepSeek/deepseek-flash 和各自环境变量。API 返回成功与工具可执行是不同证据。CLI 仅支持规定的 stdout-only Python -B 测试；浏览器、PWA 实机、多用户、生产并发和真实 JSON 恢复未测/未实现适配。原有业务源码与数据结构未改。

后续需要用户授权的独立操作：仅诊断并修复 Windows Codex 沙箱执行账户/启动环境（超出项目文件修改范围）；不索取或显示密码、不修改账户策略或普通用户账号。解锁前先审查 CODEX_HOME 与共享 Windows 凭据缓存策略，避免恢复后再次锁定；修复后必须先进行只读 canary/写入拒绝验证，再重跑 Scanner→Reviewer→Builder→QA→Reviewer→GPT 的实际失败/返工/通过链路。未获授权不执行账户修复。最终独立复审及八调用验收保持未完成。


### 交付范围、证据与修复建议

新增：scripts/lads_orchestrator.py、.codex/agents/qa-tester.toml、.codex/worker-prompts/qa-tester.md、schemas/lads-task-report.schema.json、tests/lads/test_orchestrator.py、docs/LADS_ORCHESTRATOR.md、docs/LADS-STAGE1-REPORT.md。更新：scripts/lifeos-deepseek.py、AGENTS.md、STATUS/ARCHITECTURE/DEVELOPMENT_PROGRESS/CODEX_WORK_ENVIRONMENT/CODEX_TECH_STACK_AND_TOOLING。

编排器已有 UUID、状态机、实际报告归档、超时进程树终止、只读瞬时重试上限、首版+最多三轮返工、worktree/QA 候选副本、哈希/范围门禁与统一交接。数学 demo 候选不自动复制回源仓库。保留过往真实 BLOCKED：9bb69d0f（plan/build0）、05059ec5（配置漂移/build1）、ed68049a（配置漂移/build1）；后续 C、D 启动失败也保留，未标成功。

主控原始证据目录：C:/Users/Administrator/.codex/worktrees/750c/Life OS/work/lads-stage1-20261102-181417/。其中 integration-result.json 记录14文件备份位置；integrated-unit-tests.json 记录当前工作区70项实际测试；frozen-preflights-integrated.json/candidate.json 是实际权限探针；latest-live-result.json 与 live-*.jsonl 记录失败流程。阶段报告在相应 demo/source/work/lads/runs/<task-id>/ 内，包含安全报告、真实命令状态、权限证明和哈希。D 盘审查/构建证据保存在 D:/CODEX/LADS-Stage1-20261102-181417/work/lads-bootstrap/，不读取或上传其内部 runtime-homes 缓存、会话或 sandbox-secrets。

一次补充备份清单发现原 JSON 为 null，六份物理副本已独立验证与先前批准哈希一致，原清单保留并另建 retrospective supplemental-verification.json，不能冒充修改前清单。被超时中断的合成测试临时目录仅搬入 ignored work/ 保留，未删除用户文件。最新状态文档由 GPT 主控因系统级角色执行阻断直接追加，仅为事实记录；没有伪称独立最终 Reviewer 已通过。

修复方案待用户决定：先只处理 CodexSandboxOffline 执行账户锁定和启动兼容性，保持 API 密钥、主控登录、业务数据、权限策略不变；然后评估复用单一受支持的 Windows sandbox 状态根与独立 Provider profile，或其他经过验证的隔离方式。不得继续盲建每次独立 HOME 并反复登录。本文不声称已确定密码不一致根因，也未实施未批准的执行区迁移方案。

## 2026-11-03 Windows shared original-HOME repair completion

- The current continuation keeps the approved shared original `CODEX_HOME`
  design and adds raw project-key identity hashing to the main-config guard.
  Only exact verified source/common additions containing exactly
  `trust_level = "trusted"` are accepted; old-key spelling rewrites, removals,
  non-project changes, unknown run roots, aliases/case collisions, and extra
  trust fields are rejected without serializing config contents.
- Focused synthetic Windows coverage passed 5/5. The subsequent actual
  `python -B -m unittest discover -s tests/lads -v` command passed 75/75 with
  zero failures/skips in 107.706 seconds, preserving the original 70 methods.
- Independent Scanner/Reviewer review and the real eight-call demo remain
  pending. No API/Codex subprocess, account/auth/config change, dependency,
  commit, push, deployment, source copyback, or real-data operation occurred.

## 2026-11-03 QA candidate digest prompt staging

Status: `STAGED CANDIDATE ONLY / FULL SUITE PENDING`; this is not installed or
live acceptance. Approvals `9cfeb4c0`, `8f370ab6`, and `a61be59f` are
`APPROVED`. The five formal source/document changes plus the protected prompt
baseline were backed up under
`work/lads-stage1-20261102-181417/backups/qa-candidate-staged-20261103-022754/`
with a parseable SHA-256 manifest before edits.

The QA prompt now supplies exactly one current candidate digest as
`source_snapshot_sha256 REQUIRED VALUE` and no source aggregate line; Source
HEAD remains. `validate_report` is AST-identical to baseline. The existing real
subprocess failure/rework workflow test captures both QA prompts; its final
targeted run
`python -B -m unittest discover -s tests/lads -p test_orchestrator.py -k qa_fail_is_reviewed_then_reworked_once -v`
exited 0 with 1 test. The first run exited 1 because its new assertion required
the unchanged negative candidate to differ from source; that test-only error is
retained here and was corrected before the passing rerun.

Only `work/lads-stage1-20261102-181417/qa-tester-candidate.md` contains the
desired role prompt. Actual `.codex/worker-prompts/qa-tester.md` remains
`7c4b849ba1f7b40951c24f15aaae1cab6f63f0cfd47890a2c9e2b99526ceb548`.
Historical Task `02348c59...` and the protected-write block in
`qa-prompt-build-result.json` remain `BLOCKED`; no protected write or
permission change was retried. The full 75-method suite, installed-prompt
role-contract assertion, independent review, main-host installation, and fresh
eight-call live run are `PENDING`. No API/Codex child, source copyback, config,
key, account, data, dependency, Git, commit, push, deployment, or integration
action occurred.

## 2026-11-03 Installed QA prompt verification

Sole-Builder task `b79dd451-1e48-4e51-a304-3b0c3c8ee862` verified Reviewer
`APPROVED` for exact candidate
`06259d00ba949c22e44bcbbaca55d57fe018f6033535c1d7df2fee38ac400676` and the
successful `qa-prompt-config-install.json` host installation. Actual
`.codex/worker-prompts/qa-tester.md` is byte-identical to
`work/lads-stage1-20261102-181417/qa-tester-candidate.md`; the fixed
`qa-tester` role, one Coordinator-provided candidate digest instruction,
six-field JSON contract, profile role, and schema fields match the reviewed
candidate.

The Builder made fresh pre-append copies and a parseable manifest of the three
documentation files at
`work/lads-stage1-20261102-181417/backups/pre-installed-qa-finish-20261103-024712388/`.
It read but did not write the protected QA prompt. The actual full command
`python -B -m unittest discover -s tests/lads -v` exited `0` with
`Ran 75 tests in 110.731s`, `OK`, and zero failures/skips; AST inspection found
exactly 75 test methods. This is the current installed-file run, not the
historical 107.706-second run.

Compilation of the core and test module exited `0`, and `git diff --check`
exited `0`. The 139-item baseline retains 132 byte-identical items plus seven
expected approved changes, with no other eight-file scope drift. Historical
Task `02348c59...`, the protected-write failure, and the first targeted
assertion failure remain preserved. Fresh eight-model demo acceptance remains
`PENDING`; no API/Codex child, business/data/config/key/account, schema, route,
profile, permission, dependency, Git, commit, push, deployment, integration,
or real-user-data action was performed.


## 2026-11-03 LADS Stage 1 合成主验收报告

状态 `MAIN_ACCEPTED_SYNTHETIC_FLOW`。任务 `9ea064c1-ab38-4dd6-b4b5-95f36a08b6fb`：SCAN_COMPLETE → APPROVED → BUILT → QA FAIL → CHANGES_REQUESTED → BUILT → QA PASS → APPROVED，随后 GPT 主控验收；两次构建，QA 退出码 1→0。Builder 隔离 worktree，QA 无 `.git`，源码不回拷，approved 14 哈希保持一致。

GPT/OpenAI `gpt-6-astra` 保持主控；四角色独立 DeepSeek CLI 均用 `deepseek-flash`；QA TOML 仅角色元数据，非原生跨 provider 调用。Windows 共享原 `CODEX_HOME`，非 Windows frozen 分支仅合成测试。三角色 canary 读通、写拒、无文件。Lockout 自动清除，未改密码、账户、策略、ACL，未削弱沙箱；Win10 MXC 探针不受支持。主配置守卫仅允许已核验 source/common trusted 新增，raw SHA 前后/added roots 如实记录；模型/provider/非项目/旧项目项不变。

安装后 75/75、110.731 秒，保留原 75 方法、早期 70+5 Windows 用例及 QA 子进程提示回归。Token 仅记成功 demo 返回用量，不推断账单或历史成本。初始 14 文件：`scripts/lads_orchestrator.py`、`scripts/lifeos-deepseek.py`、`.codex/agents/qa-tester.toml`、`.codex/worker-prompts/qa-tester.md`、`schemas/lads-task-report.schema.json`、`tests/lads/test_orchestrator.py`、`docs/LADS_ORCHESTRATOR.md`、`docs/LADS-STAGE1-REPORT.md`、`AGENTS.md`、`docs/STATUS.md`、`docs/ARCHITECTURE.md`、`docs/DEVELOPMENT_PROGRESS.md`、`docs/CODEX_WORK_ENVIRONMENT.md`、`docs/CODEX_TECH_STACK_AND_TOOLING.md`；因编排、路由/提示、schema、测试、shared-HOME 加固。

证据在 `work/lads-stage1-20261102-181417`；备份为 `backups/lads-stage1-integration-20261103-003556`、`backups/lads-qa-prompt-install-20261103-024605`、`work/lads-stage1-20261102-181417/backups/pre-installed-qa-finish-20261103-024712388`。`02348c59` 仍 BLOCKED，受保护 QA markdown 写失败已回退，最终候选 `06259d00...` 经独立批准并精确安装；网络/SSE 故障、一次只读重试、旧 run-config drift/Win32 错误均保留，不追认失败运行。

受保护配置采用候选、精确 SHA 审查、授权主控备份安装、实际测试流程，worker 隔离不降级；文档候选同规。边界：仅 Python-B stdout 测试适配器；浏览器、PWA、移动端、多用户适配器未实现测试。这是合成流程验收，不是业务全回归或生产就绪；真实浏览器数据/JSON 恢复未跑，业务 132 文件、数据、存储键、备份格式未变；用户脏工作区保留，无 Life OS 提交、推送、部署。下一阶段为单独授权的浏览器/多用户适配器。
