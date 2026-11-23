# 工程工具链

Python3.14、SQLAlchemy2.0.55、Alembic1.20.1、SQLite FTS5；确切依赖见 requirements-lads.txt。本轮 Codex CLI 实测0.167.0-alpha.4.1（旧0.166仅作历史）；不自动升级或改用户主控配置。Git/GitHub CLI 用现有安全认证。

测试：tests/automation、tests/knowledge、tests/lads；产品实物哈希集成需要单独产品根。GitHub runner 不注入 DeepSeek 密钥，不运行不可信高权限自托管 runner。Actions 固定到已核实提交，GITHUB_TOKEN 默认 contents:read。

<!-- lads-automation:event {"doc":"docs/CODEX_TECH_STACK_AND_TOOLING.md","id":"ba9a1574-24dd-5644-b53c-045db4b064c5","payload_sha256":"46265dde44901732cf35abd8df81f60c56a57b67109375b3f407dacc3f5afc8e"} -->
### LADS event ba9a1574-24dd-5644-b53c-045db4b064c5

- Task: `b8fc0fe9-92d2-41cf-a2bd-cf1f95af49de`
- Event: `toolchain_changed`
- Recorded: `2026-11-13T15:31:35.213552Z`
- QA/review verified: `true` (machine delivery receipt `{"acceptance_mode":"independent","authorization_sha256":null,"candidate_sha256":"733febae71fe949431a2237832af23ec533eccd920b6bd2e1a377309b0377df5","controller_sha256":"c32e0e25b828c757ee378226b49a76cc827cfcd5594fb7ced00b5edb663c672e","qa_sha256":"7f8fec5c65868d9cc5c36d006389900550cda4246f97587a0a4943a0626268b8","reviewer_sha256":"0cae6211238e41c223a3096927d420ec7f3cde5fb94e0f1b0b988eae060c4a14","simulated":false}`)
- summary: "Stage 3 delivery event: toolchain_changed"
- reason: "User-authorized main-authored dual-repository engineering migration, independently reviewed complete baseline and ACK. Actual v3 QA passed 255 automation, 38 knowledge, 89 LADS and product broker checks; account authentication and AI remain unimplemented. Private runtime locations are explicitly documented without claiming physical relocation. Product PR5 merged; DevTeam publication not yet performed. Original failed attempts remain blocked and archived."
- details: "{\"candidate_sha256\":\"733febae71fe949431a2237832af23ec533eccd920b6bd2e1a377309b0377df5\",\"changed_files\":[\"docs/MIGRATION_ACK.md\"],\"changed_files_count\":1,\"changed_files_sha256\":\"4aad177b74e771a90935ac4d6657b1087ef7dd1bc1d2d12fcd3a32e389f51c26\",\"dirty\":true,\"observed_head\":\"2c27792a33c31363d1a5fb627bf1d36aa34f9fbe\",\"qa_contract_version\":3,\"reason\":\"User-authorized main-authored dual-repository engineering migration, independently reviewed complete baseline and ACK. Actual v3 QA passed 255 automation, 38 knowledge, 89 LADS and product broker checks; account authentication and AI remain unimplemented. Private runtime locations are explicitly documented without claiming physical relocation. Product PR5 merged; DevTeam publication not yet performed. Original failed attempts remain blocked and archived.\",\"review\":{\"sha256\":\"0cae6211238e41c223a3096927d420ec7f3cde5fb94e0f1b0b988eae060c4a14\",\"status\":\"APPROVED\"},\"source_head\":\"2c27792a33c31363d1a5fb627bf1d36aa34f9fbe\",\"task_id\":\"b8fc0fe9-92d2-41cf-a2bd-cf1f95af49de\",\"tests\":[{\"command\":\"python -B ../../split-verifier.py automation ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py knowledge ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py lads ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py broker ../../qa-broker-current.json\",\"exit_code\":0,\"status\":\"completed\"}],\"unit_evidence_sha256\":\"453d2d38176410a87469a29cdb1104b1e5eb0821de4d0239cbd857f77a4c681e\",\"working_content_commit\":null}"
- component: "lads-stage3"
<!-- /lads-automation:event -->


## 2026-11-14 实测补充

CLI0.167.0-alpha.4.1已能发现项目hooks.json；不要再默认重复内联。新增stdio只读知识MCP复用现有Python/KnowledgeReader，无新依赖。实际Codex mcpServer/tool/call及Scanner独立profile exec的MCP工具调用通过。该CLI不支持 --profile 与 app-server 组合，DeepSeek继续使用独立profile的codex exec；不得因参数错误切GPT。原生探测使用LADS_PYTHON与现有Windows代理，不动主配置。兼容协议与验收边界见POST_MIGRATION_ACCEPTANCE_20261114.md。

<!-- lads-automation:event {"doc":"docs/CODEX_TECH_STACK_AND_TOOLING.md","id":"234e271d-fbdd-5644-be8c-ae05b34a5ddd","payload_sha256":"6abb2da0da92cb7990b4304ece24bf639e90f2cae1d6fb81a6dc22936dd74b0b"} -->
### LADS event 234e271d-fbdd-5644-be8c-ae05b34a5ddd

