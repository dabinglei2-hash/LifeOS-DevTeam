# 已知问题

- 原 main 的浏览器编辑等待失败已在本地主控验收修复，独立发布未通过。
- 原生桌面 Hook 热加载、产品真实多用户、物理 iPhone 及真实 Notion 未在本轮验证。
- 双仓库发布门禁及部署适配的实际剩余事项见 [迁移报告](DUAL_REPOSITORY_MIGRATION_REPORT.md)。

## 2026-11-13 研发预检与回执修复

产品任务 f1b9ba0f-f123-480b-a402-a76568fa7c00 已完成独立 Scanner、PLAN Reviewer、Builder确认文档、实际隔离 v3 QA、FINAL Reviewer APPROVED、MAIN-ACK 和文档/知识回写。候选 6c000eff2750ccbfc751adbf412b66db65cbb7be1d20ad63044ee4a451c87387，实际245+38+89及产品14项浏览器通过。一次 Reviewer 流连接失败单独记录，代码返工为0，不切换GPT；原失败保留。产品发布本地门禁目前已通过，尚未提交或推送。

研发首次任务 3e5b3b6e-241e-4432-970c-a8a5b05ce25a 被独立 Scanner 正确 BLOCKED：Git已初始化但HEAD不存在时原Registry返回None，flow未拒绝。主控已修复为必须解析有效 HEAD commit；空源码安全扫描也改为拒绝。13项Registry和7项安全测试实际通过，含空仓库/正确origin拒绝及存在commit正例。Scanner原报告和计数不改写，修复尚待新的独立审批。

产品发布门禁曾被Agent上下文裁剪阻断：get_task_history返回8KB上限上下文，裁掉部分真实文档回执。新增控制器专用get_release_receipts，仅返回固定证据字段，逐列表最多20、总计64KB，超限拒绝、不静默丢弃，不增加Agent上下文或读取产品数据。门禁按最新记录顺序保留当前证明，旧失败不覆盖它。新增6项真实内存数据库测试、既有67项发布测试全部通过；实际产品七份文档及四条QA证明已由该只读接口重验通过。数据库schema未变，无用户业务记录读取。

迁移实现与修复由主控完成；独立审批只以真实工作进程报告为准。下一步：对更新后的研发完整候选重新执行四角色与隔离QA，然后才推进两仓库PR、CI和原生Auto-merge。不部署。

<!-- lads-automation:event {"doc":"docs/KNOWN_ISSUES.md","id":"1b2b8a34-fdee-5362-a881-b677dbca6257","payload_sha256":"90a9c303d65d92d5a45095431f32fe99adb3990f4564d6d95a0233a9632f48ee"} -->
### LADS event 1b2b8a34-fdee-5362-a881-b677dbca6257

- Task: `b8fc0fe9-92d2-41cf-a2bd-cf1f95af49de`
- Event: `issue_found`
- Recorded: `2026-11-13T15:31:35.403957Z`
- QA/review verified: `true` (machine delivery receipt `{"acceptance_mode":"independent","authorization_sha256":null,"candidate_sha256":"733febae71fe949431a2237832af23ec533eccd920b6bd2e1a377309b0377df5","controller_sha256":"c32e0e25b828c757ee378226b49a76cc827cfcd5594fb7ced00b5edb663c672e","qa_sha256":"7f8fec5c65868d9cc5c36d006389900550cda4246f97587a0a4943a0626268b8","reviewer_sha256":"0cae6211238e41c223a3096927d420ec7f3cde5fb94e0f1b0b988eae060c4a14","simulated":false}`)
- summary: "Stage 3 delivery event: issue_found"
- reason: "User-authorized main-authored dual-repository engineering migration, independently reviewed complete baseline and ACK. Actual v3 QA passed 255 automation, 38 knowledge, 89 LADS and product broker checks; account authentication and AI remain unimplemented. Private runtime locations are explicitly documented without claiming physical relocation. Product PR5 merged; DevTeam publication not yet performed. Original failed attempts remain blocked and archived."
- details: "{\"candidate_sha256\":\"733febae71fe949431a2237832af23ec533eccd920b6bd2e1a377309b0377df5\",\"changed_files\":[\"docs/MIGRATION_ACK.md\"],\"changed_files_count\":1,\"changed_files_sha256\":\"4aad177b74e771a90935ac4d6657b1087ef7dd1bc1d2d12fcd3a32e389f51c26\",\"dirty\":true,\"observed_head\":\"2c27792a33c31363d1a5fb627bf1d36aa34f9fbe\",\"qa_contract_version\":3,\"reason\":\"User-authorized main-authored dual-repository engineering migration, independently reviewed complete baseline and ACK. Actual v3 QA passed 255 automation, 38 knowledge, 89 LADS and product broker checks; account authentication and AI remain unimplemented. Private runtime locations are explicitly documented without claiming physical relocation. Product PR5 merged; DevTeam publication not yet performed. Original failed attempts remain blocked and archived.\",\"review\":{\"sha256\":\"0cae6211238e41c223a3096927d420ec7f3cde5fb94e0f1b0b988eae060c4a14\",\"status\":\"APPROVED\"},\"source_head\":\"2c27792a33c31363d1a5fb627bf1d36aa34f9fbe\",\"task_id\":\"b8fc0fe9-92d2-41cf-a2bd-cf1f95af49de\",\"tests\":[{\"command\":\"python -B ../../split-verifier.py automation ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py knowledge ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py lads ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py broker ../../qa-broker-current.json\",\"exit_code\":0,\"status\":\"completed\"}],\"unit_evidence_sha256\":\"453d2d38176410a87469a29cdb1104b1e5eb0821de4d0239cbd857f77a4c681e\",\"working_content_commit\":null}"
- component: "lads-stage3"
<!-- /lads-automation:event -->


