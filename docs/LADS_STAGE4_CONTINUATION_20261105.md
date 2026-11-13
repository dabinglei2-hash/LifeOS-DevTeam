# LADS 阶段4续办记录（2026-11-05）

当前：**BLOCKED，未提交、推送、创建PR、合并或部署；自动发布未启用。**

## 隔离与已实现候选

- 正式工作区 `C:/Users/Administrator/.codex/worktrees/750c/Life OS` 的业务代码和历史用户数据未修改；源码备份及清单位于 `backups/lads-stage4-continue-20261105-a42f9c11/`，共116份。
- 在同一 Git 仓库的 `D:/CODEX/.lads-s4-1105-a42f9c11` worktree 中，仅打包107个明确前置工程文件；未复制无关 `docs/AI/` 草稿，核心文档从 Git HEAD 开始。正式 HEAD 仍为 `aea9e758baac88c310319f34a77f94f2364b0c11`。
- 独立 Scanner/Reviewer发现并批准限定方案：critical finding 可被风险标记放行、控制器与CI安全规则不一致、Linux LF字节被Windows单值哈希拒绝。DeepSeek Builder已在隔离候选中修复，未整合进正式工作区。
- 第一候选摘要为 `c64bee9ae70508600a6a30d65b7d8ceb210d3a1cc44c72307149bdf69bba5345`。17个文件改变，测试增加到automation174项。精确豁免绑定路径、原始SHA和规则；真实critical不可由风险标记放行；PWA仅接受已核实的LF/CRLF有限哈希集合。

## 实际执行与阻断

| 证据来源 | 真实结果 | 限制 |
| --- | --- | --- |
| 主控旧候选 LADS 单测 | 89项通过 | 主控辅助，不是新候选独立QA |
| DeepSeek Builder | 174 automation、38 knowledge通过 | 不能代替独立QA |
| 独立 DeepSeek QA | automation退出1、174项/60错误 | 无可用临时目录，保持FAIL |
| 独立 QA knowledge | 38项通过 | 实际退出0 |
| 密封 broker及独立验证 | 34项检查、6个suite通过 | 路由、合成业务/JSON、浏览器与PWA等；不代表真实多用户认证 |
| 最终 Reviewer | CHANGES_REQUESTED | 独立复现临时目录错误；未APPROVED |
| 第二次 Builder | BLOCKED | 仅补记12份文档；所需控制器修复不在20文件白名单内 |
| 主控可信执行器探针 | 174 automation、38 knowledge通过 | `independent_qa=false`；隔离副本、私有TEMP、网络关闭，不替代QA |
| 工程库在线备份/新别名恢复 | 十张表逐行摘要一致 | 仅LADS工程库，不是个人JSON恢复 |

正式任务 `5fbe8834-26e6-4b78-84fb-af5dc1786766` 共六次有效角色调用，provider为 `lifeos_deepseek`、模型为 `deepseek-flash`；两次Builder、一次QA失败、一次Review要求修改、`code_reworks=1`。原计数不重置。

另保留：`8720970d-b1c4-4d17-a39b-0eee48d65adf` 在模型调用前因基础CLI不支持浏览器能力选择而停止，修正manifest后仍强制执行Stage3浏览器broker；`763c973c-7894-4707-946c-e250d6e0e6a6` 的Reviewer报告被类似密钥内容校验拒绝，计为报告失败1、代码返工0。它们没有被改写为通过。

## GitHub核验

实际仓库为 `dabinglei2-hash/LifeOS`，ID1420946918，公开，owner/admin权限有效，legacy origin指向已核实的同仓库重定向。原生Auto-merge已启用，但工作流仍0；main仍为上述原SHA，四项必需检查、strict、enforce_admins、禁止强推/删除均保留。默认工作流Token只读，不能自动批准Review；没有hooks、部署环境、Pages或已有deployment。上述都是只读核验，不代表本轮发布成功。

## 下一步与证据

需要确认[QA执行适配提案](LADS_QA_EXECUTION_ADAPTATION_PROPOSAL_20261105.md)：推荐保持QA模型只读，把文件型单测交现有可信隔离执行器实际运行，再由QA独立验证版本化回执。不能在当前QA FAIL下继续commit/PR。

