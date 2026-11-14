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