## 2026-11-15 原生合并收尾竞态

PR #4曾已合并但本地普通收尾/恢复失败；旧错误与计数保留，事后回写经过全部原证据重验。主控最小修复已补精确已合并PR恢复及REST/GraphQL竞态处理，新增九项合成测试；独立验收/远端结果以本轮回执为准，详见[恢复记录](RELEASE_RECOVERY_20261115.md)。

<!-- lads-automation:event {"doc":"docs/KNOWN_ISSUES.md","id":"fdb80b69-8c10-5e98-92fb-b8a56be6e47c","payload_sha256":"12c7e2709e8d171df14703351fd931b40fea4760dce946a42cc7f68851c39026"} -->
### LADS event fdb80b69-8c10-5e98-92fb-b8a56be6e47c

- Task: `e383fe89-3300-4c4e-9196-f050093f89df`
- Event: `issue_found`
- Recorded: `2026-11-15T06:19:24.020777Z`
- QA/review verified: `true` (machine delivery receipt `{"acceptance_mode":"independent","authorization_sha256":null,"candidate_sha256":"e7d25bd455317210bc51a59ac5236865937314c9b5a92510051bd78dd2f7a895","controller_sha256":"ef529d53cc054c9dae67bdf334d03d01c940763a06673f631bb7c580647de892","qa_sha256":"b253e7912346f2cd667e8242167d815449b57c1326e4565c0e0395e27efd3509","reviewer_sha256":"888b913100476c7f3ce470eb3960524bb72f9ab4205f44ae21650eb00507a22f","simulated":false}`)
- summary: "Stage 3 delivery event: issue_found"
- reason: "Actual independent DeepSeek Scanner, plan/final Reviewer, Builder ACK and sealed full isolated QA approved the complete main-authored release recovery baseline. Preserve prior native-merge race, manual verified reconciliation, API errors and counters. Reconciliation still verifies native proof, source CI, protection and merge ancestry; ordinary base drift remains blocked. No user data, product features, credentials or production deployment change."
- details: "{\"candidate_sha256\":\"e7d25bd455317210bc51a59ac5236865937314c9b5a92510051bd78dd2f7a895\",\"changed_files\":[\"docs/RELEASE_RECOVERY_ACK.md\"],\"changed_files_count\":1,\"changed_files_sha256\":\"1de14403ca1c93542246f203e16a168bf4dc9a19dc8a3320922c9aaa8c6f1f5c\",\"dirty\":true,\"observed_head\":\"7b7b16fbaf94b545b7e9df3b8ea368d1a7e9bf42\",\"qa_contract_version\":3,\"reason\":\"Actual independent DeepSeek Scanner, plan/final Reviewer, Builder ACK and sealed full isolated QA approved the complete main-authored release recovery baseline. Preserve prior native-merge race, manual verified reconciliation, API errors and counters. Reconciliation still verifies native proof, source CI, protection and merge ancestry; ordinary base drift remains blocked. No user data, product features, credentials or production deployment change.\",\"review\":{\"sha256\":\"888b913100476c7f3ce470eb3960524bb72f9ab4205f44ae21650eb00507a22f\",\"status\":\"APPROVED\"},\"source_head\":\"7b7b16fbaf94b545b7e9df3b8ea368d1a7e9bf42\",\"task_id\":\"e383fe89-3300-4c4e-9196-f050093f89df\",\"tests\":[{\"command\":\"python -B ../../split-verifier.py automation ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py knowledge ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py lads ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py broker ../../qa-broker-current.json\",\"exit_code\":0,\"status\":\"completed\"}],\"unit_evidence_sha256\":\"9b59ea17d167c6a999694ffc0d226b2f1e9e73cff17595cbf505d44a3b4d1dd4\",\"working_content_commit\":null}"
- component: "lads-stage3"
<!-- /lads-automation:event -->