本轮所有本地证据位于 `work/lads-stage4-continue-20261105-a42f9c11/`；六份角色stage、QA/Reviewer失败和两份备份位于正式任务run root。主控没有运行仅供门禁通过后使用的 `postapproval.py`、`prepare-authorization.py` 或发布CLI；没有生成虚假MAIN-ACK、独立批准或DELIVERY_READY。

真实浏览器用户JSON备份、本轮iPhone实机与GitHub PR/CI/native merge尚未测试。全部历史失败、工程候选、备份与未提交的无关工作保留。


## 决策更新（2026-11-05）

用户已批准方案A。原任务继续BLOCKED，旧QA失败及`code_reworks=1`原样保留；新适配独立任务已启动，未进行任何发布。

## 方案 A：独立适配与连接失败恢复

- 适配任务 `734687e3-b690-48f4-ab8c-2d9b210fc660` 实际完成 DeepSeek Scanner 和方案 Reviewer；Builder exit 1，终端证据是模型响应断流，未提交有效结构化报告。状态 `BLOCKED`，`connection_failures=1`，`code_reworks=0`，没有生成可验收候选或 QA 回执。没有覆盖原正式任务 `5fbe8834-26e6-4b78-84fb-af5dc1786766` 的失败及 `code_reworks=1`。
- 草稿实际修改 15 个白名单文件。主控在可信执行器的无网络隔离副本上跑两条固定命令：185 项 automation 和 38 项 knowledge 均 exit 0。两份输出摘要、224 文件草稿摘要和原失败证据摘要分别存于 `work/lads-stage4-qa-adaptation-20261105-0a6d49e1/disconnected-build-diagnostic.json`。原草稿与失败记录、诊断副本均未变化。`independent_qa=false`、`release_eligible=false`。
- 单独恢复任务 `8102275d-28e1-4125-8208-f2c9cee12c53` 使用新隔离工作区；草稿 SHA-256 为 `791b65c44476f67a80c4f2526729eb8ea60b7aad7ba61f033d6ed586b986319f`，是未批准输入。15 文件备份及清单在 `backups/lads-stage4-qa-recovery-20261105-0a6d49e1/`。恢复任务重新审核实际字节，无静默重试、无 GPT 代替任何 DeepSeek 角色、无权限扩大。必须完成实际密封回执、只读 QA、最终审查，再用生产 v2 流程独立验证，才可进入提交及 GitHub 门禁。
- 恢复任务 Scanner 读到相同 224 文件和草稿摘要，但 `turn.completed` 后追加了 CLI 重连错误。进程 exit 0 并不满足终止事件规则，调度器记为 `execution_failures=1`、`builds=0`、`code_reworks=0`；没有可信阶段报告，未继续派发。保留原始错误摘要与哈希。聚焦复验 `a5c5db7a-62db-4979-8cc7-e9898f4d7da8` 以同一摘要和两文件白名单继续，先核对报告目标路径在解析前的链接拒绝；未放宽终止事件门禁。复验准备时曾因将已忽略的运行记录计入快照而停止，未调用模型；之后改按 Git 管理的源文件校验，并保留该准备错误。
- 聚焦复验第一次 Scanner 为 `RETRYABLE`（连接失败，exit 1）；一次受限只读重试在本机执行 `git rev-parse --git-common-dir` 时超出 30 秒，状态 `BLOCKED`。保留 `connection_failures=1`、`execution_failures=1`、`readonly_connection_retries=1`、`builds=0`、`code_reworks=0`。主控之后对同一隔离 worktree 单独复跑 Git 命令，exit 0、402 毫秒；这不追认失败的权限预检，也不是稳定性保证。两个 Scanner 证据文件的 SHA-256 分别为 `3580a1550da4db4d27a3bae7e19512ed76592d4bdf06d9953b9caf7b9f156194`、`afd288423da701ce7a205a6b3c819f4f880771103e2654af95afc3478c96d46a`。

本轮最终状态：`BLOCKED`。草稿未安装，也未取得只读 QA 的真实验证和最终 Reviewer 批准。未进入主控交付回执、当前候选的文档/知识索引同步或远端发布；没有提交、推送、PR、CI、原生合并、部署。下一次从已保留的聚焦任务清单开始，先验证 CLI 完整终止事件和 Git 预检，再验证准确候选上的两条单测回执及浏览器回执。生产 v2 流程和 GitHub 原生合并顺序仍待复验，不能因主控诊断通过而开启发布。

## 用户续办后的真实验证

