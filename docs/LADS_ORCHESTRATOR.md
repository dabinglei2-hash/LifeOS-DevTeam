# LADS 独立工作进程编排器

更新：2026-11-02。适用范围：Stage 1 工程验证。状态口径：本地实现与确定性单元测试可核验；真实 DeepSeek 四角色 live 流程、QA 超时后的完整主流程和浏览器/多用户能力仍为 `LIVE PENDING` 或明确不支持。

## 1. 边界

`scripts/lads_orchestrator.py` 是标准库 Python 3.11+ 协调器，只调度独立 CLI 工作进程，不启动原生子 Agent，不读取 rollout/私人会话，不自动回退 GPT，不把候选写回调用方 checkout，也不提交、推送、部署、安装依赖或操作真实用户数据。

角色路由唯一来源是用户级 profile 与 `scripts/lifeos-deepseek.py`：

| 角色 | profile | provider/API | model | 专用环境变量 | 权限 |
| --- | --- | --- | --- | --- | --- |
| Scanner | `scanner-deepseek` | `lifeos_deepseek` / `https://api.deepseek.com` | `deepseek-flash` | `DEEPSEEK_SCANNER_API_KEY` | read-only / never |
| Reviewer | `reviewer-deepseek` | 同上 | `deepseek-flash` | `DEEPSEEK_REVIEWER_API_KEY` | read-only / never |
| Builder | `builder-deepseek` | 同上 | `deepseek-flash` | `DEEPSEEK_BUILDER_API_KEY` | workspace-write / never |
| QA | `qa-tester-deepseek` | 同上 | `deepseek-flash` | `DEEPSEEK_QA_API_KEY` | read-only / never |

`.codex/agents/qa-tester.toml` 与 `.codex/worker-prompts/qa-tester.md` 已由主控按审查哈希安装；TOML 只描述角色，不自行证明权限。实际 argv 由 helper 的 `command()` 构建，并显式覆盖 provider、model、sandbox、approval、web、apps、plugins、MCP、`sandbox_workspace_write.writable_roots=[]`、`network_access=false`、`exclude_tmpdir_env_var=true` 和 `exclude_slash_tmp=true`。子进程环境是严格 allowlist：只允许 PATH/系统运行目录/home/临时目录、`CODEX_HOME`、TLS/proxy 变量及当前角色唯一专用密钥；不传入其他角色密钥、数据库、SSH、云凭据或普通无关变量。

## 2. 运行流

状态只允许：

`CREATED -> SCANNING -> PLAN_REVIEW -> BUILDING -> QA -> FINAL_REVIEW -> AWAITING_MAIN_ACCEPTANCE`

任一阻断条件可将任务落到 `BLOCKED`。初版加最多 3 次返工，共最多 4 次 Builder；QA FAIL、Reviewer `CHANGES_REQUESTED` 回到 Builder，final Reviewer `BLOCKED` 立即停止。只读角色失败最多做 1 次仅限瞬时错误的透明重试；Builder 不盲重试，认证、格式、角色、任务 ID、证据或权限错误不重试。

每次任务使用 UUID，绑定调用方 HEAD、非忽略源码基线与 manifest SHA-256。扫描快照包含 tracked 与 nonignored untracked 源码，不包含 ignored 用户数据和 `.git`。Builder 在独立 Git worktree 的 HEAD 上覆盖该精确内容快照；Builder 停止后复制全部候选字节，排除 `.git`，再制作 QA 副本。状态、日志、候选和 QA 快照均位于调用方仓库 ignored 的 `work/lads/` 下。

Scope 检测在 Builder 停止后遍历完整候选，包括新生成的 ignored 文件，仅排除 `.git`；任何白名单外新增、删除或修改均 `BLOCKED`。这是事后验收门禁，不是逐文件 OS ACL。Builder 不得改变 worktree HEAD；原 checkout 在最终批准前必须保持相同 HEAD 和状态基线。清理接口只接收已核验的 UUID runroot，并从其派生 `builder`；递归删除前检查 runroot、父链和全部子项不是 symlink/junction/reparse，状态、事件和 stage 报告路径也使用相同 containment 检查。`work/lads/runs/` 外的路径及任一 junction 反例均 `BLOCKED`。