<!-- lads-automation:event {"doc":"docs/KNOWN_ISSUES.md","id":"24ee8ea1-9f97-5108-80fd-3c2efaf1fff1","payload_sha256":"c80418691fef8b72ee4db2724f09a277ee70a930e9b5ba35f64bcd260633f017"} -->
### LADS event 24ee8ea1-9f97-5108-80fd-3c2efaf1fff1

- Task: `decef281-1bfa-4ec9-b8ac-93daf86d8451`
- Event: `issue_found`
- Recorded: `2026-11-23T15:13:14.232442Z`
- QA/review verified: `true` (machine delivery receipt `{"acceptance_mode":"independent","authorization_sha256":null,"candidate_sha256":"7cac0ff9abb472dd93026f3f601bb54ba67d1ebd17d98cb4afe39a5c7bb8cd18","controller_sha256":"75cd17c0961c6211948ef9ef1233600b282bd3ca808410541a75a6c3b5cdfcb6","qa_sha256":"6ab99358b2b5b341ed4becb66aa202212e03f4d519b08b0fa8b6e0f85e7d6c29","reviewer_sha256":"b886741cdb579851c88ee2db4d64668f98cdd5eb3ca8af36485d3f5bbea9155c","simulated":false}`)
- summary: "Stage 3 delivery event: issue_found"
- reason: "Main-authored Control Center stages2-8 baseline actually reviewed by all four independent DeepSeek roles. Sealed QA passed; main 299 automation, 51 knowledge,89 LADS,23 UI/API and v2 synthetic heartbeat/SSE/restore separately evidenced. Two connection retries and cancelled old candidate retained. Explicit --monitor protects tests; only v2 sources trusted. GPT desktop heartbeat, supplier invoice/request count are UNKNOWN. Live unreconciled usage preview is project-scoped; advanced agent/model/task/date filters apply to indexed historical records, not this supplemental recent-event preview (non-blocking limitation). No product/old KB schema/login/production deployment changes; publication still subject to GitHub gates."
- details: "{\"candidate_sha256\":\"7cac0ff9abb472dd93026f3f601bb54ba67d1ebd17d98cb4afe39a5c7bb8cd18\",\"changed_files\":[\"docs/CONTROL_CENTER_MONITORING_ACK.md\"],\"changed_files_count\":1,\"changed_files_sha256\":\"3021cf0078cd0629d7f864fe047df647b3514496ab451bafc7f98d7c73c57ae1\",\"dirty\":true,\"observed_head\":\"f5ca59e2ec7f2c51e2905e9c7a804a5d5ef8deef\",\"qa_contract_version\":3,\"reason\":\"Main-authored Control Center stages2-8 baseline actually reviewed by all four independent DeepSeek roles. Sealed QA passed; main 299 automation, 51 knowledge,89 LADS,23 UI/API and v2 synthetic heartbeat/SSE/restore separately evidenced. Two connection retries and cancelled old candidate retained. Explicit --monitor protects tests; only v2 sources trusted. GPT desktop heartbeat, supplier invoice/request count are UNKNOWN. Live unreconciled usage preview is project-scoped; advanced agent/model/task/date filters apply to indexed historical records, not this supplemental recent-event preview (non-blocking limitation). No product/old KB schema/login/production deployment changes; publication still subject to GitHub gates.\",\"review\":{\"sha256\":\"b886741cdb579851c88ee2db4d64668f98cdd5eb3ca8af36485d3f5bbea9155c\",\"status\":\"APPROVED\"},\"source_head\":\"f5ca59e2ec7f2c51e2905e9c7a804a5d5ef8deef\",\"task_id\":\"decef281-1bfa-4ec9-b8ac-93daf86d8451\",\"tests\":[{\"command\":\"python -B ../../split-verifier.py automation ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py knowledge ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py lads ../../qa-unit-current.json\",\"exit_code\":0,\"status\":\"completed\"},{\"command\":\"python -B ../../split-verifier.py broker ../../qa-broker-current.json\",\"exit_code\":0,\"status\":\"completed\"}],\"unit_evidence_sha256\":\"88f5d3743c8cb9bed2d54f39cd1fe3d2904df88c26087ecee39c1df92f8b08a4\",\"working_content_commit\":null}"
- component: "lads-stage3"
<!-- /lads-automation:event -->
