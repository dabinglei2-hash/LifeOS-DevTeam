# LADS 工程修复与本地验收（2026-11-05）

## 授权与当前结论

本轮用户明确允许 GPT/Codex 主控直接修复工程层，作为强制 DeepSeek 写入与既定返工上限的一次例外。**主控本地 298 项测试通过；真实 DeepSeek 四角色合成任务通过并经主控证据核验；自动发布未启用。** 独立 QA/Reviewer 验收的是合成任务，不代表已独立审查本轮全部工程增量；没有生成虚假审批或模型使用记录。

正式 HEAD 保持 `aea9e758baac88c310319f34a77f94f2364b0c11`。本轮不提交、推送、合并或部署；Git 行为测试只操作临时合成仓库，不触及正式历史或 GitHub。

## 范围与备份

- 逐文件备份与原始清单：`C:\Users\Administrator\.codex\worktrees\750c\Life OS\backups\lads-engineering-repair-20261105-4d0928ce`。
- 本轮基线与命令回执：`C:\Users\Administrator\.codex\worktrees\750c\Life OS\work\lads-engineering-repair-20261105-4d0928ce`。
- 活跃执行代码：`scripts/lads_orchestrator.py`、`scripts/lads_runtime.py`、`scripts/lifeos-deepseek.py`、`lads_automation/team.py` 与 Builder 指令。
- 阶段 4 草稿导入活跃工程目录后修复：发布控制器、CLI、三份工作流及测试；均仍未提交或启用。
- 历史 `engineering/lads-protocol-fix/`、`engineering/lads-stage3-install/`、旧任务目录、原计数及失败副产物保留。安装测试用新鲜的内存绑定验证当前代码，生产验证器仍拒绝陈旧哈希；没有重写历史安装图。
- Life OS 产品源码、存储键、JSON v11、财务/健康/学习记录、浏览器存储及用户密钥没有修改。

## 已修复

1. 主控在 `run_root/backups/build-N/` 为允许路径生成修改前副本和 SHA 回执，明确记录原本不存在的文件；该目录位于 Builder 可写工作树外。分派前准备、返回后核验；Builder 不得新建备份、缓存或额外辅助文件。白名单保持严格，越界副产物保留并阻断。
2. 完整捕获先脱敏再取尾，stdout/stderr 分开保留；结构化 `error`/`turn.failed` 保存最后二十条，嵌套错误信息也会提取。超过捕获限额时保留真正末尾并标记 bounded，不能把缺失前文当作完整成功证据。旧的丢失终端信息无法追溯，不猜测旧退出码1的根因。
3. 新任务增加 `counter_schema_version=2`：dispatch、连接、认证、报告、执行、范围、QA、Reviewer、代码返工分别计数。`builds` 保留兼容含义，旧状态不迁移、不重置。代码返工只在有效 QA/Review 要求修改后增加；Builder 连接/格式错误保持 BLOCKED，不盲重试；只读连接重试另记且有上限。正常最多四次构建/三次代码返工不变。
4. Windows 在子进程初始线程运行前分配受控 kill-on-close Job，覆盖 venv 启动器退出后的后代；失败关闭，不降级为不受控执行。真实 PowerShell/中文输出与正常退出/超时清理经过合成进程测试。进程与 shell 统一 UTF-8、隐式 Python 缓存禁用；显式 py_compile/compileall 仍被明确禁止且越界检查不豁免 pyc。结构化报告继续使用 CLI output-schema，重复键、非有限数、非 JSON 最终回复拒绝；未做宽松修复或伪造成功。
5. 阶段4：真实 publisher receipt 的 B→C 追加可与候选 A→B 共存；原始候选证明不变；逐文件 Git add 只在该次命令关闭 autocrlf、严格验证 blob 字节；提交不再覆盖 hooksPath 或使用 no-verify，活动 hook 在任何私有工作树创建前阻断；合成样本豁免同时绑定精确路径与原始 SHA，控制器和真实提取的 security 脚本均验证异路径、变字节会拒绝。

Shell 路径由当前环境解析并明确写入 worker 执行约定；并不宣称提示词是系统级 Shell 白名单。只读/写权限仍必须以每次实际 Codex 权限探针和工作区完整性为依据。