QA 快照不含 `.git`，以 read-only/never 和 `--skip-git-repo-check` 启动。QA 只能执行 manifest 规定的 stdout-only `python -B` 命令，快照运行前后 SHA 必须一致，报告必须绑定候选 source snapshot SHA-256。PASS 必须同时具备实际 command_execution 事件、completed 状态和退出码 0；仅有文字声明、`echo`、额外命令或矛盾退出码均阻断。

## 3. Manifest

调用方提供绝对仓库路径与精确文件白名单。JSON 必须恰好包含：

```json
{
  "repo": "D:\\CODEX\\Life OS",
  "task": "冻结后的具体工程任务",
  "allowed_paths": ["example.py"],
  "test_commands": ["python -B -m unittest discover -s tests -v"],
  "applicability": {"browser": false, "multiuser": false}
}
```

`allowed_paths` 使用相对 POSIX 路径，不接受绝对路径、`.`/`..`、工作区逃逸、大小写碰撞、symlink/reparse point、`.git`、凭据类文件名或工程/策略保护路径。`test_commands` 至少一条且不得重复，不接受 shell 拼接、重定向、管道、内联 `python -c` 或非 `python -B` 命令。当前 CLI 无法验收浏览器和多用户断言，因此两项必须为 `false`；为 `true` 会显式 `BLOCKED`，不会伪造 PASS。

## 4. 严格报告

最后一条 assistant 消息必须是纯 JSON，带准确 task UUID、角色和角色专属状态。通用字段为 `task_id`、`role`、`status`、`summary`、`evidence`；QA 必须额外携带 `source_snapshot_sha256`，其他角色不得携带该字段。字段、状态、长度、角色绑定或 secret-like 文本不符即 `BLOCKED`。

进程必须正常退出并恰好产生一次 `turn.completed`，且它必须是事件流绝对最后一个事件；其后的 message、tool、error 或任意事件均阻断。`turn.failed`、非 JSON stdout、原生 child/spawn 证据或输出超限同样阻断。协调器读取 usage 生成规范化元数据，token 未返回时保存 `null`，不猜测。各角色 `developer_instructions` 末尾追加统一协议：最后一条 assistant 消息只能是准确 task/role/status 的纯 JSON，不添加 metadata；父编排器拥有规范化元数据，严格 parser 不放宽。

报告 schema 位于 `schemas/lads-task-report.schema.json`。每次角色调用在 `stages/` 生成独立原子 JSON，记录角色、状态、build/attempt、脱敏命令、退出码、bounded output tail、规范化 metadata、`output_sha256`、起止时间和 SHA/错误原因；不保存 raw prompt 或完整 transcript。QA `tests` 跨 build 追加而非覆盖。最终 `finalhandoff.json` 引用每个 stage 文件的相对路径和 SHA-256，并记录 task/head/baseline/manifest/allowed paths、实际变更、Diff、全部测试命令、未测项、角色运行元数据、限制和批准绑定；批准结果只表示 `AWAITING_MAIN_ACCEPTANCE`。

## 5. 运行与离线检查

主控在独立 worktree、Reviewer 已审 Diff 且专用 profile/API/权限均已单独验证后运行。每个 worker 启动前会使用与正式 worker 相同的可执行文件、cwd、profile、角色环境和完整 `-c` 前缀运行 `codex debug prompt-input`，只解析恰好一个 `<permissions instructions>` 块；read-only 必须无 writable root，Builder 的唯一有效 writable root 必须经路径解析/大小写归一后等于当前 workspace，并同时证明 network restricted、approval never。缺失、重复、歧义或更宽权限均 `BLOCKED`，无 raw debug context 日志。

