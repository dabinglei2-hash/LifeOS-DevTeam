# 研发团队开发规则

每次先读 docs/PRD.md、docs/ARCHITECTURE.md、docs/STATUS.md，检查 Git 基线及未提交改动，修改既有文件先备份。

不得清空浏览器存储、修改历史 ID/金额/日期/统计口径或 JSON 格式；不得索要或输出密钥、上传个人记录、提交数据库、覆盖用户改动、强推、重写历史或自动部署。

唯一主控为 GPT/Codex。Scanner、Reviewer、Builder、QA Tester 只能通过 LifeOS-DevTeam 的独立 DeepSeek CLI profile 执行，不使用继承 GPT 的原生子 Agent，不静默回退。只读角色须实际权限预检；Builder 使用隔离 worktree；QA 合成环境独立于 Builder。MCP 未验收时保持禁用。

普通任务仅在真实 QA、独立 Reviewer APPROVED、GPT 验收、文档/知识回执、精确 Diff 和必需 GitHub 检查全部通过后才可提交、推送、创建 PR 和原生自动合并。不得将主控本地测试冒充独立批准；高风险变更须人工授权，生产部署独立审批。

本轮用户明确授权主控迁移，记录 main-authorized-exception、reviewer_independent=false，不放宽发布门禁。每次任务结束更新 STATUS，架构/数据变化同步相应文档，中文汇报实际测试、备份、风险和未测项。

本仓库的 Git 内容只含研发工程，产品源码属于 ../LifeOS-Product。知识库实际文件、跨仓库QA测试产物和完整迁移备份在 ../LADS-Workspace。复用现有已验证的独立CLI安全合同：各仓库的 work/lads/runs/<task> 保存控制状态、源码快照、Builder worktree与阶段报告，阶段3/4私有journal仍使用既有用户级安全目录；这些位置尚未物理外移。它们一律不得存放真实用户记录、进入Git、部署或交给无关模型；安全检查必须拒绝已跟踪的私有目录，不能仅依靠.gitignore。不要把“未上传”写成“已外移”。后续物理路径迁移需单独审查UUID父链、Git worktree回链和权限合同，不能为迁移文档而放松已验证的路径限制。

注册双仓库任务统一使用 LifeOS-DevTeam/scripts/lads_project_flow.py RUN，配套两个正式 repo/QA profile；只读四角色、MAIN-ACK、精确源码与另一仓库快照校验保持。主控实现必须如实标注，独立审查必须覆盖本次全部已准备 Git 差异而非只看 Builder 确认文件。本轮未完成新源码远端门禁前不声明自动发布启用。
