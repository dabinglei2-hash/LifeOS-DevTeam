# LADS 实际架构

Python 独立 DeepSeek CLI 工作进程由 scripts/lads_orchestrator.py 编排。lads_automation 管理真实权限预检、不可变候选、日志/报告、隔离 QA、文档/知识回执与 GitHub 发布门禁；lads_knowledge 使用 SQLAlchemy/Alembic/SQLite FTS5。工程数据库、跨仓库QA产物和完整备份位于独立LADS-Workspace，绝不连接产品用户数据库。既有受保护CLI控制目录仍在各代码根的ignored work/lads/runs/<task>；阶段3/4 journal在既有用户级私有目录。它们不是Git源码，也尚未全部物理外移；保留UUID父链与worktree安全合同，单独归档并禁止任何真实业务数据进入。

config/project-registry.json 固定两个 project_id、namespace、canonical origin、目录与 QA profile。scripts/lads_project.py 校验并索引正式工程文档；Git/文档是事实来源，数据库不能反写架构。旧工程库保留，新数据库从已核实在线备份恢复，保留旧实体 ID。

正常注册任务使用 scripts/lads_project_flow.py RUN --project lifeos|devteam --task-file ... --base D:/CODEX --workspace D:/CODEX/LADS-Workspace。它在读密钥前扫描两个来源，准备独立受限 RPC 执行器和 v3 跨仓库 QA broker。Builder 用精确白名单独立 worktree；Scanner/Reviewer/QA 逐次通过只读权限预检。只读 QA 执行固定四命令核验密封回执，实际测试由无凭据 broker 在私有合成副本运行。候选/配套来源、HEAD、原始命令/输出、报告及组合文件均哈希绑定。产品业务实物只进入私有测试环境，不进入 DevTeam Git。

主控 MAIN-ACK、ACCEPT、文档和知识回执继续复用 scripts/lads_stage3.py。scripts/lads_github.py 显式 --repository 只能选择两个 canonical 仓库；授权、origin、QA 仓库必须相同，DevTeam 拒绝旧 v2 合同。精确暂存、独立审批、CI、原生 Auto-merge、保护和合并后知识回执仍强制；产品 PR #5（162ab01f）与研发 PR #1（63d9278f）已在各自新仓库完成源码迁移、真实 CI 与原生 Auto-merge；该验收不包括新根原生 Hook/MCP 或生产部署，详细证据和限制见迁移报告。

四角色配置/提示词位于 .codex/agents、.codex/worker-prompts，七项 Skills 位于 .agents/skills。主控 GPT/OpenAI 不变；子进程各用专属 Windows 用户环境变量和 DeepSeek profile。禁止原生 GPT 子 Agent 和静默回退。Hooks 使用 LADS_PYTHON 和 scripts/lads_session.py，保留正常信任；新根原生自动触发尚未重验。MCP 仍默认禁用。

两个仓库各五个最小权限工作流；DevTeam CI 必须显式固定 LIFEOS_PRODUCT_QA_SHA。无部署目标时 staging/production/release 非零退出，不创建虚假部署。生产还必须进入用户审批的 protected-branches environment，管理员不可 bypass。详情见 [迁移报告](DUAL_REPOSITORY_MIGRATION_REPORT.md)。

发布控制器使用 KnowledgeReader.get_release_receipts 读取固定列的项目/任务隔离证据，不再把用于Agent上下文的8KB裁剪视图当成完整回执。接口限20条/列表和64KB总字节，溢出阻断；旧Agent查询上限不变。Registry预检必须存在真实HEAD commit；空Git目录/正确origin本身不够。

<!-- lads-automation:event {"doc":"docs/ARCHITECTURE.md","id":"551356b1-c84f-51ae-8ca7-5d8795de5f61","payload_sha256":"57fba3d6c999f09ffc0372f7304217794c792b50e64bee4ea1d894c6b1be2d93"} -->
### LADS event 551356b1-c84f-51ae-8ca7-5d8795de5f61

