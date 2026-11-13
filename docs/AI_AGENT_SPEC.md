# 工程角色与权限

主控 GPT/OpenAI；Scanner、Reviewer、Builder、QA Tester 经独立 CLI profile 使用 lifeos_deepseek / deepseek-flash。四个专用环境变量分别 DEEPSEEK_SCANNER_API_KEY、DEEPSEEK_REVIEWER_API_KEY、DEEPSEEK_BUILDER_API_KEY、DEEPSEEK_QA_API_KEY。仅记录变量名，不记录值。

Scanner/Reviewer/QA 实际 read-only/never；Builder 隔离 worktree/workspace-write。QA 由可信隔离执行器产生回执，只读模型独立核验。APPROVED 不等于部署授权，主控测试不等于独立审批。Provider 失败不得切换 GPT 或改变原失败计数。

<!-- lads-automation:event {"doc":"docs/AI_AGENT_SPEC.md","id":"1076d0a2-b37b-5b53-8e67-eb473b0b19c7","payload_sha256":"6ae5f7501f450ed8f661e441a40e90be55ac5557a837c7bb61444591570b9b81"} -->
### LADS event 1076d0a2-b37b-5b53-8e67-eb473b0b19c7

- Task: `b8fc0fe9-92d2-41cf-a2bd-cf1f95af49de`
- Event: `agent_rules_changed`
- Recorded: `2026-11-13T15:31:35.130948Z`
- QA/review verified: `true` (machine delivery receipt `{"acceptance_mode":"independent","authorization_sha256":null,"candidate_sha256":"733febae71fe949431a2237832af23ec533eccd920b6bd2e1a377309b0377df5","controller_sha256":"c32e0e25b828c757ee378226b49a76cc827cfcd5594fb7ced00b5edb663c672e","qa_sha256":"7f8fec5c65868d9cc5c36d006389900550cda4246f97587a0a4943a0626268b8","reviewer_sha256":"0cae6211238e41c223a3096927d420ec7f3cde5fb94e0f1b0b988eae060c4a14","simulated":false}`)
- summary: "Stage 3 delivery event: agent_rules_changed"
- reason: "User-authorized main-authored dual-repository engineering migration, independently reviewed complete baseline and ACK. Actual v3 QA passed 255 automation, 38 knowledge, 89 LADS and product broker checks; account authentication and AI remain unimplemented. Private runtime locations are explicitly documented without claiming physical relocation. Product PR5 merged; DevTeam publication not yet performed. Original failed attempts remain blocked and archived."
- details: "{\"candidate_sha256\":\"733febae71fe949431a2237832af23ec533eccd920b6bd2e1a377309b0377df5\",\"changed_files\":[\"docs/MIGRATION_ACK.md\"],\"changed_files_count\":1,\"changed_files_sha256\":\"4aad177b74e771a90935ac4d6657b1087ef7dd1bc1d2d12fcd3a32e389f51c26\",\"dirty\":true,\"observed_head\":\"2c27792a33c31363d1a5fb627bf1d36aa34f9fbe\",\"qa_contract_version\":3,\"reason\":\"User-authorized main-authored dual-repository engineering migration, independently reviewed complete baseline and ACK. Actual v3 QA passed 255 automation, 38 knowledge, 89 LADS and product broker checks; account authentication and AI remain unimplemented. Private runtime locations are explicitly documented without claiming physical relocation. Product PR5 merged; DevTeam publication not yet performed. Original failed attempts remain blocked and archived.\",\"review\":{\"sha256\":\"0cae6211238e41c223a3096927d420ec7f3cde5fb94e0f1b0b988eae060c4a14\",\"status\":\"APPROVED\"},\"source_head\":\"2c27792a33c31363d1a5fb627bf1d36aa34f9fbe\",\"task_id\":\"b8fc0fe9-92d2-41cf-a2bd-cf1f95af49de\",\"tests\":[{\"command\":\"python -B ../../split-verifier.py automation ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py knowledge ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py lads ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py broker ../../qa-broker-current.json\",\"exit_code\":0,\"status\":\"completed\"}],\"unit_evidence_sha256\":\"453d2d38176410a87469a29cdb1104b1e5eb0821de4d0239cbd857f77a4c681e\",\"working_content_commit\":null}"
- component: "lads-stage3"
<!-- /lads-automation:event -->
