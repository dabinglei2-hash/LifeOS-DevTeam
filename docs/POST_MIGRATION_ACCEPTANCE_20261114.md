# 新目录 Hook、知识 MCP 与 Sites 检查（2026-11-14）

本轮由用户授权 GPT 主控实现工程代码与本机验收。以下实测不是独立 Reviewer 审批；源码尚待本轮独立发布门禁。产品业务代码、用户记录、存储格式及登录方式未变。

## 已实际执行

- 当前 CLI 为 0.167.0-alpha.4.1。新根原生项目配置可发现四个 Hook；原兼容启动器重复注入使列表出现八项，且新根定义尚未信任。启动器默认改用项目配置，旧 CLI 仅通过 `--legacy-inline-hooks` 明确选择兼容模式。
- 通过原生 `/hooks` 审阅并信任项目四定义；没有使用信任绕过参数、改写信任文件或关闭沙箱。验收补齐 `LADS_PYTHON` 并继承已配置的无凭据 Windows HTTP 代理；不改模型、Provider、登录或全局配置。
- 真实 OpenAI/gpt-6-astra 回合触发 PreToolUse、PostToolUse、Stop；实际命令退出 7 被记录；Stop 请求继续一次后完成，合成未完成任务的交付仍关闭。原生 SubagentStop 不代表独立 DeepSeek 进程完成，后者继续使用 worker_completed。
- 新增零新增依赖的知识 MCP stdio 适配层，复用 KnowledgeReader：固定一个 Registry 项目、五个只读工具、1–8 条结果、16KB 输入和32KB协议输出上限。没有 SQL、文件、Shell、写入、审批或发布接口。内部错误只记录异常类别与代码位置，不记录参数或异常内容。
- 实际 Codex 客户端发现并调用全部五工具；读取另一项目任务返回 isError。工程数据库和用户主配置字节未变。
- 真实独立 scanner-deepseek / deepseek-flash 的 `codex exec` 调用一次知识搜索 MCP，exit0、turn.completed；实测只读/never/网络 restricted/无可写根，未执行 Shell、未回退 GPT。只验证该受限适配器，不扩称任意第三方 MCP 已验收。生产 Worker 默认 MCP 继续禁用，主控用 `scripts/lads_session.py --knowledge-project lifeos|devteam` 显式启用。
- MCP 的13项内存合成测试、启动器定向测试已执行；全量跨仓库合成QA及最终独立审查由本轮正式任务提供回执，不能用本段自述替代。

## Sites：已存在站点，只读检查

复用 Life OS `appgprj_6ae0c18393c0819198cc9248b4799cf9`，当前账户为 owner、访问模式 public、已有保存版本1。现有地址为 https://life-os-personal-20261027.yellowape13.chatgpt.site ，首页与主要静态资源实际HTTP200；直接请求 index.html 曾403，使用首页路由与一致审计UA后资源读取成功，未使用Cookie或绕过Token。

从产品提交814b55ca的Git字节建立独立构建副本，白名单导出59项、约7.7MB公开资源，摘要/敏感模式/私有路径排除检查通过。未复制浏览器记录、数据库、用户备份或凭据。主要JS仅与本地有换行差异；CSS、线上SW和manifest相同；页面HTML包含托管层变化，不能仅凭首页摘要声明完整线上UI验收通过。

用户本轮选择“使用ChatGPT Sites，先检查现有站点和授权”，因此没有创建新Site、改变访问权限、请求发布凭据、推送站点源、保存新版本或部署。可选Python Notion代理不能作为静态文件发布；不声称真实Notion服务已在Sites可用。

## 保留的失败与边界

首次原生回合因网络超时失败；代理修正后模型执行成功但因Hook未信任而无事件；正常信任后真实原生验收通过。失败命名空间全部保留。

DeepSeek app-server探测两次没有完成initialize：实际诊断为本版CLI不支持 `--profile` 与app-server组合，并非密钥失败（官方API /models 实测200）。改用已有独立profile exec机制后实际MCP工具调用通过。两次失败回执不追认通过。

首次全量直接在研发根运行出现一项旧静态格式检查不兼容与两项缺少跨仓库合成输入的错误；保留输出。环境赋值保持既有可识别写法，完整验收必须使用SplitQABroker的真实合成副本，不向正式产品目录补工程文件。

知识库已在LADS-Workspace；旧CLI工件与既有AppData journal仍需版本化兼容方案才能外移，见[运行根提案](RUNTIME_EXTERNALIZATION_PROPOSAL.md)。未移动或删除旧证据。专用GitHub App仍缺账户侧安装/凭据；未声称已有最小权限App。真机iPhone、真实用户数据恢复、任意外部MCP均不在本轮实测范围。

原始证据仅在本机私有followups与acceptance命名空间；不提交原始对话、日志、SQLite、Token或个人记录。使用说明与接口事实来源：[OpenAI Hooks](https://learn.chatgpt.com/docs/hooks)、[Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)、[实际适配的2025-11-25 stdio规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。