- Task: `b8fc0fe9-92d2-41cf-a2bd-cf1f95af49de`
- Event: `architecture_changed`
- Recorded: `2026-11-13T15:31:35.004870Z`
- QA/review verified: `true` (machine delivery receipt `{"acceptance_mode":"independent","authorization_sha256":null,"candidate_sha256":"733febae71fe949431a2237832af23ec533eccd920b6bd2e1a377309b0377df5","controller_sha256":"c32e0e25b828c757ee378226b49a76cc827cfcd5594fb7ced00b5edb663c672e","qa_sha256":"7f8fec5c65868d9cc5c36d006389900550cda4246f97587a0a4943a0626268b8","reviewer_sha256":"0cae6211238e41c223a3096927d420ec7f3cde5fb94e0f1b0b988eae060c4a14","simulated":false}`)
- summary: "Stage 3 delivery event: architecture_changed"
- reason: "User-authorized main-authored dual-repository engineering migration, independently reviewed complete baseline and ACK. Actual v3 QA passed 255 automation, 38 knowledge, 89 LADS and product broker checks; account authentication and AI remain unimplemented. Private runtime locations are explicitly documented without claiming physical relocation. Product PR5 merged; DevTeam publication not yet performed. Original failed attempts remain blocked and archived."
- details: "{\"candidate_sha256\":\"733febae71fe949431a2237832af23ec533eccd920b6bd2e1a377309b0377df5\",\"changed_files\":[\"docs/MIGRATION_ACK.md\"],\"changed_files_count\":1,\"changed_files_sha256\":\"4aad177b74e771a90935ac4d6657b1087ef7dd1bc1d2d12fcd3a32e389f51c26\",\"dirty\":true,\"observed_head\":\"2c27792a33c31363d1a5fb627bf1d36aa34f9fbe\",\"qa_contract_version\":3,\"reason\":\"User-authorized main-authored dual-repository engineering migration, independently reviewed complete baseline and ACK. Actual v3 QA passed 255 automation, 38 knowledge, 89 LADS and product broker checks; account authentication and AI remain unimplemented. Private runtime locations are explicitly documented without claiming physical relocation. Product PR5 merged; DevTeam publication not yet performed. Original failed attempts remain blocked and archived.\",\"review\":{\"sha256\":\"0cae6211238e41c223a3096927d420ec7f3cde5fb94e0f1b0b988eae060c4a14\",\"status\":\"APPROVED\"},\"source_head\":\"2c27792a33c31363d1a5fb627bf1d36aa34f9fbe\",\"task_id\":\"b8fc0fe9-92d2-41cf-a2bd-cf1f95af49de\",\"tests\":[{\"command\":\"python -B ../../split-verifier.py automation ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py knowledge ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py lads ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py broker ../../qa-broker-current.json\",\"exit_code\":0,\"status\":\"completed\"}],\"unit_evidence_sha256\":\"453d2d38176410a87469a29cdb1104b1e5eb0821de4d0239cbd857f77a4c681e\",\"working_content_commit\":null}"
- component: "lads-stage3"
<!-- /lads-automation:event -->


## 2026-11-14 后续本机验收（源码待本轮发布门禁）

项目Hook现在优先由原生项目层加载；显式legacy-inline仅用于未发现项目层的旧CLI。控制端可通过lads_session的knowledge-project选择Registry中一个工程项目，启动stdio KnowledgeReader只读适配器（五工具、1–8条、输入16KB/输出32KB，2025-06-18/2025-11-25工具子集）。无HTTP监听、写接口或业务数据库访问，生产Worker默认MCP继续关闭。原生Hook与实际Codex/DeepSeek exec工具兼容的本轮证据见[验收记录](POST_MIGRATION_ACCEPTANCE_20261114.md)。用户选择ChatGPT Sites后已核实既有public站点及owner权限，尚未更新线上版本；Python Notion代理不能静态托管。物理运行根仍未变，兼容提案单列。

