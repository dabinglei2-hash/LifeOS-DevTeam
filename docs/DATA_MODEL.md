# 工程数据模型

既有迁移0001/0002保持：projects、dev_tasks、task_events、agent_runs、code_changes、test_runs、architecture_decisions、known_issues、knowledge_items、model_usage；另有 FTS5。project_id 是所有检索与关联边界。

Registry 使用两个不同 UUID 和 lads.lifeos / lads.devteam 命名空间，实际数据库放 LADS-Workspace/knowledge/dual-repository.sqlite3。只复制已验证工程数据库，不接触浏览器、Notion业务SQLite或用户JSON。旧项目和记录不删除、不改 root_ref；新根注册为新身份。

Schema、迁移、恢复和检索代码提交 Git；SQLite、备份、运行日志、成本明细及角色密钥不提交。

<!-- lads-automation:event {"doc":"docs/DATA_MODEL.md","id":"7ffc9b7e-6f94-5f44-b2d4-9630c35f83a8","payload_sha256":"7a692d49cfcedf285e9218151ad068458630d2c3f6aa29ac1e10cb5de3322cc1"} -->
### LADS event 7ffc9b7e-6f94-5f44-b2d4-9630c35f83a8

- Task: `b8fc0fe9-92d2-41cf-a2bd-cf1f95af49de`
- Event: `data_model_changed`
- Recorded: `2026-11-13T15:31:35.068130Z`
- QA/review verified: `true` (machine delivery receipt `{"acceptance_mode":"independent","authorization_sha256":null,"candidate_sha256":"733febae71fe949431a2237832af23ec533eccd920b6bd2e1a377309b0377df5","controller_sha256":"c32e0e25b828c757ee378226b49a76cc827cfcd5594fb7ced00b5edb663c672e","qa_sha256":"7f8fec5c65868d9cc5c36d006389900550cda4246f97587a0a4943a0626268b8","reviewer_sha256":"0cae6211238e41c223a3096927d420ec7f3cde5fb94e0f1b0b988eae060c4a14","simulated":false}`)
- summary: "Stage 3 delivery event: data_model_changed"
- reason: "User-authorized main-authored dual-repository engineering migration, independently reviewed complete baseline and ACK. Actual v3 QA passed 255 automation, 38 knowledge, 89 LADS and product broker checks; account authentication and AI remain unimplemented. Private runtime locations are explicitly documented without claiming physical relocation. Product PR5 merged; DevTeam publication not yet performed. Original failed attempts remain blocked and archived."
- details: "{\"candidate_sha256\":\"733febae71fe949431a2237832af23ec533eccd920b6bd2e1a377309b0377df5\",\"changed_files\":[\"docs/MIGRATION_ACK.md\"],\"changed_files_count\":1,\"changed_files_sha256\":\"4aad177b74e771a90935ac4d6657b1087ef7dd1bc1d2d12fcd3a32e389f51c26\",\"dirty\":true,\"observed_head\":\"2c27792a33c31363d1a5fb627bf1d36aa34f9fbe\",\"qa_contract_version\":3,\"reason\":\"User-authorized main-authored dual-repository engineering migration, independently reviewed complete baseline and ACK. Actual v3 QA passed 255 automation, 38 knowledge, 89 LADS and product broker checks; account authentication and AI remain unimplemented. Private runtime locations are explicitly documented without claiming physical relocation. Product PR5 merged; DevTeam publication not yet performed. Original failed attempts remain blocked and archived.\",\"review\":{\"sha256\":\"0cae6211238e41c223a3096927d420ec7f3cde5fb94e0f1b0b988eae060c4a14\",\"status\":\"APPROVED\"},\"source_head\":\"2c27792a33c31363d1a5fb627bf1d36aa34f9fbe\",\"task_id\":\"b8fc0fe9-92d2-41cf-a2bd-cf1f95af49de\",\"tests\":[{\"command\":\"python -B ../../split-verifier.py automation ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py knowledge ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py lads ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py broker ../../qa-broker-current.json\",\"exit_code\":0,\"status\":\"completed\"}],\"unit_evidence_sha256\":\"453d2d38176410a87469a29cdb1104b1e5eb0821de4d0239cbd857f77a4c681e\",\"working_content_commit\":null}"
- component: "lads-stage3"
<!-- /lads-automation:event -->
