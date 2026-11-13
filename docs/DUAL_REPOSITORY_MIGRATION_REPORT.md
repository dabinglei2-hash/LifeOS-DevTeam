# LifeOS / LADS 双仓库迁移报告

开始：2026-11-12；更新：2026-11-13。**当前为本地拆分与回归已核实，远端正式迁移仍 BLOCKED，不能宣布全部验收完成。** 本轮用户授权 GPT/Codex 主控实现；`reviewer_independent=false`。独立 DeepSeek 合成验证不冒充迁移代码最终审批。

## 仓库与来源

| 项目 | 本地目录 | GitHub | 状态 |
| --- | --- | --- | --- |
| LifeOS 产品 | `D:/CODEX/LifeOS-Product` | [LifeOS](https://github.com/dabinglei2-hash/LifeOS) | 独立 clone，候选分支 `codex/dual-repository-20261112`；来源工作树仍保留HEAD `607fb7687e7966ef1acfad02c07e558625044d2c`；独立发布提交65bf20d已通过PR #5原生合并为162ab01，未重置本地来源树 |
| LADS 研发 | `D:/CODEX/LifeOS-DevTeam` | [LifeOS-DevTeam](https://github.com/dabinglei2-hash/LifeOS-DevTeam) | 独立 Git 仓库，同名候选分支；README bootstrap 已经用户明确授权完成；main/本地 HEAD `2c27792a33c31363d1a5fb627bf1d36aa34f9fbe`，正式迁移源码尚未提交 |
| 私有运行数据 | `D:/CODEX/LADS-Workspace` | 不上传 | 完整备份、工程知识库、模型/测试回执与临时合成环境 |

两仓库均已真实核实为个人用户 `dabinglei2-hash` 所有、Public；本轮未新建重复 GitHub 仓库、未改变可见性。原工作区/主工作区及原分支 HEAD `aea9e758baac88c310319f34a77f94f2364b0c11` 保留。原 GitHub 旧功能、标签和历史不改写；旧提交仍含混放时期的工程文件，当前树拆分不等于历史抹除。

## 实际迁移内容

- 产品保留原 JS/CSS/HTML、assets/vendor、server、PWA 白名单构建脚本、产品文档、真实产品浏览器/Notion代理合成测试。业务文件以原活动工作树逐文件校验，金额、ID、存储键、JSON v11 和路由没有改动。
- 研发迁入 `lads_automation/`、`lads_knowledge/`、工程 `scripts/`、`schemas/`、工程安装/协议回归 fixtures、四角色 `.codex/agents/` 与提示词、七项 Skills、Hooks、工程测试和 LADS 历史文档。产品 app.js/index.html/server/vendor 未进入研发仓库。
- 原输入分类：产品 111、研发 108、两边分别重建职责的核心文档/工作流 17、私有配置排除 1。另保存远端新于旧活动树的工程文件，避免漏掉历史证据。[输入归属清单](MIGRATION_FILE_OWNERSHIP.json)
- 跨仓库 QA：产品实物复制到 LADS-Workspace 的一次性测试副本，只叠加工程验收 harness；不把产品业务源码提交到 DevTeam。旧混合工作流断言移到显式 legacy fixture，新增测试检查本轮实际工作流。
- 现行 Hook 去掉机器固定 Python 路径，兼容启动器用 `LADS_PYTHON=sys.executable`；正常信任机制保留。新路径的原生 Hook 自动模型回合尚未重验；不是桌面热加载完成声明。旧工程 fixture 仅作历史测试输入，不作新机器安装配置。

## 知识库与数据保护

实际数据库为 `D:/CODEX/LADS-Workspace/knowledge/dual-repository.sqlite3`。通过原工程库在线备份、验证、恢复到新别名建立，schema `0002`、FTS5 与完整性检查 `ok`。原十实体所有已有 ID 保留，旧库不覆盖；再次恢复到新别名通过。数据库、备份及 sidecars 均不进入 Git。

| 正式项目 | project_id | namespace |
| --- | --- | --- |
| LifeOS | `b64ec23c-dd38-528c-9827-d72127513caa` | `lads.lifeos` |
| DevTeam | `ebc8cefb-1f79-5193-bbfb-0ac069f9ee71` | `lads.devteam` |

`config/project-registry.json`、`lads_automation/project_registry.py` 和 `scripts/lads_project.py` 显式绑定两根、canonical origin 和 QA profile。越界路径、重复 ID、错源/错 profile、未初始化仓库及跨项目任务读取均有拒绝测试。两个正式 project_id 的文档索引及重复同步均已实测；DevTeam 索引绑定真实 bootstrap HEAD。工作树文档标记 dirty，不把尚未提交的内容伪装为某个 Commit。新增 v3 跨仓库 QA 合同和显式仓库发布路由已通过主控隔离测试，正式独立迁移与远端发布验收尚未执行。

完整原项目备份位于 `D:/CODEX/LADS-Workspace/backups/20261112-dual-repository-original`：20815 文件逐 SHA256 校验，Git all-refs bundle 验证通过。**源码备份不是浏览器 JSON 备份。** 本轮不访问日常浏览器配置、不读取或导入真实 JSON、财务、健康或聊天数据；不能声称真实用户备份恢复已重验。

历史和当前候选密钥模式扫描仅命中固定字节的合成脱敏测试字符串，无真实密钥发现。例外按文件路径和完整 SHA 绑定，不放行任意同名文件。此扫描不保证发现所有未知秘密格式；最终公开提交仍须检查精确 staged Diff。`.openai` 私有托管配置、运行日志、数据库和真实用户数据均未迁移到候选。

## 实际测试证据

| 检查 | 本轮实际结果 | 边界 |
| --- | --- | --- |
| 产品 JS 语法/PWA构建/资源摘要 | 30 个语法文件；构建和摘要通过 | 构建只在临时副本更新 sw.js |
| 产品浏览器回归 | 14/14 PASS | 合成数据、独立浏览器上下文；含路由、日程、学习、财务、妙想、v11恢复、去重/坏文件、窄屏/短屏/桌面、底栏、离线与控制台 |
| Notion代理契约 | PASS | 本地真实路由 + 注入 FakeNotion；未连接真实 Notion；真实产品账户认证 NOT_IMPLEMENTED |
| 研发自动化 | 245/245 PASS | 主控受限 RPC 执行；含 Registry、双仓库 v3 合同、封存报告篡改拒绝及日志测试；不是独立模型审批 |
| 知识库 | 38/38 PASS | 合成内存/文件；另有真实工程库在线恢复与原ID校验 |
| 调度器 | 89/89 PASS | 本机 Windows；不代替 GitHub CI |
| 本轮工作流安全 | 新增6项 PASS | 私有路径/合成密钥拒绝、权限、固定版本、MCP禁用和可移植Hook；原生回合仍未测 |
| 四角色实际模型路由 | PASS，5次真实 CLI 调用，1次构建 | 任务 `73417744-862d-4656-9891-06e72b0dea1c`，Scanner → Reviewer → Builder → QA → Reviewer；仅隔离合成任务 |
| 四角色权限/报告 | PASS | Scanner/Reviewer/QA `read-only/never`，Builder独立worktree `workspace-write/never`；实际权限探针及纯JSON报告；无GPT回退 |
| 新迁移 GitHub CI/PR/自动合并 | **尚未执行，BLOCKED** | 不能以本地测试或旧 PR3/PR4 代替 |
| 物理 iPhone、真实数据恢复、生产部署 | **尚未测试/未部署** | 不虚报完成 |

模型均由实际进程元数据核实为 `lifeos_deepseek / deepseek-flash`，各角色独立密钥通过既有 Windows 用户环境变量读取；不打印、不写入仓库或 argv。GPT/OpenAI 主控配置语义保护在各阶段通过，没有更改主脑 Provider/登录。当前 Codex CLI 实测 `0.167.0-alpha.4.1`，旧报告版本仅为历史。

## GitHub 工作流与保护

产品候选具有 ci/security/docs-check/staging-deploy/production-deploy 五个工作流；研发具有 ci/security/docs-check/lads-build/release 五个工作流。默认 `contents: read`，没有 DeepSeek secret、pull_request_target 或高权限 self-hosted runner；Actions 引用固定提交。现有 LifeOS main 的四项检查、管理员保护和禁止强推/删除经前后比较保持一致。

DevTeam 已真实启用 Auto-merge 设置，并创建 active 规则集 `27133373`：main 必须PR、严格 CI/安全/文档/控制器检查，禁止强推和删除，无 bypass actors。用户另行明确批准一次最小初始化：仅在此期间停用本次新建规则集，通过 GitHub Contents API 写入单个 README，并在 finally 恢复、读取核验原规则集。根提交 `2c27792a33c31363d1a5fb627bf1d36aa34f9fbe` 无父提交、仅 README。正式源码仍须完整独立审批、CI 与 PR；没有推广 main 直写例外。设置开启不等于已验证合并成功。

LifeOS 已建立 production 环境：要求用户人工审批，只允许受保护分支，不允许管理员 bypass。生产 workflow 手动触发并需明确确认；没有服务器、域名或云授权时以 BLOCKED 非零退出，不执行部署。Staging 仅在 main CI 成功后触发，目前同样只报告缺少目标；未部署。研发 release 也仅做失败关闭的准备检查，不创建标签或 Release。

现有 GitHub App 安装已核实，但当前选择范围为 all repositories；本轮管理设置复用原 gh 安全登录，未新建 PAT 或存储 Token。正式自动化 App 最小化到两个仓库的权限仍待配置，不能把工作流的只读 token 误称为现有账号凭据已最小化。

## 尚未完成与下一步

1. 本次迁移候选须绑定精确 Diff/摘要完成有效独立 QA 与最终 Reviewer，合成四角色烟测不代替此批准。
2. DevTeam README bootstrap 已完成并恢复保护；下一步正式源码 PR 必须以此真实 main 为 base。
3. 产品候选通过后取得真实 Commit，设置 DevTeam 的 `LIFEOS_PRODUCT_QA_SHA`。当前工作流缺省即阻断，禁止用旧失败 main 静默兜底。
4. 新增 scripts/lads_project_flow.py、split_qa.py 与发布器双仓库精确路由；主控 v3 QA 已通过，仍须独立四角色和远端实证。旧 v2 继续仅适用原产品合同，DevTeam 发布明确拒绝 v2。
5. 两边执行真实任务 PR、GitHub CI、原生 Auto-merge、合并后 main CI 和知识回写，再填写真实新 Commit/PR/合并 SHA；当前未生成这些版本，绝不填造。
6. 配置实际部署服务和最小权限 GitHub App；另行授权部署，再验证 staging/production。暂不创建无依据服务器或购买服务。

原项目全部保留，失败/返工记录保留，无强推、重写历史或用户数据迁移。本报告是阶段性交付和阻断清单，不是自动研发或部署完成证书。

依据：[GitHub保护分支API](https://docs.github.com/en/rest/branches/branch-protection)、[GitHub环境审批](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)、[DeepSeek官方Responses兼容说明](https://api-docs.deepseek.com/guides/responses_api/)。实际本地证据优先，文档能力不等于本机调用成功。

## 2026-11-13 本地工程合同复验

真实主控 RPC 任务 `40970f97-9ea1-4ae5-b273-0cd14de1fc43`：自动化245、知识38、LADS89 均 exit0/零跳过；产品浏览器14项及原文件/PWA/Notion代理契约通过，三项真实认证/AI能力仍 NOT_IMPLEMENTED。回执及完整输出在私有 LADS-Workspace；`reviewer_independent=false`。第一轮合成副本错误合并两套 workflow 导致数量断言失败；第二轮精确合成凭据文件哈希过期导致两项安全测试失败。均保留原失败，没有清零旧计数。现按来源整体隔离配置命名空间、缩短 Windows 临时路径、更新经核验的固定字节例外；RPC 错误完整脱敏诊断单独持久化，不再丢失终端尾部，也不伪造 exit0。

v3 只读模型 QA 执行四条固定 stdout-only 验证命令；可信受限执行器先在私有副本运行245+38+89与产品 UI。封存合同同时绑定所选仓库候选与另一仓库精确实物/HEAD，验证原始进程输出摘要、内层收据、所有输入及报告文件。失败基础设施不会算作代码返工。两仓库 origin、授权、QA repository 必须精确一致；跨仓库凭据/审批不能复用。实际独立迁移审批尚未完成。

产品独立 clone 的76个文件曾因 Windows checkout 产生纯 CRLF 差异。已先单独备份，再逐个证明换行归一后与现有 HEAD blob 完全一致，将该副本恢复为原 Git canonical 字节，避免发布产生无业务变化的76文件噪声。原用户工作区字节不变；唯一既有浏览器回归修复仍按真实差异审查。

## 2026-11-13 研发预检与回执修复

产品任务 f1b9ba0f-f123-480b-a402-a76568fa7c00 已完成独立 Scanner、PLAN Reviewer、Builder确认文档、实际隔离 v3 QA、FINAL Reviewer APPROVED、MAIN-ACK 和文档/知识回写。候选 6c000eff2750ccbfc751adbf412b66db65cbb7be1d20ad63044ee4a451c87387，实际245+38+89及产品14项浏览器通过。一次 Reviewer 流连接失败单独记录，代码返工为0，不切换GPT；原失败保留。产品发布本地门禁目前已通过，尚未提交或推送。

研发首次任务 3e5b3b6e-241e-4432-970c-a8a5b05ce25a 被独立 Scanner 正确 BLOCKED：Git已初始化但HEAD不存在时原Registry返回None，flow未拒绝。主控已修复为必须解析有效 HEAD commit；空源码安全扫描也改为拒绝。13项Registry和7项安全测试实际通过，含空仓库/正确origin拒绝及存在commit正例。Scanner原报告和计数不改写，修复尚待新的独立审批。

产品发布门禁曾被Agent上下文裁剪阻断：get_task_history返回8KB上限上下文，裁掉部分真实文档回执。新增控制器专用get_release_receipts，仅返回固定证据字段，逐列表最多20、总计64KB，超限拒绝、不静默丢弃，不增加Agent上下文或读取产品数据。门禁按最新记录顺序保留当前证明，旧失败不覆盖它。新增6项真实内存数据库测试、既有67项发布测试全部通过；实际产品七份文档及四条QA证明已由该只读接口重验通过。数据库schema未变，无用户业务记录读取。

迁移实现与修复由主控完成；独立审批只以真实工作进程报告为准。下一步：对更新后的研发完整候选重新执行四角色与隔离QA，然后才推进两仓库PR、CI和原生Auto-merge。不部署。

2026-11-13 配置保护补充：研发重验任务4843ddeb-84a4-42f2-adf4-6a09856324d4因运行中用户级非project配置哈希变化被保护门禁BLOCKED，未复用其扫描结果，原任务/计数/快照保留。主控只读检查当前仍为OpenAI/gpt-6-astra，未写入配置或登录文件；旧快照没有字段级内容，不能臆断变化来源。新的独立验收将固定当前合法配置摘要，不放宽运行内漂移门禁。四个独立DeepSeek profile仍须逐次路由和实际权限预检，无GPT回退。

隔离依赖审计已实际完成：使用临时venv中的pip-audit 2.9.0审计requirements-lads.txt的6项固定依赖，exit0，已知漏洞0，源码依赖字节不变。它是本地主控安全证据，不代替GitHub Actions实际运行。

## 2026-11-13 运行工件位置事实更正

独立Scanner任务d01f0922-23ce-418a-8a5e-a7462899fe3a复核148项源码及新修复后，因AGENTS/ARCHITECTURE对“所有日志已外移”的错误声明BLOCKED。该任务保留，不追认通过。最小纠正为准确记录当前受保护合同：知识数据库、跨仓库QA测试副本、完整原始备份在LADS-Workspace；独立CLI的state/events/source/candidates/Builder worktree/stages仍在各repo的ignored work/lads/runs；阶段3/4 journal仍在既有用户级私有安全目录。原长期规则要求使用该UUID目录/父链和worktree隔离，本轮不把未经验证的物理路径重构冒充已完成。所有这些工件均不得进入Git或包含真实业务数据。

Git仓库内容分离与物理运行目录外移是两件事。新增强制跟踪work文件仍被安全扫描拒绝的回归测试；未来若统一物理运行根，需要另外完成路径/权限/回链的等价迁移验证。目前仅保证工程数据库的实际文件已按用户要求外置，不声称全部工件已外移。

## 2026-11-13 产品远端已验真、研发文档冻结

产品任务f1b9ba0f-f123-480b-a402-a76568fa7c00已通过真实GitHub必需检查并经原生Auto-merge完成[PR #5](https://github.com/dabinglei2-hash/LifeOS/pull/5)：源提交65bf20dcb99adaed48f9b45f7cf5b9067183ec02，合并162ab01f71226320026494e1b17f8fa6e49c70f5。合并后CI43443555144、安全43443555011、文档43443555081均success；staging readiness43443697166因没有部署目标按设计非零退出，未部署。知识合并回执MERGED已写入。DevTeam的公开CI变量LIFEOS_PRODUCT_QA_SHA已读取核验固定为该产品合并SHA，不含密钥。

研发任务f057f2c5-d9df-4cf5-90bb-1295ac623581由主控主动取消，以同步修正README和工作环境说明的残余路径过度声明；实际Scanner进程返回非零，原CLI输出、任务状态及计数保留，不能称作独立审查通过。主控没有更改真实隔离路径，只准确记录现状。以下新任务重新冻结全部候选及白名单文档修正；研发源码尚未提交/推送，独立验收和远端CI仍待完成。