CLI 格式依据：[OpenAI 非交互模式官方文档](https://learn.chatgpt.com/docs/non-interactive-mode)，同时核对了本机 exec --help；主控模型与登录未切换。

## 本轮实际测试（主控，不是独立 QA）

| 测试 | 结果 | 证据 |
| --- | --- | --- |
| LADS 完整套件 | 89 通过 | `lads-final.txt/json` |
| 自动化完整套件 | 171 通过 | `automation-final.txt/json` |
| 工程知识库套件 | 38 通过 | `knowledge-final.txt/json` |
| 阶段4聚焦测试 | 53 通过，包含于171 | `release-focused.txt` |
| Windows运行/诊断聚焦 | 9 通过，包含于89 | 完整套件中的 test_runtime |
| 最终工作流/文档合同复查 | 4 通过，包含于171 | `docs-contract-final.txt/json` |

初轮 LADS 回归的八项失败记录仍保留于 `lads-full.txt`，修复后使用新日志名，不覆盖失败结果。数据库测试使用合成内存/临时 SQLite；不连接个人业务数据库。真实浏览器用户 JSON 备份本轮未读取、未恢复；不能称其通过验收。

最终差异检查发现三份新工作流各多一个文件末尾空行，另建 `workflow-eof-20261105` 备份后仅删除该三字节，并按最终文件哈希复查四项合同测试。验收脚本还通过合成正常/错误空白样本确认 `git diff --no-index --check` 的退出码语义；初次检查的错误假设及随后真实空白失败分别保留，不计为 Agent 代码返工。

## 真实四角色验证

状态：PASS（合成任务与权限/交接核验）。任务 `26ebde50-faee-42c9-8a3d-5796d4c10514` 实际调用独立 DeepSeek CLI 八次；Provider 均为 `lifeos_deepseek`、模型均为 `deepseek-flash`，未使用继承 GPT Provider 的原生子 Agent 或静默回退。

| 实际阶段 | 结果 |
| --- | --- |
| Scanner、方案 Reviewer | SCAN_COMPLETE、APPROVED |
| Builder 第一版、独立 QA | BUILT、FAIL；QA 真实 unittest exit 1 |
| 最终 Reviewer 第一轮 | CHANGES_REQUESTED |
| Builder 修复、独立 QA | BUILT、PASS；QA 真实 unittest exit 0 |
| 最终 Reviewer 第二轮 | APPROVED |
| 主控核验 | 八份阶段报告哈希、权限探针、实际命令、工作区与两份备份匹配；未发现额外缓存 |

总计两次构建、一次 `code_reworks`；QA 和 Review 拒绝各记一次，连接/认证/报告/执行/范围失败均为零。Scanner/Reviewer/QA 的实际权限探针为 read-only、无可写根；Builder 为独立 worktree 的 workspace-write。Windows 实际命令使用 `pwsh.exe -NoProfile`。最终交接保留原始 `AWAITING_MAIN_ACCEPTANCE`，主控核验另存回执，不改写 worker 状态或伪造 Stage3 MAIN-ACK/DELIVERY_READY。

证据：工作目录下 `live-attempt-2-fc3f3931/live-summary.json`、`live-handoff.json` 与 `verified-evidence.json`。原始八份记录由该回执引用的 run root 中 `state.json`、`stages/` 和 `finalhandoff.json` 保存。

本轮第一次实际尝试 `f20255d2-c4cc-43b3-ba6e-324bb573ca0b` 在权限预检即失败，模型调用数为零，provider/model 为 null，执行失败计数为一、代码返工为零。Codex `-c` 将 JSON map 识别为字符串，错误为 `shell_environment_policy.set` 预期 map。已改为逐项 TOML 标量覆盖，并增加解析回归测试及预检 stderr 回执；新任务重新验证，不覆盖失败记录。首轮失败哈希清单见 `first-live-failure-preserved.json`；67 份既有历史证据原字节不变。

此合成任务只修改隔离的 `add.py`，不是 Life OS UI/业务回归，也不是对主控工程改动的独立审查。生产权限、真实浏览器数据、GitHub 发布及模型计费金额不据此宣称通过。

## 文件清单与最终范围

- 修改执行层：`scripts/lads_orchestrator.py`、`scripts/lifeos-deepseek.py`、`lads_automation/team.py`、`.codex/worker-prompts/builder.md`。
- 新增运行层：`scripts/lads_runtime.py`。
- 新增阶段4活跃草稿：`lads_automation/github_release.py`、`scripts/lads_github.py`、`.github/workflows/ci.yml`、`security.yml`、`docs-check.yml`。仅本地文件，未上传或启用。
- 测试：修改 `tests/lads/test_orchestrator.py`、`tests/automation/test_install.py`；新增 `tests/lads/test_runtime.py`、`tests/automation/test_github_release.py`。
- 文档：更新 `docs/ARCHITECTURE.md`、`STATUS.md`、`DEVELOPMENT_PROGRESS.md`、`LADS_ORCHESTRATOR.md`、`LADS_STAGE4_ACCEPTANCE_REPORT.md`；新增 `LADS_GITHUB_AUTOMATION.md` 和本报告。
- 原允许的 `tests/automation/test_team.py` 未修改。与本轮基线比较，11 个既有文件改变、10 个新文件；其余 211 个基线文件保持原字节。最终清单、范围及备份哈希检查见 `final-scope-audit.json`，主控分层验收见 `final-acceptance.json`。

## 发布状态

本轮没有创建正式 commit/PR、远程 CI、合并或部署。上轮仓库原生 allow_auto_merge 设置不等于 LADS 全链路启用；保持现有保护，不移除检查、不绕过人工或仓库规则。四角色合成验证已通过，但全部工程增量的独立审查与真实 GitHub PR/CI/native merge 仍待验；自动发布仍未启用，需另行授权后继续。
