# LifeOS-DevTeam / LADS 2.0

GPT/Codex 唯一主控，四个独立 DeepSeek CLI 执行角色。工程代码与产品源码分开。知识库、跨仓库QA和完整备份在LADS-Workspace；受保护的CLI控制目录仍在各repo的ignored work/lads/runs，阶段3/4 journal仍在用户级私有目录；均不得入Git。详细实际路径以AGENTS和ARCHITECTURE为准，不声称运行目录已全部外移。

依赖：Python3.14、Git、Codex CLI；`python -m pip install -r requirements-lads.txt`（安装须获授权，已有环境优先复用）。

本地验证：`python -B scripts/validate_devteam.py --product-root ../LifeOS-Product --workspace ../LADS-Workspace`。注册：`python -B scripts/lads_project.py status --project lifeos`。

[架构](docs/ARCHITECTURE.md)、[知识库](docs/LADS_KNOWLEDGE_DB.md)、[迁移报告](docs/DUAL_REPOSITORY_MIGRATION_REPORT.md)。