```powershell
python -B scripts/lads_orchestrator.py --task-file .\approved-task.json --timeout 900
```

一次性合成 negative drill：

```powershell
python -B scripts/lads_orchestrator.py --demo --timeout 900
```

Demo 在 ignored `work/lads/demos/` 内创建新的临时 Git 仓库和不变量测试。首个候选被透明指定为 `a+b+1`，预期 QA 实际 FAIL、Reviewer `CHANGES_REQUESTED`，第二次修复为 `a+b` 后预期 QA PASS、final Reviewer APPROVED。该负向步骤只属于显式 `--demo`，不会注入普通任务。

Builder 沙箱不运行 live demo、API 或嵌套 Codex。主控可用以下命令做不调用模型的 profile/argv 检查，前提是用户级 profile 已存在：

```powershell
python -B scripts/lifeos-deepseek.py qa-tester --check
```

## Effective preflight and audit evidence repair

Every successful worker preflight now records a bounded `preflight_proof` in
`WorkerResult.metadata` and therefore in role metadata, stage reports, and the
final handoff. The proof contains the sanitized same-prefix `debug prompt-input`
argv, worker-prefix hash, exit code, stdout SHA-256, the actually validated
sandbox/network/approval policies, exact writable roots, and configuration-file
hashes. Raw prompt context is not stored. A live `RealWorker` result without a
valid proof fails closed; fake-worker tests must provide an explicit synthetic
proof.

Failure stage JSON now persists `command`, `exit_code`, `output_tail`, and
`output_sha256` from `Blocked` and transient worker errors on preflight, timeout,
nonzero-exit, bounded-output, launch, and malformed-report paths. Command and
output evidence is sanitized and bounded, while a real nonzero process exit code
is retained.

QA command events are retained raw only for the in-memory report/runtime
validation. Before persistence or prompt construction, typed QA commands and
report/control-plane fields are canonicalized to the exact prescribed Python `-B`
commands and redacted. State test evidence, stage JSON, final handoff, final
Reviewer prompts, approval data, and rework feedback therefore contain only those
canonicalized/redacted typed and control-plane fields. Secret-like test commands
are rejected while loading the manifest, before they can be echoed into any
worker prompt.

## Combined compatibility close (2026-11-02)

The QA terminal protocol now follows the observed CLI event shape. A finished
`item.completed` command with item type `command_execution` is accepted only
when its status is `completed` or `failed` and `exit_code` is an actual
non-boolean integer. `PASS` requires every prescribed command to be
`completed/0`. `FAIL` requires every prescribed command to have a terminal
status and valid exit code, with at least one non-zero result across the full
suite. `running`, null, `failed/0`, unknown, duplicate, missing, and extra
commands are rejected. The typed `failed` status remains intact; the parser does
not relabel it as `completed`. Global `turn.failed` and `error` events remain
blocking.

`scripts/lifeos-deepseek.py` now derives project trust only from verifiable Git
linkage. The helper validates the exact absolute existing non-reparse cwd, the
Git top-level marker, absolute gitdir, common directory, linked-worktree
backlink, and the common directory's parent repository equivalence. User home,
drive roots, wildcards, non-Git roots, and reparse paths are rejected for every
role, with no non-Git fallback. QA snapshots inside the generating repository's
ignored runroot remain valid through ancestor Git linkage.

Each validated root receives an ephemeral command-local override of the form
`-c projects."<normalized absolute path>".trust_level="trusted"`. The same
helper-produced prefix is used by `debug prompt-input` and the worker. No
project trust is written persistently, and the Codex home/config is not
rewritten. The existing full config/profile/prompt/helper/catalog/executable
snapshot guard, provider/model route, sandbox, network, approval, environment,
and native-child guards remain in force.

