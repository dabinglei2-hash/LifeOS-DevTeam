# LADS QA 执行适配提案（待确认，2026-11-05）

## 已核实的问题

阶段4任务 `5fbe8834-26e6-4b78-84fb-af5dc1786766` 保持 **BLOCKED**。独立 DeepSeek QA 的规定 automation 命令实际运行174项、退出1，其中60项因只读沙箱没有可用临时目录而报错；知识库38项和密封 broker 的34项检查通过。Reviewer 实际复现并要求修改，第二次 Builder 明确报告该修复超出20文件白名单，没有跳过测试或放宽权限。

当前协议同时要求 QA 模型保持 read-only、无可写根，并直接运行需要创建临时 Git/SQLite 文件的整套 unittest。这是执行合同冲突，不能靠改测试预期、跳过文件测试或冒称 QA PASS 解决。官方权限说明区分了只读与工作区可写模式：[OpenAI 权限文档](https://learn.chatgpt.com/docs/agent-approvals-security)。实际行为以上述本机证据为准。

主控已复用现有 `CodexJsonRpcExecutor` 做隔离实验：固定两条 unittest 命令在独立副本及私有 TEMP/TMP 中、网络关闭、无模型和密钥执行。automation174、knowledge38均退出0；原候选与工作副本的原始字节摘要均未变化。此结果标记为 **main-controller-architecture-probe / independent_qa=false**，不是对失败 QA 的替代或最终批准。

证据位于 `work/lads-stage4-continue-20261105-a42f9c11/host-unit-probe.json` 和两份 `host-unit-probe-*.txt`；QA、Reviewer、Builder 原始失败及计数保留在该任务的 `stages/`、`events.jsonl`、`finalhandoff.json`。

## 推荐方案 A：保留只读模型，扩展现有可信测试执行器

1. Scanner、Reviewer、QA 模型权限与独立 DeepSeek profiles 均不变；Builder 继续使用独立 worktree，不增加任何模型的写权限。
2. 复用当前 credential-free host broker 和受控 app-server executor，在一次性代码副本及候选之外的私有临时根实际执行两条固定 unittest 命令。禁止任意命令、外网、真实用户数据和候选修改。
3. 封存任务ID、候选原始 SHA、测试命令、实际退出码、运行次数/错误/跳过情况、输出摘要、执行权限及前后完整性回执。缺少、损坏、重放、错候选或失败回执均不放行。
4. 只读 QA 独立运行固定验证命令并审阅这些真实回执；明确区分“可信隔离执行器运行测试”和“DeepSeek QA 独立验收”，不声称模型自己完成了文件写入。
5. 为新运行设置明确的工程 QA 合同版本，兼容识别既有成功/失败回执；旧任务、计数、审批、安装图原字节不改写。当前实际 `code_reworks=1` 不回写为0。基础设施准备失败单独记录，不伪造为逻辑测试结果或自动发起无意义代码返工。
6. 受保护文件仍通过“Builder 写候选 → 独立审查精确 Diff/哈希 → 主控备份安装”的既有机制；必要的自举验收单独标识。安装后必须重新执行真实四角色任务和失败路径，不能用本轮主控探针代替。
7. 新合同独立 QA、最终 Review、MAIN-ACK、文档、知识索引全部通过后，继续已授权的阶段4分支、commit、push、PR、CI与原生自动合并；不降低 GitHub 保护，不正式部署。

## 拟扩展的最小工程范围

- `lads_automation/qa.py`、`rpc.py`、`team.py`：固定单测执行、临时根、隔离副本和密封证明。
- `scripts/lads_stage3.py`、`lads_automation/delivery.py`、`github_release.py`：版本化验证入口、实际命令/权限/来源绑定及发布门禁。
- 相关 `tests/automation/`；若基础编排器需要合同版本透传，受保护的 `scripts/lads_orchestrator.py` 与对应 `tests/lads/` 必须先给出精确候选，按原流程审查安装。
- 相关工程规范、架构、开发进度和验收记录。实施前重新收敛为明确文件白名单并备份。

不改产品源码、localStorage/IndexedDB、用户记录、旧ID、JSON v11、数据库迁移、模型路由、用户级配置、密钥、账户权限或正式部署配置。

## 验收要求

- 两条固定单测在隔离执行器真实执行，无失败或关键用例跳过；原候选和正式工作区保持完整。
- QA 仍只读；Scanner/Reviewer仍只读；角色不得自定义测试命令或修改回执。
- 伪造/过期/错候选/错命令/失败/超时/临时根不可用均阻断；保留全部失败，不重置旧计数。
- 四角色真实调用、QA拒绝失败和 Reviewer要求返工再次验证；只有真实门禁通过后才继续 GitHub 验收。

本提案未实施。待用户确认方案 A 后才能扩展已冻结的 QA 合同；选择停止则保留当前 BLOCKED，不提交或发布。


## 决策更新（2026-11-05）

用户已明确批准方案A。原任务及其失败/计数仍冻结；适配在新独立任务进行。此批准不代表已实现或已通过独立QA，不放宽模型权限与GitHub保护。
