# 运行工件外移兼容提案（待实施）

状态：仅方案，不是已完成外移。当前知识库、跨仓库QA和完整备份已在LADS-Workspace；旧CLI工件与阶段3/4 journal仍在原位置。任何实施必须保留失败、计数、Git回链与不可变证据，不能用junction或关闭路径检查代替迁移。

## 代码事实与影响

`scripts/lads_orchestrator.py` 的 validate_run_root 明确要求 repo/work/lads/runs/UUID，调度器在该处建根；锁、Builder Git worktree及相对密封验证命令依赖此布局。`lads_automation/delivery.py` 重新验证位置和原始报告；`lads_automation/state.py` 与 github_release.py 的私有journal使用既有用户级固定目录。旧报告绑定实际绝对cwd/argv、源码和回执哈希，直接移动会使历史验收失效。

## 可回滚的最小分阶段方案

1. 新增版本化运行位置描述，固定 Registry workspace、project_id、task_id、canonical repo、Git common-dir 与父链归属；新任务可选 W/runs/project_id/task_id，旧v1读取与默认行为保持兼容。
2. 只切换新任务，完成只读角色权限、Builder worktree回链、密封QA相对路径、超时、重试、清理及发布幂等的正负测试。不得开放任意 --run-root 或符号链接逃逸。
3. 旧工件先逐文件校验归档副本，保留原位置。若需要彻底移出，另实现显式只读归档解析器：原记录逻辑路径与新的物理路径映射均哈希绑定；旧记录字节不改写、不再执行归档命令。
4. 全部独立验收通过后，才考虑按确认清单处理旧原件。需先核实所有任务空闲、原始备份/恢复、Git worktree移动回链和每个旧失败/已合并回执可重验；任何一步失败保留原件。

拟涉及：scripts/lads_orchestrator.py、scripts/lifeos-deepseek.py、lads_automation/{state,delivery,github_release,split_qa,project_registry}.py 与对应权限/路径/归档/发布回归测试；最终精确清单由专项Scanner确认。Schema/权限兼容方案须先独立Reviewer审查，不能把本提案视为自动授权删除或已实现新架构。

本轮没有改变路径合同、搬迁Git worktree、删除旧目录、重写记录或清零计数。彻底外移与历史证据兼容尚待专项实施和验收。