`RealWorker` checks the complete snapshot before preflight and on success,
failure, launch, timeout, nonzero-exit, malformed-output, and preflight failure
paths. A snapshot change is reported as a `BLOCKED` configuration-drift cause
while retaining the original safe process evidence. If drift is already present
before preflight, no worker process is started. At orchestration level, the raw
SHA of the main `config.toml` is captured at run start and checked after each
role and immediately before acceptance; a changed run-level baseline cannot be
silently refreshed.

Builder prompts receive only the validated Scanner report, approved plan
report, source HEAD/digest, build number, exact allowlist, and immutable tests.
QA prompts receive only the validated Builder report, source/candidate digests,
changed-file list, exact allowlist, a bounded redacted diff, and the immutable
test commands. Process tails, raw transcripts, raw config, environment values,
and credentials are not copied into role prompts. The synthetic demo task
explicitly requires PowerShell or `pwsh` on Windows, one prescribed Python `-B`
command, and the exact three-file synthetic source; parent traversal and
`.codex`/user/private data access are forbidden.

The preserved real failure history remains unchanged: task
`9bb69d0f-37ad-44c6-9457-b4a426dc7fde` is `BLOCKED` with `builds=0`, and task
`05059ec5-5e91-47d0-b1cf-709c920635af` is `BLOCKED` with `builds=1`. Local
unit/protocol verification does not prove a fresh live four-role run,
DeepSeek route provenance, Windows host enforcement, or unchanged real main
config; those remain `LIVE PENDING` for the host and subsequent Scanner/Reviewer
checks.

Stage `output_tail` is a separate bounded diagnostic capture: it intentionally
may include up to 4000 generic-redacted stderr/stdout characters, including
command wrappers. Redaction is pattern/current-environment-key based, not a
universal private-content filter. A full raw transcript is not saved. The
existing `not-a-real` `FakeWorker` sentinel test had an EMPTY `output_tail`; it
proves canonicalization/redaction of typed and control-plane fields only, not
the absence of command wrappers from an actual captured tail.

These checks and tests are local engineering evidence only. Actual four-role CLI
preflights, DeepSeek route provenance, and the live synthetic demo remain
`LIVE PENDING` until the host executes them.

## V2 stage evidence and phase contract (2026-11-02 follow-up)

Every successful role call now writes the exact sanitized result object as the
stage-level `report` field. `report_sha256` remains the existing field name and
is the SHA-256 of canonical JSON for that same persisted `report`; it is not a
hash of a raw model message. Raw worker output is not retained or transported.
Failed stages keep `report` and `report_sha256` as `null` when no validated
report exists. The worker report schema and final-handoff `stage_reports`
entries remain unchanged: each reference still contains only its relative
`path` and stage-file `sha256`. Historical stage files without `report` are left
unchanged and are not reconstructed.

Stage `output_tail` is now the true final 4000 characters of the combined
stderr/stdout text after the complete existing `redact()` pipeline, including
its `MAX_CAPTURE` and normal redaction bounds. The order is redact first, tail
second, so a secret crossing the former tail boundary is not exposed by
pre-redaction slicing. No marker is appended after this tail operation, and the
stored value is always at most 4000 Unicode characters. Redaction remains
pattern/current-environment-key based rather than a universal private-content
filter, and no raw transcript is saved.

Role prompts carry explicit phases: `PHASE=SCANNING`, `PHASE=PLAN_REVIEW`,
`PHASE=BUILDING`, `PHASE=QA`, and `PHASE=FINAL_REVIEW`. The plan prompt binds
the source HEAD and snapshot digest, proposed task, exact allowlist, immutable
prescribed test commands, required QA/final flow, and the fact that the current
baseline is unfinished rather than completed work. A plan result other than
`APPROVED` persists its safe report and blocks before Builder, leaving
`builds=0`. In `--demo` only, plan review knows that the first build is the
transparent expected-negative drill; final review receives no demo exemption.
A QA `FAIL` requires final Reviewer `CHANGES_REQUESTED`; QA `PASS` acceptance
still requires strict final Reviewer `APPROVED`.

