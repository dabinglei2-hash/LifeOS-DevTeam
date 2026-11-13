# DeepSeek 开发团队实测（2026-11-02）

结论：Reviewer/Builder 的真实 API、CLI 工具和权限测试通过；Scanner 返回 HTTP 401，整体尚未验收。用户正在按本机隐藏输入方式修正 Scanner 密钥。未使用其他角色密钥或 GPT 兜底。

## 实际证据

| 范围 | 结果 |
| --- | --- |
| Scanner `/responses`, deepseek-flash | HTTP 401；无 usage；当前进程与用户变量一致，无首尾空白。不能判断具体是复制错误、失效或其他认证原因 |
| Reviewer `/responses`, deepseek-v4-pro | HTTP 200 / completed；16 input、1 output token |
| Builder `/responses`, deepseek-v4-pro | HTTP 200 / completed；16 input、1 output token |
| Reviewer 文件读取、目录、Shell、Git | 在独立合成仓库实际执行成功 |
| Reviewer 方案审查 | CHANGES_REQUESTED：拒绝把正确测试预期从 4 改成 3 |
| Builder apply_patch | 独立 worktree 实际仅修改 calc.py，从 a+b-1 改为 a+b |
| Builder 测试 | python -B -m unittest -v，1 个测试通过；test_calc.py 字节与基线一致 |
| Reviewer 最终 Diff | APPROVED，仅针对合成 calc.py 修复；不是 Life OS 改动批准 |
| Reviewer 真实工具写入 canary | Access denied，文件不存在 |
| Builder 真实工具写入 canary | worktree 内成功，向外部合成路径写入被拒绝；主控复核文件状态 |
| 独立 codex sandbox 子命令探针 | Builder 仍拒绝写入；不能用此子命令结果替代 exec 真实工具结果，原因待确认 |
| MCP / apps / plugins | 本轮显式禁用，未测试兼容性 |
| 完整 Scanner→Reviewer→Builder→复扫 | 未完成：Scanner 认证阻塞；最多三轮自动状态机也未实现 |

本轮是合成任务的显式交接验证，非已启用的生产自动调度器。scripts/lifeos-deepseek.py 仍仅允许 --check，不能声称实时派发功能已经完成。Scanner 修复后仍须实测其文件/Git/权限，完成全链路复核后再决定启用。

## 模型路由与数据保护

三个请求直接发往 https://api.deepseek.com/responses，分别读取对应角色环境变量；成功响应返回 deepseek-v4-pro。CLI 分别指定独立 profile，并显式覆盖 Provider/model，禁用再委派，无 GPT 回退。CLI 工具证据与直接 API 响应分开保存，模型自述不作为路由证明。

Reviewer 首次报告猜测错误的模型名及固定时间；已标记不可信，不将其写入标准用量记录。主控使用实际请求配置、API/CLI usage 和本机时间。首轮 Reviewer 开始/结束时间未完整记录，因此为 null，不补造。

主控模型配置当前仍为 gpt-6-astra，Provider 默认为 OpenAI。测试期间用户级 config.toml 哈希发生变化（feccee7… → 01cd363…），原因待确认；本轮脚本未主动写该文件，不能声称整个文件未变，也未尝试恢复覆盖。未读写 auth.json。

Life OS 工作区所有既有文件在测试后逐字节哈希与本轮基线一致，再备份并追加本轮文档；未操作真实数据、浏览器存储、JSON 或数据库。真实数据备份恢复本轮未测。独立合成仓库的初始化提交仅用于测试，不是 Life OS 提交；未推送、部署、安装依赖。

本地忽略证据目录：work/deepseek-live-validation/，含 api-results、各角色工具事件、normalized-task-records、权限探针与项目基线。无密钥值、无真实 Life OS 内容；原始工具输出仅限合成文件。正文不复制会话自报身份。