同一工作区的 Git 预检复跑 3 次均 exit 0。任务 `8c1e216c-9038-4508-8941-5cdeea2227c3` 的 DeepSeek Scanner、方案 Reviewer、Builder 均完成并通过报告校验。Builder 仅修改 `lads_automation/unit_qa.py` 与 `tests/automation/test_unit_qa.py`，增加回执目标在解析前的链接/重解析点检查和回归测试。候选 SHA-256 为 `f07fbb65349148f082ddbcf0a9151e1c11c4b40248909c64ebbf320df9ab7bdf`。无网络隔离执行器实跑 automation 186、knowledge 38，均 exit 0。浏览器 broker 的 34 项检查通过。单测回执 SHA-256 为 `cb8d27641f85c8af8e7aba5e576cc895b5ea81c0700507419ac7c2e543a41b2e`，浏览器回执 SHA-256 为 `6386817467578030a637b1e7addae4fc11208d51441538da2519c876b90b5fcb`。

只读 QA 进程 exit 0，报告未在 `evidence` 逐字引用规定命令，原校验器拒绝报告。`report_failures=1`、`code_reworks=0`。阶段未进入最终 Reviewer，也未取得发布资格。记录位于 `work/lads-stage4-qa-user-resume-20261105-0a6d49e1/bootstrap-summary.json`。新复验 `642e69e0-3b37-4f45-87ed-cddceffd1103` 仅在提示中明确逐字列出命令的要求。控制脚本第一次生成因引号错误在调用模型前停止，修正后做了语法检查并保留准备记录。上一轮证据和计数原样保存。

任务 `642e69e0-3b37-4f45-87ed-cddceffd1103` 与 `aedcb589-e3db-4405-9643-7edcb98da8c5` 均完成真实 Scanner、方案 Reviewer 和 Builder。可信执行器的 automation 未返回结构化退出码，knowledge 38 项通过，两次任务都在启动独立 QA 前停止。第二次留存的 RPC 错误前缀包含部分 unittest 输出和 `sandbox denied exec error`，旧截断逻辑丢失终止尾部。隔离引导器曾把该返回当作缺失进程回执并执行一次重试，仍未通过。后续引导器将含嵌入退出码、stderr 或拒绝字样的错误排除在重试之外。各任务 `code_reworks=0`，旧失败未覆盖。

主控为 `aedcb589-e3db-4405-9643-7edcb98da8c5` 的同一候选另做无网络诊断，使用保持原沙箱和命令的接收器，并在出现 RPC 错误时保留脱敏尾部。实际 exit 0、186 项通过，两份快照未改变，未复现失败且未取得独立 QA。结果保存在 `work/lads-stage4-qa-executor-recovery-20261105-0a6d49e1/diagnostic-executor-result.json`。当前独立复验 `5657813c-fc23-4ff5-acd8-eeb731423355` 使用曾通过主控测试的已知两文件补丁和原白名单，继续要求新回执和实际独立审查。

`5657813c-fc23-4ff5-acd8-eeb731423355` 完成真实 Scanner、方案 Reviewer、Builder，候选精确匹配早先的 `f07fbb65349148f082ddbcf0a9151e1c11c4b40248909c64ebbf320df9ab7bdf`。knowledge 38 项通过。automation RPC 返回嵌入退出码 1、部分 stderr 和沙箱错误，正常进程字段缺失，终止测试报告未保存。控制器没有重试该非零错误，没有启动 QA。失败回执 SHA-256 `6b3f56c568062bda72cf996d18b9c369681186c2f5344d252aa9d8574c7ababa`。候选和测试副本均未变化，代码返工为 0，状态 `BLOCKED`。

相同目录层级的独立诊断复跑了同一候选，186 项 exit 0，候选和副本均未变化，未复现故障。诊断与旧失败并存。之后在正式隔离引导任务 `073382f8-17d1-405c-a658-6f813f2f8e23` 内加了脱敏 RPC 尾部采集，没有放宽权限或通过条件。该任务五次真实 `lifeos_deepseek` / `deepseek-flash` 调用全部完成，角色顺序为 Scanner、Reviewer、Builder、QA、Reviewer，权限分别为只读、只读、工作区写入、只读、只读。186 项 automation、38 项 knowledge 和 34 项浏览器检查通过，QA 的三条验证命令均 exit 0，最终审查 `APPROVED`，候选哈希与先前完全相同。单测回执 SHA-256 `367858739a89cec57f36f38aa82a4153be5787245dbf8e74f3bff2abd3598763`，浏览器回执 SHA-256 `42c7bc27f4a11cc57d0135f805ce44be45bd3812a5d026ce8721b634ef260281`。