`lifeos-deepseek.py` 的普通启动仍保持 gate；协调器通过其 `command()` 构建 argv 后先执行上述实际权限探针，再直接启动独立 `codex exec --json --ephemeral`，不依赖 helper 子命令代跑任务。Builder 沙箱不执行 live Codex/API；实际 CLI/profile 的探针与完整 live 仍由主控在独立环境执行。

## 6. 已知限制

- Scope 为停止后全树哈希门禁；不声称 per-file OS ACL。
- Windows 超时使用标准库进程枚举终止后代；正常结束后也清理遗留后代，但仍不能代替生产级作业系统和主机审计。
- 状态 JSON 使用临时文件加 `os.replace`，不是数据库事务；锁与持久事件不能证明跨主机互斥。
- Windows reparse/junction 使用检查与删除之间存在固有 TOCTOU 窗口；当前实现会在递归删除前拒绝已观察到的父链或子项 reparse，但不能替代 OS ACL、受控卷或生产级沙箱。
- 不读取或操作浏览器 localStorage/IndexedDB、真实 JSON、个人备份、私人数据库或凭据内容。
- 不提供浏览器、移动端、实体设备、多用户隔离或生产安全认证验收。
- 候选永不自动整合；是否采用发现、备份、Diff、测试和风险由 GPT 主控与用户另行决定。
## Frozen per-role CODEX_HOME adapter (2026-11-03)

Status: `ADAPTER ENGINEERING TESTED / SCANNER-REVIEWER PENDING`; full fresh
four-role/eight-call live operation remains `LIVE PENDING`.

The live `RealWorker` is now bound once to the coordinator's validated UUID
`run_root`. Each role attempt asks the helper to create a new UUID directory
under that run root's `runtime-homes/` container. There is no caller-supplied
home argument in the live worker path and no manifest field for a home. The
directory must be absent, resolve without aliases, remain inside the validated
run root, and have a non-reparse parent chain. A managed runtime home may be a
new subtree of an explicitly approved Git worktree even when that worktree is
inside the user's home; it may not equal the user home, equal the original
`CODEX_HOME`, or alias existing config/auth/session paths.

The helper treats the four user profiles and their catalogs as static route
authority only. It validates and hashes every profile/catalog, but copies only
the selected catalog bytes into the fresh home. It generates `config.toml`, the
selected `<role>-deepseek.config.toml`, and `models.json`. The generated TOML
uses `model="deepseek-flash"`, `model_provider="lifeos_deepseek"`, the exact
DeepSeek responses endpoint and role key reference, read-only for
Scanner/Reviewer/QA or workspace-write for Builder,
`approval_policy="never"`, empty `notify`, the prototype-proven
`windows.sandbox="elevated"`, and disabled web/apps/plugins/native agents.
Runtime TOML contains no hooks or MCP sections.

Validated Git project roots are written as real TOML
`[projects."<normalized path>"] trust_level="trusted"` tables in both generated
base/profile files. Project `.codex/config.toml` hooks are rejected; discovered
MCP names receive command-local disabled overrides. Runtime generation does not
copy the original `config.toml`, `auth.json`, sessions, environment values, or
arbitrary cache data.

The helper `check()` and `command()` accept an explicit home for offline
checks, and the CLI exposes `--codex-home` for that purpose. They never mutate
`os.environ`. The worker child environment is a copy containing the generated
`CODEX_HOME`, the current role's one dedicated key, and the existing narrow
runtime/TLS/proxy allowlist. The same environment object and argv prefix are
used by `debug prompt-input` and `exec`.