<!-- lads-automation:event {"doc":"docs/ARCHITECTURE.md","id":"31e7a644-3f65-54d6-906e-ea0b8c8b7042","payload_sha256":"5030a7765c1724d1404607e97cf89436387640bd52f22f144d401c7990ff3fb1"} -->
### LADS event 31e7a644-3f65-54d6-906e-ea0b8c8b7042

- Task: `1b2d853f-738a-4866-ac68-144befa29d71`
- Event: `architecture_changed`
- Recorded: `2026-11-14T16:42:32.447834Z`
- QA/review verified: `true` (machine delivery receipt `{"acceptance_mode":"independent","authorization_sha256":null,"candidate_sha256":"8d61330a7029010fe79d38d8f35e9585732e684393f85e693891922e1e64dbc1","controller_sha256":"bea69821287edd671cd0018a14c98e5e9cee2b1c30b28b3ded8e5a07902c4d3e","qa_sha256":"4071df2acec492025ee5ea311c3da3458bbdd0067a7dd77b97c5c424d8bafe2b","reviewer_sha256":"78d5e244439bb7cb100f9767e08c0f34f01a4845570470bb34b0167f7df7278f","simulated":false}`)
- summary: "Stage 3 delivery event: architecture_changed"
- reason: "Main-authored bounded Hook and readonly MCP implementation independently reviewed in full by actual DeepSeek roles; isolated 258+51+89 tests and 34 checks passed. Preserve blocked attempts and private probe failures. No business data, physical runtime migration or deployment."
- details: "{\"candidate_sha256\":\"8d61330a7029010fe79d38d8f35e9585732e684393f85e693891922e1e64dbc1\",\"changed_files\":[\"docs/POST_MIGRATION_ACK.md\"],\"changed_files_count\":1,\"changed_files_sha256\":\"3d7bd747fcd25ac8e408c677294bafa3125e6805cb079deb1121381960c7bf0e\",\"dirty\":true,\"observed_head\":\"c06473ee5a0d118be79f01224025e0400d65262d\",\"qa_contract_version\":3,\"reason\":\"Main-authored bounded Hook and readonly MCP implementation independently reviewed in full by actual DeepSeek roles; isolated 258+51+89 tests and 34 checks passed. Preserve blocked attempts and private probe failures. No business data, physical runtime migration or deployment.\",\"review\":{\"sha256\":\"78d5e244439bb7cb100f9767e08c0f34f01a4845570470bb34b0167f7df7278f\",\"status\":\"APPROVED\"},\"source_head\":\"c06473ee5a0d118be79f01224025e0400d65262d\",\"task_id\":\"1b2d853f-738a-4866-ac68-144befa29d71\",\"tests\":[{\"command\":\"python -B ../../split-verifier.py automation ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py knowledge ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py lads ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py broker ../../qa-broker-current.json\",\"exit_code\":0,\"status\":\"completed\"}],\"unit_evidence_sha256\":\"dfcaeed22d16626bb45c337ae22388072511ebe16e229557b26a1394f7b3868d\",\"working_content_commit\":null}"
- component: "lads-stage3"
<!-- /lads-automation:event -->


## 2026-11-15 已合并发布恢复

publish在合并前base漂移检查之前识别持久化精确PR的真实merged状态；通过既有原生合并证明、必需CI、当前保护与合并祖先校验后调用原知识回写。普通未合并PR保留原漂移门禁，轮询的OPEN/MERGED竞态只对同一精确PR重新验真。没有自动批准、推送main或放宽CI/身份/数据规则，详见[恢复记录](RELEASE_RECOVERY_20261115.md)。

<!-- lads-automation:event {"doc":"docs/ARCHITECTURE.md","id":"537d663d-677a-5887-817a-f66cc7612cf7","payload_sha256":"cdb72e0a05169519282dec0cd1cd77938662eec6cc4984a1ce6cdc963834eac2"} -->
### LADS event 537d663d-677a-5887-817a-f66cc7612cf7