主控的首次安装预检因缺少仅供引导验证的标记而停止，补记明确的非发布标记后通过。主控核对两个白名单文件、备份、测试和最终审查哈希，只将精确字节安装到隔离工作区。安装回执与备份在 `work/lads-stage4-qa-rpc-trace-20261105-0a6d49e1/`。该次引导不能作为生产发布资格。正式 v2 任务 `75e3e5b1-266b-4b46-8a84-8b8a6806bec7` 已启动，剩余 2 轮逻辑返工。

正式任务 Scanner 确认 GitHub 合并顺序及工作流状态来源问题。计划 Reviewer 对技术方向无异议，但要求白名单加入 AGENTS 规定的 `docs/STATUS.md`，因此原任务 `CHANGES_REQUESTED` 并 `BLOCKED`。未启动 Builder，未增加代码返工。补齐该文档范围后的独立任务 `0502f178-5a54-41d2-8b7c-e7d7bce47e3a` 重新走方案审批。原阶段证据和哈希保存于 `work/lads-stage4-v2-plan-corrected-20261105-0a6d49e1/plan-rejection.json`。

## 正式 v2 验收结果

任务 `0502f178-5a54-41d2-8b7c-e7d7bce47e3a` 实际完成 11 次 DeepSeek 调用，包括 3 次 Builder、3 次只读 QA 和 3 次最终 Reviewer。候选实现先核验三个工作流，再登记原生合并，最后提交控制器状态，附工作流来源、异常恢复和合并证据检查。候选改动留在隔离区。正式工作区只更新本记录、状态和开发进度。

| 构建 | 独立 QA 验证退出码 | Reviewer |
| --- | --- | --- |
| 1 | `1, 1, 1`，缺单测回执，浏览器妙想编辑检查失败 | `CHANGES_REQUESTED` |
| 2 | `1, 1, 0`，缺单测回执，浏览器通过 | `CHANGES_REQUESTED` |
| 3 | `1, 1, 0`，缺单测回执，34 项浏览器检查通过 | `CHANGES_REQUESTED` |

确定的单测阻断在 `lads_automation/unit_qa.py`。它要求回执路径位于执行器 `work_root` 内，而原正式运行把隔离仓库放在 D 盘、把执行器根设在本机私有验收目录。两个固定命令因此没有在正式任务中生成回执。末轮候选 SHA-256 `401268eab443a279f31aaf43d1ef4a5ac6b5af06bf3a1b60f924230d351a7fd9`。历史控制器的终止文本写成“四次构建”，实际状态 `builds=3`、`code_reworks=2`、`qa_failures=3`、`review_failures=3`。保留原文本，按实际计数汇报。与原阶段4任务已发生的 1 次返工合计 3 次，自动返工停止。

在现有 CLI 支持的布局下，主控将合成任务副本和回执置于同一个受控 `--work-root` 子目录，并实际调用生产 v2 单测执行器和浏览器 broker。第一份候选实际通过 191 和 38 项单测，浏览器妙想编辑仍失败。第二份候选 SHA-256 `fedfb0a9b8a448878afa8928db490afd019204027ed5f79f503b7ba79117c169` 实际通过 193 和 38 项单测、34 项浏览器检查，源候选和诊断副本未改变。妙想的额外浏览器诊断在合成记录上观测到编辑成功持久化，该诊断的整套命令另因 harness 错误退出 1，保留为诊断。主控测试回执存于 `work/lads-stage4-v2-plan-corrected-20261105-0a6d49e1/`，均标为 `independent_qa=false`、`release_eligible=false`。

阶段4最终仍 `BLOCKED`。正式候选没有独立 QA PASS 或最终 APPROVED，未进入正式候选交付、GitHub PR、CI、原生合并和合并后知识库记录。只读复核确认 `main` SHA 和四项保护检查未变。下次正式验收的隔离 worktree 应位于受控验收目录子路径，并传入其父目录作为 `--work-root`。在派发之前先校验预计的 `work/lads/runs` 回执路径属于该受控目录，再按原门禁执行。

## 主控接手后的冻结候选复验