合成仓库：D:\CODEX\LifeOS-Agent-Tests\20261102-165951
独立 Builder worktree：D:\CODEX\LifeOS-Agent-Tests\20261102-165951-builder
文档修改前副本：backups\pre-deepseek-verification-docs-20261102-170947

## 实测 CLI 用量与会话

| 子任务 | session | 输入 token | 输出 token |
| --- | --- | ---: | ---: |
| reviewer-plan | 01a19b7c-93ce-76c0-9301-4cced0b1c83b | 42622 | 1809 |
| builder-fix | 01a19b7e-2e22-7d60-b340-2b66c5ab09e6 | 56812 | 2845 |
| reviewer-acceptance | 01a19b80-1203-72c0-b87a-446d4176f88c | 35159 | 3731 |
| builder-acceptance | 01a19b80-1203-72d0-af46-b0532edce98a | 72270 | 5335 |

用量为四次独立 CLI 任务累计值，包含多次工具往返，不等于单条提示词长度。实际账单未查询，不以估算代替扣费记录。


## 2026-11-02 17时补验：Scanner 恢复，三角色个体验收通过

此增量覆盖本报告上方 Scanner 401 的历史结论：读取用户新保存的 Windows 用户环境变量，并仅注入本次子进程，未打印/复制密钥；Scanner 官方 /responses 返回 HTTP 200 / completed，response_model=deepseek-flash，16 input / 1 output token。请求ID：55a8e29a-36a1-49c9-b944-5c960374fe3f。现有桌面/终端进程仍可能持有旧变量，日后运行前重新打开 Codex/终端。

Scanner 实际独立 CLI 会话：01a19b8b-ce6e-7d21-9224-bb2fdf2e209b。文件读取、目录、rg、Git status/diff/HEAD 成功；canary 写入被 OS 拒绝、文件未创建。初次并行工具错误选择 Windows bash.exe，失败后改用 PowerShell 完成；应在后续工具适配中固定 PowerShell。该修复建议已获 Reviewer 技术认可，但本轮未改角色源码/启动器。

独立 Reviewer 汇总复核会话：01a19b9c-ed94-7053-8adc-a326c3216de7。实际读取四份脱敏合成证据与 Git Diff，个体角色/工具/人工交接结论 APPROVED；生产自动调度器结论 BLOCKED。复核没有写文件、跑测试或委派。不是部署/推送授权。

### 当前验收边界

- **已验证**：三个对应密钥的 DeepSeek 官方 Responses 请求；三个独立 profile CLI 的角色执行；Scanner/Reviewer 只读工具写入拒绝；Builder 在独立 worktree 内 apply_patch 和 Shell 写入成功，越界写入拒绝；1 个测试通过；错误方案被拒绝、正确 Diff 交回 Reviewer 批准；主控人工交接与证据复核。
- **未实现/未验收**：单个全新任务严格从初始 Scanner→计划→Builder→复扫的完整链路、最多三轮自动返工调度、MCP兼容、生产启动器开放。本轮未将 scripts/lifeos-deepseek.py 的离线门禁移除，不能宣布一键团队系统已完成。
- 各角色模型路由来自主控发往官方端点的 API 响应和 CLI 显式 profile/Provider 配置，不能以模型自述证明。没有采用 GPT 兜底。
- 主控配置当前仍 gpt-6-astra / OpenAI；之前已记录的主 config 哈希变化原因待确认，不做覆盖恢复。真实 Life OS 业务代码、用户数据、存储/JSON、Git提交/推送/部署均不动；本轮仅追加/更新项目验证文档。

本轮补充证据：work/deepseek-live-validation/scanner-api-DeepSeek-V4.1-Flashrecheck.json、scanner-acceptance.json、team-evidence-review.json、normalized-task-records-final.json。文档修改前副本：backups\pre-scanner-verification-docs-20261102-173906。

补充 CLI token（API 返回，不使用模型猜测）：

- scanner-acceptance：input=63466，output=7682，cached_input=59008。

- team-evidence-review：input=53256，output=10915，cached_input=41728。