- Task: `e383fe89-3300-4c4e-9196-f050093f89df`
- Event: `architecture_changed`
- Recorded: `2026-11-15T06:19:23.880800Z`
- QA/review verified: `true` (machine delivery receipt `{"acceptance_mode":"independent","authorization_sha256":null,"candidate_sha256":"e7d25bd455317210bc51a59ac5236865937314c9b5a92510051bd78dd2f7a895","controller_sha256":"ef529d53cc054c9dae67bdf334d03d01c940763a06673f631bb7c580647de892","qa_sha256":"b253e7912346f2cd667e8242167d815449b57c1326e4565c0e0395e27efd3509","reviewer_sha256":"888b913100476c7f3ce470eb3960524bb72f9ab4205f44ae21650eb00507a22f","simulated":false}`)
- summary: "Stage 3 delivery event: architecture_changed"
- reason: "Actual independent DeepSeek Scanner, plan/final Reviewer, Builder ACK and sealed full isolated QA approved the complete main-authored release recovery baseline. Preserve prior native-merge race, manual verified reconciliation, API errors and counters. Reconciliation still verifies native proof, source CI, protection and merge ancestry; ordinary base drift remains blocked. No user data, product features, credentials or production deployment change."
- details: "{\"candidate_sha256\":\"e7d25bd455317210bc51a59ac5236865937314c9b5a92510051bd78dd2f7a895\",\"changed_files\":[\"docs/RELEASE_RECOVERY_ACK.md\"],\"changed_files_count\":1,\"changed_files_sha256\":\"1de14403ca1c93542246f203e16a168bf4dc9a19dc8a3320922c9aaa8c6f1f5c\",\"dirty\":true,\"observed_head\":\"7b7b16fbaf94b545b7e9df3b8ea368d1a7e9bf42\",\"qa_contract_version\":3,\"reason\":\"Actual independent DeepSeek Scanner, plan/final Reviewer, Builder ACK and sealed full isolated QA approved the complete main-authored release recovery baseline. Preserve prior native-merge race, manual verified reconciliation, API errors and counters. Reconciliation still verifies native proof, source CI, protection and merge ancestry; ordinary base drift remains blocked. No user data, product features, credentials or production deployment change.\",\"review\":{\"sha256\":\"888b913100476c7f3ce470eb3960524bb72f9ab4205f44ae21650eb00507a22f\",\"status\":\"APPROVED\"},\"source_head\":\"7b7b16fbaf94b545b7e9df3b8ea368d1a7e9bf42\",\"task_id\":\"e383fe89-3300-4c4e-9196-f050093f89df\",\"tests\":[{\"command\":\"python -B ../../split-verifier.py automation ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py knowledge ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py lads ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py broker ../../qa-broker-current.json\",\"exit_code\":0,\"status\":\"completed\"}],\"unit_evidence_sha256\":\"9b59ea17d167c6a999694ffc0d226b2f1e9e73cff17595cbf505d44a3b4d1dd4\",\"working_content_commit\":null}"
- component: "lads-stage3"
<!-- /lads-automation:event -->


## 2026-11-23 LADS Control Center 第一阶段

本轮仅建立 React/TypeScript 总览与 FastAPI 本机只读接口，沿用既有工程知识库。首轮独立 Builder 在任务5f992691-77b9-403b-9dd7-c2a59c11a8b8中产出初稿，因超时及临时Git树被门禁BLOCKED，原记录保留、不追认为通过。主控在待审候选补齐基础依赖、Windows资源MIME类型与venv重定向器子PID归属校验，加入回归测试。主控已实际完成隔离构建/API/浏览器及Windows启停测试；独立审查、正式QA、提交和PR以本轮后续机器回执为准。Agent心跳、事件入库、实际费用、远程GitHub监控、完整知识库页及第二至八阶段未完成。产品代码、个人数据及原工程库结构均未改变。