复验 `3b1fbcf1-9377-461f-8a71-488c9dc12ca1` 对应原正式任务第 3 份候选 `401268eab443a279f31aaf43d1ef4a5ac6b5af06bf3a1b60f924230d351a7fd9`。重新计算 224 个文件的哈希，验证三个构建备份及原阶段回执。隔离 worktree、执行器和回执落点在同一受控根目录。生产执行器实际通过 193 项 automation、38 项 knowledge 和 34 项浏览器检查，单测回执 `4d0472d829d93e8d4aa1a36588b51ac9a83600eeec26927c2e703328085e8073`，浏览器回执 `9188d81e995a4b3618418f2afc97e9cc957ef60833ac6eff0471707ad722c045`。

首次主控复验入口漏绑 worker 运行目录，在模型调用前停止。补绑后只读 QA 首次使用系统 Python，三条命令因缺少 Alembic 均退出 `1`。保留这些记录。再次执行沿用生产入口的工程 Python 准备步骤，没有安装依赖。独立只读 QA 的三条命令均退出 `0`，回执 SHA-256 `79f3d8b84e0b4eb0c0858a5112c42643ea8e5ab20ba8e2bbd8f5b1a641e18182`。一份 Reviewer 报告指出工作流来源缺口，另一份在 QA 通过后给出 `APPROVED`，但未回应同一 GitHub Actions 应用下的错误工作流来源。

主控复现 `lads_automation/github_release.py` 的 `_check_snapshot`（第 3398 行）。用候选的实际函数及无网络合成传输，提供三个来自 `.github/workflows/unrelated.yml` 的同名成功检查。函数只读取提交的 check-runs，未查询工作流来源，返回 `SUCCESS`。复现记录和复验回执索引位于 `work/lads-stage4-v2-plan-corrected-20261105-0a6d49e1/`。现有 MAIN-ACK 接收器也实测拒绝原 `BLOCKED` 交接。没有新构建、返工、正式交付或远端写入。

独立 DeepSeek Reviewer 已在看到两份前序意见、通过的 QA 回执及离线复现后完成定向复核，结论 `CHANGES_REQUESTED`，回执 SHA-256 `aceb0fae096ca57df0cb05dcf453d15fe9426f95b58090b21268a211159436b3`。确认 `_check_snapshot` 仅记录 `check_suite.id`，不会验证对应 workflow ID 或路径。全部候选与历史证据重新校验后均未变化。

待授权的最小修复只涉及发布控制器和现有发布测试。将三个必需检查绑定到指定工作流的文件路径及工作流 ID，再按候选提交和 `check_suite_id` 绑定实际运行。来源缺失、错误或歧义时拒绝通过。新增同名错误工作流的反例，保留现有分支保护与原生合并顺序。若获准，保留已累计的 3 次返工，单独记录这次修复并执行一次正式验收。

## 2026-11-11 主控授权续办与跨平台修复

用户先批准来源绑定修复，随后明确授权本轮由主控完成后续工程工作。独立任务 `e63d0fd8-9d81-4518-a621-1a22a9481f4d` 实际完成五次 DeepSeek 调用，一次构建，196 项 automation、38 项 knowledge 及 34 项浏览器检查通过，最终 Reviewer APPROVED。主控逐项核验后完成 MAIN-ACK、文档和知识交付。来源修复限定两个文件，候选 `9932e7f2f5e7712bfea3476799a4061c0e81b5843698d1298460776208c74c8a`。