Before the first worker process, bind pins all four original profile/catalog
authorities. Generated config/profile/catalog, any project config, role prompt,
helper, and executable are snapshot-pinned. Successful preflight proof records
the exact runtime home, a canonical home hash, the authority hash, runtime
config hashes, actual read-only or exact Builder writable-root permission,
restrictive network policy, and `never`. The guard checks runtime and original
authority drift before preflight and on successful, launch, timeout, nonzero,
malformed, and preflight-failure paths. The orchestrator's original main-config
raw-SHA baseline remains a separate run-level guard.

The prior host prototype is limited evidence for one disposable Builder call:
`work/lads-bootstrap/frozen-home-prototype-result.json` records a successful
`probe.txt` write canary, unchanged main config, unchanged runtime hashes, and
no `auth.json`. It is not full live acceptance. No Codex/API child was called by
this Builder implementation run; fresh Scanner/Reviewer/Builder/QA/Reviewer and
retry/rework-permission coverage for all eight calls remains pending.

Finalization note (2026-11-03): the earlier 900-second Builder process was
interrupted and timed out after a pre-final-patch 69/69 run, so that history is
incomplete rather than a successful delivery. After the post-preflight guard
and its focused tests were rechecked, the current candidate passed the single
final `python -B -m unittest discover -s tests/lads -v` run with 70/70 tests,
no failures, and no skips in 93.812 seconds. Independent Scanner/Reviewer
review and the fresh host eight-call live acceptance remain pending.


<!-- LADS1_ACTUAL_BLOCKED_20261103 -->
## 2026-11-03 当前门禁：停止live

当前版本本地70/70通过，但未通过真实八调用验收。C、D工作区均出现Windows进程启动失败；只读CIM核实CodexSandboxOffline Lockout=True。上方live命令目前仅为接口说明，不是已可用承诺。停止Worker/登录重试，不改unrestricted、不换GPT、不复制sandbox凭据。单Builder冻结HOME原型成功不代表多角色Windows执行账户/凭据缓存兼容；修复环境需单独用户授权并重新审查该设计，随后重跑只读canary及完整流程。见[实际报告](LADS-STAGE1-REPORT.md)。

## 2026-11-03 Windows shared original-HOME guard continuation

- Windows uses one shared original `CODEX_HOME`; no per-attempt or per-role
  runtime HOME is created. All four routes retain the fixed sandbox,
  environment allowlist, and main/project configuration exclusion checks.
- Main-config snapshots now hash each normalized project key's raw spelling
  identity. A transition may only add the exact verified source/common roots
  with the sole field `trust_level = "trusted"`; old-key rewrites, removals,
  non-project changes, unknown roots, alias/case collisions, and extra fields
  fail closed. Snapshots/events serialize hashes, not config contents.
- Verification: focused synthetic Windows tests passed 5/5, followed by the
  actual `python -B -m unittest discover -s tests/lads -v` run: 75/75 passed,
  zero failures/skips in 107.706 seconds. This is not eight-role live
  acceptance, which remains pending with independent Scanner/Reviewer review.

## 2026-11-03 QA candidate digest prompt staging

- APPROVED plan `9cfeb4c0`, supplement `8f370ab6`, and protected-install plan
  `a61be59f` authorize a staged candidate only. The QA context now emits one
  `source_snapshot_sha256 REQUIRED VALUE: <candidate_hash>` line and requires
  verbatim return; the source aggregate digest line is removed while Source HEAD
  remains. Validator and sanitizer behavior are unchanged.
- The existing real-subprocess negative/rework workflow test now captures both
  QA prompts and digests. It verifies one required field per prompt, the second
  candidate differs from the source aggregate, and old-digest or extra-command
  returns remain `BLOCKED`. The targeted command passed 1/1 after an initial
  test-only assertion failure was corrected to the approved second-candidate
  condition; the original 75-method count remains.
- Candidate prompt:
  `work/lads-stage1-20261102-181417/qa-tester-candidate.md` (ignored work
  file). Backup:
  `work/lads-stage1-20261102-181417/backups/qa-candidate-staged-20261103-022754/`.
  Actual `.codex/worker-prompts/qa-tester.md` remains unchanged at
  `7c4b849b...`; the earlier protected-write block and Task `02348c59...` remain
  historical `BLOCKED` records.
