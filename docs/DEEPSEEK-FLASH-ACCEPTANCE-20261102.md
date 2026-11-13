# Reviewer / Builder 切换 DeepSeek-V4.1-Flash 验收

日期：2026-11-02。结论：**可采用；新隔离合成任务的五步人工调度链路通过，最终 Reviewer 为 APPROVED**。不是复杂任务质量全覆盖或生产自动调度器完成证明。

## 模型与文件

- Reviewer、Builder 用户级 profiles 的 model 从 deepseek-v4-pro 改为 deepseek-flash；仍各用对应独立环境变量，无密钥落盘/输出，Provider 不变。
- 官方 DeepSeek-V4.1-Flash 的 API ID 是 deepseek-flash，不把大写展示名作为 API ID；服务商映射不等于不可变权重版本。Scanner profile 哈希未变。
- scripts/lifeos-deepseek.py 仅更新两角色模型校验映射，与副本精确比较通过；离线门禁、权限、密钥引用不变。
- GPT 主控当前仍 gpt-6-astra / OpenAI，未改登录。本轮代码未主动写主 config.toml，但其哈希期间从 dc36f692… 变为 3e64cac8…，原因未确认；不能声称整个主配置逐字不变，没有覆盖恢复。
- 主控按用户明确授权准备模型配置；合成源码由独立 Flash Builder 修改。Life OS 业务源码/用户数据/JSON/数据库不变，不安装依赖，不提交/推送/部署。测试仓库初始化提交只为合成基线，不是 Life OS 提交。

## 实际测试

| 项目 | 实际结果 |
| --- | --- |
| Reviewer 与 Builder 官方 /responses | 各自密钥 HTTP 200/completed，response_model=deepseek-flash |
| Scanner 初扫 | 找出 add() 错用减法，提出 calc.py 单行修复 |
| Flash Reviewer 方案 | APPROVED，绑定 HEAD/文件哈希，仅允许 calc.py，原测试不变 |
| Flash Builder | apply_patch 修改 calc.py；实际运行 unittest，3项通过 |
| Scanner 复扫 | tracked 修改只有 calc.py，测试文件字节一致；另有授权合成canary |
| Flash Reviewer 最终Diff | APPROVED，并实际复跑3项测试通过 |
| 主控核验 | 再跑同一3项测试通过，不把重复执行累计为9项 |
| Builder 写权限 | worktree 内写入成功；外部合成路径明确 Access denied |
| Reviewer 只读 | 初次错误捕获脚本受限；直接命令补测明确 Access denied/exit1，文件不存在 |
| Scanner 只读 | 本轮探针文件未创建，捕获细节受限；保留前轮直接拒绝证据，不宣称本轮取得完整异常原因 |
| 配置与范围 | Python/TOML/模型绑定通过；Scanner不变，项目非允许文件哈希一致 |

本轮在同一个新 worktree 串行执行 Scanner→Reviewer→Builder→Scanner→Reviewer；无嵌套委派，无 GPT/Pro 回退。metadata 使用主控实测时间、指定Provider与API/CLI usage，不依赖模型自述。

## 限制

- 部分调用仍误选 bash/WSL，失败后 PowerShell 重试成功。ConstrainedLanguage 禁止 .GetType()/自定义对象，诊断失败未隐藏；Reviewer 已用无catch直接命令补测。建议后续固化Windows工具适配。
- 合成工作区没有 Life OS PRD/架构等完整业务上下文；这里只验收模型路由、一次小任务与工具/权限。
- 未实现/未验收：生产一键自动启动器、最多三轮自动返工状态机、MCP（禁用）。离线检查器的实时派发门禁仍保留。本轮未触发 CHANGES_REQUESTED，不把旧 Pro 接口的返工测试计作本轮 Flash 测试。
- 业务UI、真实JSON恢复、健康财务等本轮未测试，因为没有修改。

## 证据与恢复

修改前副本：backups\pre-flash-switch-20261102-174446。

合成仓库：D:\CODEX\LifeOS-Agent-Tests\Flash-20261102-174614

Builder worktree：D:\CODEX\LifeOS-Agent-Tests\Flash-20261102-174614-builder

忽略证据目录：work\flash-validation-20261102-174446，包含 api.json、records.json、每阶段工具事件、reviewer-direct-denial.json、主控测试输出和保护基线。

| 阶段 | session | input token | output token |
| --- | --- | ---: | ---: |
| FLASH-SCAN-INITIAL | 01a19ba7-0c54-7423-be79-f539b75dfd24 | 50394 | 5623 |
| FLASH-REVIEW-PLAN | 01a19ba7-e76a-7061-93f1-c02bfbf28d54 | 57596 | 7103 |
| FLASH-BUILD | 01a19ba8-d8dd-70d1-a298-01d21cb82bc8 | 97435 | 4903 |
| FLASH-SCAN-FINAL | 01a19ba9-fdbd-7f70-ab61-9eef97555bde | 30696 | 4900 |
| FLASH-REVIEW-FINAL | 01a19baa-9831-7fb0-82e4-5ed389e2cc1e | 61174 | 5159 |

用量为包含工具往返的CLI返回值；未查询实际账单，不以预估冒充扣费。

官方来源：[模型发布说明](https://www.deepseek.com/news/deepseek-v4-1-flash/)、[模型与定价](https://api-docs.deepseek.com/quick_start/pricing/)。