当前启动入口见[控制中心说明](CONTROL_CENTER.md)。依赖、构建、日志与PID文件仅放LADS-Workspace/control-center-data；第一阶段不创建新数据库，也不执行数据库迁移。

<!-- lads-automation:event {"doc":"docs/ARCHITECTURE.md","id":"8c2c5803-8ec1-50d4-a09a-8b7b9011c84d","payload_sha256":"ebcdf309b82e0561c8b9727288279651fa2f450accf858c68cdc490e3ecd3eda"} -->
### LADS event 8c2c5803-8ec1-50d4-a09a-8b7b9011c84d

- Task: `6858a4a8-36c8-42f0-91b3-bd0e14849991`
- Event: `architecture_changed`
- Recorded: `2026-11-23T08:12:49.976150Z`
- QA/review verified: `true` (machine delivery receipt `{"acceptance_mode":"independent","authorization_sha256":null,"candidate_sha256":"c231f130635567b54a1a9a3a9d47d2860927d0d2856665702684ea15458d1b94","controller_sha256":"93657fd9beb4ed053d055f41c1fd924332f84df6697988e66b0a407e993227b3","qa_sha256":"5c0c7a80413d73afab0cd40060ce129567bd677e378a6d189d9c214e2d2006b7","reviewer_sha256":"c60b4b49c6a4e6b82a7ecdcb00ddab2cd7fde8f81f8666f7d4d4cf6b0ac0e248","simulated":false}`)
- summary: "Stage 3 delivery event: architecture_changed"
- reason: "Control Center Stage1 only: complete prepared baseline independently reviewed, real sealed QA 278+51+89 tests and 34 checks PASS. Main supplemental UI/API 15 checks, 11 new cases, build and Windows lifecycle separately evidenced. DeepSeek initial draft plus main Windows fixes; prior two BLOCKED runs/counters retained. No product data, existing schema, worker runtime, heartbeat, billing, GitHub monitor or production deployment changes."
- details: "{\"candidate_sha256\":\"c231f130635567b54a1a9a3a9d47d2860927d0d2856665702684ea15458d1b94\",\"changed_files\":[\"docs/CONTROL_CENTER_ACK.md\"],\"changed_files_count\":1,\"changed_files_sha256\":\"e449171af52f56b400d803607ae7cdc1c95312ff43b096c40bbb1ebb4c023d3f\",\"dirty\":true,\"observed_head\":\"2a90194c5da48c1d45b219ff924ce266868c348c\",\"qa_contract_version\":3,\"reason\":\"Control Center Stage1 only: complete prepared baseline independently reviewed, real sealed QA 278+51+89 tests and 34 checks PASS. Main supplemental UI/API 15 checks, 11 new cases, build and Windows lifecycle separately evidenced. DeepSeek initial draft plus main Windows fixes; prior two BLOCKED runs/counters retained. No product data, existing schema, worker runtime, heartbeat, billing, GitHub monitor or production deployment changes.\",\"review\":{\"sha256\":\"c60b4b49c6a4e6b82a7ecdcb00ddab2cd7fde8f81f8666f7d4d4cf6b0ac0e248\",\"status\":\"APPROVED\"},\"source_head\":\"2a90194c5da48c1d45b219ff924ce266868c348c\",\"task_id\":\"6858a4a8-36c8-42f0-91b3-bd0e14849991\",\"tests\":[{\"command\":\"python -B ../../split-verifier.py automation ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py knowledge ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py lads ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py broker ../../qa-broker-current.json\",\"exit_code\":0,\"status\":\"completed\"}],\"unit_evidence_sha256\":\"783e25534a148d2ab8b36f8ed5be34cd55dc102c1c2ec53028b2c5e4724ce170\",\"working_content_commit\":null}"
- component: "lads-stage3"
<!-- /lads-automation:event -->
