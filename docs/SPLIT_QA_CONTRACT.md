# 双仓库 v3 QA 与发布合同

本合同在旧 Stage3 v2 之外新增，不放宽原审批或发布门禁。正常注册任务使用 LifeOS-DevTeam/scripts/lads_project_flow.py RUN --project lifeos|devteam --task-file ABSOLUTE_JSON --base D:/CODEX --workspace D:/CODEX/LADS-Workspace --credential-source windows-user。输入 manifest 仍是原两条 QA 命令，调度器绑定 registry 摘要并替换为固定四条只读核验命令。

可信 broker 使用 credential-free Codex app-server 的受限 command/exec 执行自动化、知识库、LADS、产品浏览器/PWA/API测试。原始进程输出、返回码、测试数量、候选和另一仓库实物、HEAD、组合来源、内层报告同时绑定。read-only 模型 QA 独立读取收据并执行三项单元/一项综合验证，不能自己修改测试环境、虚报成功或取消失败的浏览器检查。基础设施失败在代码返工前阻断，原失败保留。

来源有历史已跟踪删除时，snapshot 只省略 git 明确标记删除的文件，仍绑定原 HEAD 和完整 Git status；不是忽略任意缺失文件。发布前必须逐项列出 prerequisite/candidate/deletion、Git旧字节和当前新字节，并对全部已准备基线差异作独立审查；Builder只写确认文档不代表其撰写了主控代码。

MAIN-ACK/ACCEPT/RETRY 复用 scripts/lads_stage3.py。发布明确传 --repository dabinglei2-hash/LifeOS 或 dabinglei2-hash/LifeOS-DevTeam，后者只接受 v3。不得把另一个仓库 QA/授权/remote 用在当前仓库。独立 Reviewer、主控 ACK、文档、知识库、CI、main保护及原生 Auto-merge 全部必须通过。

本地实测245+38+89测试及产品14项浏览器通过；独立迁移和远端合并仍待验证。历史原生Hook验收不能代替新根验证，账号认证/AI未实现不能被报告为通过。