- Independent Scanner/Reviewer review of the exact staged candidate, main-host
  installation, the current full suite, the installed role-contract assertion,
  and fresh eight-call live acceptance remain `PENDING`.

## 2026-11-03 Installed QA prompt verification

- Final review `APPROVED` the exact QA prompt at
  `06259d00ba949c22e44bcbbaca55d57fe018f6033535c1d7df2fee38ac400676`;
  `qa-prompt-config-install.json` records a successful host installation with
  the same `installed_sha256`. The actual QA markdown now equals that candidate.
- The installed role contract retains `qa-tester`, exactly one
  `source_snapshot_sha256 REQUIRED VALUE` supplied by the Coordinator, and
  exactly the six required final JSON fields. The profile role and schema still
  match the fixed QA route; none of those protected definitions was changed by
  this Builder verification.
- Against the actual installed file, the full
  `python -B -m unittest discover -s tests/lads -v` command exited `0` with
  `Ran 75 tests in 110.731s`, `OK`, and zero failures/skips. The suite retains
  its original 75 methods and regressions. Compilation and `git diff --check`
  also exited `0`.
- The 139-item captured baseline has 132 expected no-change files plus seven
  approved changes and no other eight-file scope drift. Fresh eight-model live
  acceptance remains `PENDING`; all prior pending, `BLOCKED`, protected-write,
  and first-target-assertion records remain historical evidence. No API call,
  source integration, protected write, permission change, or Git mutation was
  performed.


## 2026-11-03 合成主验收与受保护配置候选流

入口：`python -B scripts/lads_orchestrator.py --demo` 或 `--task-file <approved-manifest>`。进程须四角色密钥变量；host driver 安全读取同一 CLI，不打印密钥；`helper --check` 仅离线。最多 4 构建，只读重试 1，不盲重试 Builder、不 GPT 回退。受保护文件：Builder 写 ignored 候选，Scanner/Reviewer 批精确 SHA，主控授权备份、原字节安装、实跑测试；worker 隔离不变。仅 Python-B stdout 测试。


## 2026-11-05 工程执行修复（主控明确例外）

活跃编排器现通过 `scripts/lads_runtime.py` 执行受控子进程、保存真正末尾的诊断。Windows 在 resume 前分配 kill-on-close Job；环境固定 UTF-8/禁隐式 pyc。父级按 build 在 Builder 工作树外提供哈希备份，返回后核验，禁止 Builder 自建备份/缓存并保留严格范围门禁。

新任务 `counter_schema_version=2` 分开记录派发、连接、认证、报告、执行、范围、QA、Review 和 `code_reworks`；旧 builds/记录不改写。只读网络重试有界；Builder 失败不盲重试；代码返工只受有效 QA/Review 结论触发，仍最多三轮。报告继续严格验证 schema、最终 JSON、真实命令/退出状态，不把解析修复当作成功。

本轮主控本地测试89 LADS、171 automation、38 knowledge通过，不是独立 Agent 审批；独立实测与发布状态见 [工程修复验收](LADS_ENGINEERING_REPAIR_20261105.md)。历史安装图只保留，不适用于新活跃源码，生产校验仍拒绝旧哈希。

补验：真实合成任务 `26ebde50-faee-42c9-8a3d-5796d4c10514` 八次独立 DeepSeek 调用通过，QA exit 1 → Reviewer 要求修改 → Builder 修复 → QA exit 0 → Reviewer APPROVED。主控核验阶段哈希、同参数权限探针、工作区与两份外置备份；未将合成验收扩展为全部工程改动的独立批准。首个预检失败任务保留；已修复 `-c` 环境 map 的 TOML 序列化，并保留预检 stderr，不再丢失配置错误。正式 Git 操作和自动发布仍未启用。