真实发布产生提交 `6777361c77e9ea9ed2c60574f5412c5a20223278` 和 [PR 1](https://github.com/dabinglei2-hash/LifeOS/pull/1)。上传范围为 119 个明确授权的工程文件。Security 与 Docs 实际通过，CI 的 Linux automation 出现两个失败、一个错误，发布器进入 `CI_FAILED`，没有合并。失败运行 `42996932856` 保留。

主控在独立 worktree 修复三个测试文件。原生 Hook 测试显式模拟被测模块的平台，并增加非 Windows 拒绝执行断言。QA 报告路径夹具使用当前系统的绝对临时目录。权限预检夹具和其反例使用原生路径，JSON 断言检验该路径的真实转义表示。生产权限验证器未改变。最后一个测试文件包含既有合成凭据夹具，因此在发布控制器与 security 工作流中同步更新其原有精确字节哈希至 `241d4e445bdd8eee9f54563c361a8b6f42dd41f050f84d0c6243948c24017742`，没有新增豁免路径或规则。主控改动共五个文件。

第一次主控补丁中的多余字符造成一个单测错误，日志和旧字节保留，修正后重跑。直接从 WSL 读取 Windows worktree 的诊断还受到 Git 元数据路径和缺少 `python` 命令别名的影响。随后在 Linux 原生临时 Git 副本中执行全部测试，没有安装依赖或改写系统 Python。最终 Linux automation 197、knowledge 38、LADS 89 项命令均 exit 0，LADS 中一项 Windows PowerShell 测试按平台跳过。Windows automation 197、LADS 89 项全部通过，PowerShell 测试实际执行通过。所有这些补测均标为主控测试。

独立文档候选 `94785789-d279-4a2d-bf53-d105dfe4e689` 的 QA 与最终审查通过，但主控补充的 Linux 测试发现其余夹具问题，未接收或发布该候选。最终独立复验任务 `a279d5e4-a5e5-4a5a-b1cf-369508d7b0c5` 审查完整修复后的来源，Builder 只整理 STATUS 与 CHANGELOG，代码作者仍记为主控。其 QA 已通过 197 项 automation、38 项 knowledge 和密封浏览器回归。最终 Reviewer 发生一次连接中断，按现有策略执行唯一一次只读重试，代码返工为 0，连接失败单独记录。实际最终结果仍以该任务的后续回执为准。

工程知识库在交付前已完成在线备份，并恢复到新的 `stage4-restore-20261111.sqlite3` 别名。十张表的全部原 ID 一致。首次比较误用了不存在的 `KnowledgeStore.engine` 属性，恢复文件已正确创建，后续用现有只读 SQLite 接口核验成功，未覆盖原库或恢复副本。39 份旧阶段记录及备份重新核验哈希全部一致，历史累计 3 次返工保留。

本轮备份位于 `backups/lads-stage4-main-20261111/`、`backups/lads-stage4-main-final-20261111/`，过程与测试证据位于同名 `work/` 目录。当前正式工作区的业务文件、HEAD 和暂存区未改变。真实用户浏览器数据和个人 JSON 备份没有读取或恢复，物理 iPhone 与真实多用户认证不属于本轮通过范围。


<!-- LADS_STAGE4_ACTUAL_ACCEPTANCE_20261111 -->
## 2026-11-11 阶段 4 已通过真实 GitHub 验收

任务 `944c3ccd-603c-4f23-af8e-d4e9de4dd0d9` 已完成独立角色执行、主控验收、文档与知识索引交付、提交、推送、PR、真实 CI、原生 Auto-merge 及合并后知识回写。提交 `4cd141dc2ee1c839d05bf80eb2ac3a068ffc8b01`，合并 `2cecda7fc44d5a5458d08759d5540c3c665f876d`。三项工作流和 `LADS / controller` 全部成功，合并后的 main 三项工作流再次通过。重复 resume 返回同一合并回执，没有新增提交或 PR。[已合并 PR 3](https://github.com/dabinglei2-hash/LifeOS/pull/3)

本轮代码修复由用户明确授权的 GPT 主控完成，独立 DeepSeek 角色核验候选及真实 QA，Builder 整理文档。默认四角色仍通过独立 `deepseek-flash` CLI profile 运行。旧失败、39 份历史记录和累计 3 次历史返工全部保留，失败 PR 1、2 已关闭并保留分支及完整日志。

实际测试为 automation 198、knowledge 38、密封 broker 六套件与 34 项检查通过。Windows LADS 89 项全部通过，Linux LADS 89 项中一项 Windows PowerShell 测试按平台跳过。实际 Git 提交的 PWA 回放从 31 项摘要不匹配恢复为 0，未修改的浏览器 harness 通过全部 14 项检查，含离线 PWA。真实用户数据恢复、iPhone 实机尚未测试，产品真实多用户认证与 AI 权限继续保持原有未实现状态。

正式开发分支仍保留原 HEAD 与未提交内容。发布使用独立任务分支；工作流、存储和个人记录按原边界保护。后续普通任务从核验后的 `origin/main` 准备隔离 worktree，再明确纳入获准修改。当前机器的 Git 发布进程沿用已启用的 Windows 本地代理，GitHub API 使用直连，标准 TLS 校验保持启用。

当前结果优先于下方旧快照，旧 PENDING/BLOCKED 只代表其记录时点。完整证据见 [阶段 4 验收报告](LADS_STAGE4_ACCEPTANCE_REPORT.md)。
