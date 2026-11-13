# LifeOS / LADS 双仓库迁移验收报告

本报告核实**源代码迁移与两仓库正向 PR 自动合并链已经完成**。未完成事项单列，不据此宣布所有 LADS 能力或生产部署已验收。事实基准为下列两个源代码合并提交；本报告自身通过后续文档 PR 交付，其最新提交见 GitHub 文件历史。原冻结草案保留在源提交历史和私有备份中，没有抹掉失败记录。

## 1. 仓库、目录与版本

两仓库均为个人账号 **dabinglei2-hash** 所有、`User / Public`，复用已有仓库，未改变可见性。

| 项目 | 独立本地目录 | 源提交 | 源合并提交与 PR |
| --- | --- | --- | --- |
| 产品 [LifeOS](https://github.com/dabinglei2-hash/LifeOS) | `D:/CODEX/LifeOS-Product` | `65bf20dcb99adaed48f9b45f7cf5b9067183ec02` | `162ab01f71226320026494e1b17f8fa6e49c70f5`，[PR #5](https://github.com/dabinglei2-hash/LifeOS/pull/5) |
| 研发 [LifeOS-DevTeam](https://github.com/dabinglei2-hash/LifeOS-DevTeam) | `D:/CODEX/LifeOS-DevTeam` | `aa6ffc7ca9c2954dae766d41bbc7b3c396407812` | `63d9278f7c9958699d17c7cab62c0d96d3a27eb8`，[PR #1](https://github.com/dabinglei2-hash/LifeOS-DevTeam/pull/1) |

两根 `.git` 独立，不共享 common dir。原 `D:/CODEX/Life OS`、原活动工作树和原分支均保留，原 HEAD `aea9e758baac88c310319f34a77f94f2364b0c11`、Git 状态和暂存区经复核不变。没有强推、硬重置、清理用户文件或改写历史。

DevTeam 原为空仓库。按用户另行确认，仅用 README 初始化 main：`2c27792a33c31363d1a5fb627bf1d36aa34f9fbe`；临时停用本轮新规则集后，已按原描述恢复并读取核验。正式源码随后完整经过独立审批、CI 和 PR，没有扩大初始化例外。

## 2. 实际文件归属

- **产品**：原 HTML/CSS/JavaScript、UI/helpers、assets/vendor、可选 loopback Notion 代理、PWA 白名单构建、产品测试、产品文档及五个工作流。该源合并快照 136 个文件，不含 `.agents/`、`.codex/`、LADS 模块、工程数据库、备份、日志或私有托管配置。
- **研发**：`lads_automation/`、`lads_knowledge/`、Schema/迁移、Registry、四角色配置/提示词、七项 Skills、Hooks/MCP 禁用策略、工程测试、工具、研发文档和五个工作流。该源合并快照 150 个文件，不含 app.js、index.html、server、assets 或 vendor 产品业务实物；跨仓库测试副本只在私有环境生成。
- 输入归属及原始字节见 [迁移输入清单](MIGRATION_FILE_OWNERSHIP.json)。[初始审查账本](MIGRATION_REVIEW_SCOPE.json) 是源迁移冻结阶段证据，**不是未来每个新 HEAD 的通用账本**。源报告的历史版本见上述源提交。
- 76 个产品文件仅在新 clone 中恢复为既有 Git canonical 换行，逐个证明内容等价；原用户工作树字节不变。业务 ID、日期、金额、USD 口径、公式、存储键与 JSON v11 未改动。

原完整备份在 `D:/CODEX/LADS-Workspace/backups/20261112-dual-repository-original`：20,815 文件逐 SHA256 校验，all-refs bundle 验证通过。源码/Git 备份**不等于浏览器 JSON 备份**；本轮未访问日常浏览器或真实用户 JSON，仅在独立浏览器用合成数据验证 v11 导出/恢复。

## 3. 模型、权限与知识边界

GPT/Codex 仍是唯一主控，原 OpenAI 登录不变；本机检查为 `OpenAI / gpt-6-astra`。四角色均经独立 CLI profile 调用 `lifeos_deepseek / deepseek-flash`，不是原生 GPT 子 Agent，没有静默回退。

| 角色 | profile | 实际权限/工作方式 |
| --- | --- | --- |
| Scanner | scanner-deepseek | 每次实际预检 read-only / never |
| Reviewer | reviewer-deepseek | 独立只读计划与最终审查 |
| Builder | builder-deepseek | workspace-write / never，独立 worktree、精确白名单 |
| QA Tester | qa-tester-deepseek | 独立只读；核验无凭据受限执行器的真实密封结果 |

主控实现迁移和修复，Builder 的有限文档贡献单独记录；不伪称 DeepSeek 编写了主控代码。两个源迁移的真实验收任务：产品 `f1b9ba0f-f123-480b-a402-a76568fa7c00`、研发 `b8fc0fe9-92d2-41cf-a2bd-cf1f95af49de`，均完成 Scanner→PLAN Reviewer→Builder→QA→FINAL Reviewer APPROVED→MAIN-ACK→文档/知识→PR/CI/原生合并。源报告中的合成烟测不冒充迁移审批。

工程数据库实际为 `D:/CODEX/LADS-Workspace/knowledge/dual-repository.sqlite3`，SQLite/Alembic `0002` 与 FTS5 已核实。原实体 ID 保留，旧库未覆盖；新 Registry 分离命名空间：

| 项目 | project_id | namespace |
| --- | --- | --- |
| 产品 | b64ec23c-dd38-528c-9827-d72127513caa | lads.lifeos |
| 研发 | ebc8cefb-1f79-5193-bbfb-0ac069f9ee71 | lads.devteam |

数据库与备份不入 Git，目录访问收紧到当前控制用户、Administrators、SYSTEM，并保留原权限恢复描述。受信主控提供受限上下文，不向模型发送原始数据库或用户记录。实际知识合并回执已为两任务记录 MERGED；失败任务保留 BLOCKED。旧命名空间历史未被静默重归属。

**物理路径限制**：数据库、复合 QA 和完整备份在 LADS-Workspace；已验证的 CLI control/source/candidate/worktree/stages 仍在各 repo 的 ignored `work/lads/runs`，Stage3/4 journal 仍在既有用户级私有目录，尚未全部外移。它们不属于 Git 源码，严禁真实业务数据和上传；已实测强制跟踪 work 文件也被安全扫描拒绝。未来物理迁移需另验 UUID 父链、Git 回链和权限，不把“未上传”称为“已外移”。

## 4. 实测与 GitHub 门禁

| 验证 | 实际证据/边界 |
| --- | --- |
| Windows 独立工程 QA | 255 automation、38 knowledge、89 LADS，全部通过、零跳过；不是仅 Builder 自述 |
| 产品回归 | 14 项 Playwright Chromium 流程；路由/日程/计划与实际分离/财务/妙想/v11恢复/去重与坏文件/独立上下文/窄屏短屏桌面/底栏/离线/控制台 |
| 其他 broker 检查 | 31 PASS、3 NOT_IMPLEMENTED；真实认证和产品 AI 未实现，不计为通过 |
| PWA/语法/API | 30 个 JS 语法、白名单构建/摘要、真实 loopback 路由加 FakeNotion 合成契约通过；未连接真实 Notion |
| 依赖安全 | 独立临时 venv 用 pip-audit 2.9.0 审计 6 个锁定工程依赖，exit0，已知漏洞0，源码依赖不变 |
| 原始代码与数据 | 原 Git HEAD/状态/index 不变；未重置浏览器存储、未操作财务/健康/学习等真实记录；真实记录恢复未测试 |
| 幂等 | 两仓库相同授权重复 resume 均返回同一 MERGED/PR/SHA，未生成重复提交或 PR |

合并后的实际 GitHub 检查：

- 产品：[CI 43443555144](https://github.com/dabinglei2-hash/LifeOS/actions/runs/43443555144)、[Security 43443555011](https://github.com/dabinglei2-hash/LifeOS/actions/runs/43443555011)、[Docs 43443555081](https://github.com/dabinglei2-hash/LifeOS/actions/runs/43443555081) 均成功。
- 研发：[CI 43454570934](https://github.com/dabinglei2-hash/LifeOS-DevTeam/actions/runs/43454570934)、[Security 43454571021](https://github.com/dabinglei2-hash/LifeOS-DevTeam/actions/runs/43454571021)、[Docs 43454571085](https://github.com/dabinglei2-hash/LifeOS-DevTeam/actions/runs/43454571085)、[Build 43454570941](https://github.com/dabinglei2-hash/LifeOS-DevTeam/actions/runs/43454570941) 均成功。Linux CI 成功不等于 Windows 专用测试在 Linux 逐项执行；上面的零跳过数属于本机密封 QA。

main 要求 PR、严格 CI/安全/文档及 LADS/controller；禁止强推/删除。产品管理员保护及四检查与原配置一致；DevTeam active 规则集 `27133373` 无 bypass actors，前后策略一致。使用真实 native Auto-merge，未伪造人工 Review、直接推 main 或改可见性。工作流 `GITHUB_TOKEN` 为 contents:read，无 untrusted privileged runner 或 PR secret 暴露。DevTeam `LIFEOS_PRODUCT_QA_SHA` 显式固定产品合并 `162ab01f71226320026494e1b17f8fa6e49c70f5`，没有旧失败版本兜底。

源与历史敏感模式扫描未发现真实密钥；合成例外按完整路径和字节 SHA 绑定。实际 API 密钥值不进入前端、配置、日志、Git 或报告。模式扫描不保证发现未知格式的所有秘密。

## 5. 失败、限制与下一步

四轮研发阻断/取消原状态保留，并归档 619 个文件逐哈希校验：unborn HEAD 预检缺陷 `3e5b3b6e-241e-4432-970c-a8a5b05ce25a`、外部非 project Codex 配置漂移 `4843ddeb-84a4-42f2-adf4-6a09856324d4`、运行位置文档过度声明 `d01f0922-23ce-418a-8a5e-a7462899fe3a`、主控主动取消旧快照 `f057f2c5-d9df-4cf5-90bb-1295ac623581`。产品一次 Reviewer 流连接失败单计连接错误，不混入代码返工；旧失败和计数未清零。回执读取裁剪、RPC 终端诊断、空来源与仓库路由的修复有对应实际测试。

- **尚未完成最小权限专用 GitHub App 授权**：当前沿用既有 gh 用户登录；现有 App 安装为 all repositories，未擅自改变其账号级范围。不能声称已实现仅两仓库的 App 凭据；下一步需用户账号侧专用 App/安装授权。工作流最小 token 权限和程序仓库 allowlist 已验证。
- **没有正式部署目标**：无主机/域名/云授权。产品 [staging readiness 43443697166](https://github.com/dabinglei2-hash/LifeOS/actions/runs/43443697166) 按设计失败关闭，不是部署成功；生产需手动确认与 environment 用户审批，管理员不得 bypass。研发 release 也是阻断骨架，未发 tag/Release。
- 新根的原生 Hook 模型自动触发、完整 MCP、物理 iPhone/PWA 安装/键盘、真实用户数据恢复均未重验。历史成功报告不替代新路径验收；真实账户/云同步/AI 也未因迁移新增。
- 后续从 LifeOS-DevTeam 读取 AGENTS/核心文档，以 Registry 显式选择 lifeos 或 devteam，再运行 `scripts/lads_project_flow.py RUN --project ... --task-file ... --credential-source windows-user`。保持独立 QA/Reviewer、MAIN-ACK、精确字节、文档/知识与 GitHub 门禁，高风险及部署仍须单独授权。

结构化证据：[MIGRATION_FINAL_EVIDENCE.json](MIGRATION_FINAL_EVIDENCE.json)。原始模型/QA/备份回执在本机私有 LADS-Workspace，未上传个人记录或原始运行日志。
