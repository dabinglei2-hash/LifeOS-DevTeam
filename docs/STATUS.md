

## 2026-11-23 Control Center 后续阶段（当前）

主控按用户明确授权实现八页面、独立监控SQLite schema1、Worker真实进程心跳/过期/异常、outbox与SSE、任务/usage/QA/GitHub/知识库/设置。产品和原工程库schema不变。最终299自动化（含21新监控）通过，此前51知识库+89 LADS通过；构建、23项API/三尺寸浏览器、合成四角色心跳与16条SSE/备份恢复通过。后修复测试进程误上报：必须RUN --monitor显式host上下文；旧事件保留但不当可信在线数据。中断旧候选9925eb41，保留计数，需新审查。主控测试不是独立QA，合成进程不是DeepSeek。

独立完整基线审查、QA、真实DeepSeek监控与GitHub交付待本轮机器回执。GPT桌面心跳和账单未知，真机未测；无提交/推送/部署成功宣称。范围、备份、证据及限制见[监控记录](CONTROL_CENTER_MONITORING.md)。以下历史保留。

## 2026-11-23 LADS Control Center 第一阶段

本轮仅建立 React/TypeScript 总览与 FastAPI 本机只读接口，沿用既有工程知识库。首轮独立 Builder 在任务5f992691-77b9-403b-9dd7-c2a59c11a8b8中产出初稿，因超时及临时Git树被门禁BLOCKED，原记录保留、不追认为通过。主控在待审候选补齐基础依赖、Windows资源MIME类型与venv重定向器子PID归属校验，加入回归测试。主控已实际完成隔离构建/API/浏览器及Windows启停测试；独立审查、正式QA、提交和PR以本轮后续机器回执为准。Agent心跳、事件入库、实际费用、远程GitHub监控、完整知识库页及第二至八阶段未完成。产品代码、个人数据及原工程库结构均未改变。

当前启动入口见[控制中心说明](CONTROL_CENTER.md)。依赖、构建、日志与PID文件仅放LADS-Workspace/control-center-data；第一阶段不创建新数据库，也不执行数据库迁移。
## 2026-11-15 发布恢复竞态修复

研发PR #4实际原生合并、精确源码/CI/保护已核验；发现并保留OPEN到MERGED竞态和普通resume的base误判。主控已完成最小恢复代码与九项合成回归；完整独立验收及本轮远端结果以机器回执为准，不改产品数据或部署。详见[恢复记录](RELEASE_RECOVERY_20261115.md)。

## 2026-11-14 后续工程验收进行中

新根原生Hook、Codex只读知识MCP及独立DeepSeek exec MCP已由主控实际验证；工程代码按独立QA/Reviewer/发布门禁交付，当前状态以本轮机器回执和Git历史为准。Sites已有站点owner/public与HTTP200核实，隔离59文件构建通过，本轮只检查、不更新线上。失败/旧运行根保留，外移尚为兼容提案。详见[本轮记录](POST_MIGRATION_ACCEPTANCE_20261114.md)。

# 当前状态（双仓库源代码迁移已验真）

LifeOS 与 LifeOS-DevTeam 均为 dabinglei2-hash 的独立 Public 仓库。产品 PR #5（162ab01f）、研发 PR #1（63d9278f）已真实 CI 和原生 Auto-merge，独立四角色、MAIN-ACK、文档/知识与幂等门禁经过实际验证。原项目的 HEAD、工作区和 index 保留；业务数据及格式不变。

本轮最终报告与证据内容已整理，文档交付继续通过正常 PR 门禁。详情与限制见 [迁移报告](DUAL_REPOSITORY_MIGRATION_REPORT.md)：专用最小权限 GitHub App、真实部署、全部物理运行根外移、新根原生 Hook/MCP 和真机验收尚未完成，不能标为通过。

## 历史阶段记录（按原冻结时点保留）

# 研发团队当前状态

2026-11-12：双仓库迁移候选已建立，原仓库和用户数据未修改。实际测试与未完成项见 [迁移报告](DUAL_REPOSITORY_MIGRATION_REPORT.md)。独立发布门禁保持关闭，尚未宣称新仓库 CI/PR 自动合并成功。

2026-11-13 本轮更新：DevTeam README 最小初始化经用户授权完成，保护已恢复；源码尚未发布。主控双仓库 v3 QA 实际通过245+38+89测试及产品14项浏览器回归，独立模型审批与新远端 CI/PR/Auto-merge 仍待验证。前两轮失败保留。私有证据：D:/CODEX/LADS-Workspace/migrations/20261112-dual-repository/main-split-qa-receipt.json。业务数据、原工作树和原历史未变。

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