- Task: `1b2d853f-738a-4866-ac68-144befa29d71`
- Event: `toolchain_changed`
- Recorded: `2026-11-14T16:42:32.524086Z`
- QA/review verified: `true` (machine delivery receipt `{"acceptance_mode":"independent","authorization_sha256":null,"candidate_sha256":"8d61330a7029010fe79d38d8f35e9585732e684393f85e693891922e1e64dbc1","controller_sha256":"bea69821287edd671cd0018a14c98e5e9cee2b1c30b28b3ded8e5a07902c4d3e","qa_sha256":"4071df2acec492025ee5ea311c3da3458bbdd0067a7dd77b97c5c424d8bafe2b","reviewer_sha256":"78d5e244439bb7cb100f9767e08c0f34f01a4845570470bb34b0167f7df7278f","simulated":false}`)
- summary: "Stage 3 delivery event: toolchain_changed"
- reason: "Main-authored bounded Hook and readonly MCP implementation independently reviewed in full by actual DeepSeek roles; isolated 258+51+89 tests and 34 checks passed. Preserve blocked attempts and private probe failures. No business data, physical runtime migration or deployment."
- details: "{\"candidate_sha256\":\"8d61330a7029010fe79d38d8f35e9585732e684393f85e693891922e1e64dbc1\",\"changed_files\":[\"docs/POST_MIGRATION_ACK.md\"],\"changed_files_count\":1,\"changed_files_sha256\":\"3d7bd747fcd25ac8e408c677294bafa3125e6805cb079deb1121381960c7bf0e\",\"dirty\":true,\"observed_head\":\"c06473ee5a0d118be79f01224025e0400d65262d\",\"qa_contract_version\":3,\"reason\":\"Main-authored bounded Hook and readonly MCP implementation independently reviewed in full by actual DeepSeek roles; isolated 258+51+89 tests and 34 checks passed. Preserve blocked attempts and private probe failures. No business data, physical runtime migration or deployment.\",\"review\":{\"sha256\":\"78d5e244439bb7cb100f9767e08c0f34f01a4845570470bb34b0167f7df7278f\",\"status\":\"APPROVED\"},\"source_head\":\"c06473ee5a0d118be79f01224025e0400d65262d\",\"task_id\":\"1b2d853f-738a-4866-ac68-144befa29d71\",\"tests\":[{\"command\":\"python -B ../../split-verifier.py automation ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py knowledge ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py lads ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py broker ../../qa-broker-current.json\",\"exit_code\":0,\"status\":\"completed\"}],\"unit_evidence_sha256\":\"dfcaeed22d16626bb45c337ae22388072511ebe16e229557b26a1394f7b3868d\",\"working_content_commit\":null}"
- component: "lads-stage3"
<!-- /lads-automation:event -->

<!-- lads-automation:event {"doc":"docs/CODEX_TECH_STACK_AND_TOOLING.md","id":"e60d1512-e331-5611-b64c-9a4096d45873","payload_sha256":"b25b1c198d083143ea8bdba4e85f17f63c315874df94f8d083c7dd394554132a"} -->
### LADS event e60d1512-e331-5611-b64c-9a4096d45873

- Task: `6858a4a8-36c8-42f0-91b3-bd0e14849991`
- Event: `toolchain_changed`
- Recorded: `2026-11-23T08:12:50.033055Z`
- QA/review verified: `true` (machine delivery receipt `{"acceptance_mode":"independent","authorization_sha256":null,"candidate_sha256":"c231f130635567b54a1a9a3a9d47d2860927d0d2856665702684ea15458d1b94","controller_sha256":"93657fd9beb4ed053d055f41c1fd924332f84df6697988e66b0a407e993227b3","qa_sha256":"5c0c7a80413d73afab0cd40060ce129567bd677e378a6d189d9c214e2d2006b7","reviewer_sha256":"c60b4b49c6a4e6b82a7ecdcb00ddab2cd7fde8f81f8666f7d4d4cf6b0ac0e248","simulated":false}`)
- summary: "Stage 3 delivery event: toolchain_changed"
- reason: "Control Center Stage1 only: complete prepared baseline independently reviewed, real sealed QA 278+51+89 tests and 34 checks PASS. Main supplemental UI/API 15 checks, 11 new cases, build and Windows lifecycle separately evidenced. DeepSeek initial draft plus main Windows fixes; prior two BLOCKED runs/counters retained. No product data, existing schema, worker runtime, heartbeat, billing, GitHub monitor or production deployment changes."
- details: "{\"candidate_sha256\":\"c231f130635567b54a1a9a3a9d47d2860927d0d2856665702684ea15458d1b94\",\"changed_files\":[\"docs/CONTROL_CENTER_ACK.md\"],\"changed_files_count\":1,\"changed_files_sha256\":\"e449171af52f56b400d803607ae7cdc1c95312ff43b096c40bbb1ebb4c023d3f\",\"dirty\":true,\"observed_head\":\"2a90194c5da48c1d45b219ff924ce266868c348c\",\"qa_contract_version\":3,\"reason\":\"Control Center Stage1 only: complete prepared baseline independently reviewed, real sealed QA 278+51+89 tests and 34 checks PASS. Main supplemental UI/API 15 checks, 11 new cases, build and Windows lifecycle separately evidenced. DeepSeek initial draft plus main Windows fixes; prior two BLOCKED runs/counters retained. No product data, existing schema, worker runtime, heartbeat, billing, GitHub monitor or production deployment changes.\",\"review\":{\"sha256\":\"c60b4b49c6a4e6b82a7ecdcb00ddab2cd7fde8f81f8666f7d4d4cf6b0ac0e248\",\"status\":\"APPROVED\"},\"source_head\":\"2a90194c5da48c1d45b219ff924ce266868c348c\",\"task_id\":\"6858a4a8-36c8-42f0-91b3-bd0e14849991\",\"tests\":[{\"command\":\"python -B ../../split-verifier.py automation ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py knowledge ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py lads ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py broker ../../qa-broker-current.json\",\"exit_code\":0,\"status\":\"completed\"}],\"unit_evidence_sha256\":\"783e25534a148d2ab8b36f8ed5be34cd55dc102c1c2ec53028b2c5e4724ce170\",\"working_content_commit\":null}"
- component: "lads-stage3"
<!-- /lads-automation:event -->
