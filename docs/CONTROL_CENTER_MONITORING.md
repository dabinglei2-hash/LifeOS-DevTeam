# Control Center 阶段2–8

2026-11-23。用户明确授权主控完成工程实现；不冒充独立Agent开发/QA/审批。Stage1历史和失败记录不改写。当前开发分支源自`f5ca59e2ec7f2c51e2905e9c7a804a5d5ef8deef`。

## 实现与来源

八页面可操作：总览、Agent、任务、成本、QA、GitHub、知识库、设置。hash导航兼容刷新和返回，复用原视觉与项目筛选。接口全部GET；没有调度、改数据、push或merge端点。固定127.0.0.1，沿用Host/Origin/Fetch-Site/CSP边界。

- `GET /api/sections/{agents|tasks|cost|qa|github|knowledge|settings}`：固定项目范围，分页最大100，offset最大10000，UUID/带时区日期验证。
- `GET /api/events?project=devteam&after=0`：SSE每2秒最多100条；连接最长600秒，Last-Event-ID续传，断连清理。连接保活不是Agent心跳。
- 实际任务通过`python -B scripts/lads_project_flow.py RUN --monitor`显式开启本次host上报（其余参数沿用原命令）。默认导入/单元测试不启用。现有`lads_runtime.py`进程启动/结束由host observer上报；实际子进程存活时每5秒心跳。`lads_orchestrator.py`上报正式Worker、任务状态与实际turn.completed usage。原Windows Job、权限预检、超时、输出限制、报告与发布门禁保留；预检不冒充Worker。
- 20秒内心跳且PID和创建身份匹配才BUSY；过期/进程消失OFFLINE，失败ERROR，无可信样本UNKNOWN。任务结束的工作进程OFFLINE；并行run分开。GPT桌面主脑未接可信心跳，TASK_EVENT不冒充主脑在线。
- 任务读取工程知识库并补实时host状态；旧completed不推断MERGED。分支、优先级等未记录字段保持未知；保留测试/审查/失败信息、PR与时间线。
- 成本读取历史model_usage与实时API usage，分别展示避免未对账重复累加。按项目/Agent/模型/任务/时间筛选，上海自然日聚合最多200组。用量记录数不等于供应商HTTP请求次数，null不等于0。GPT桌面用量未知。
- `config/control-center-pricing.json`记录provider/model、生效时间、USD每百万Token价格与HTTPS来源；默认空，不编造报价。缓存Token缺失且存在缓存差价时不估算；可计算时仅ESTIMATED，不是实际账单，不改原始usage。当前实际费用UNKNOWN。
- QA读取现有test_runs、脱敏摘要/输出哈希、Reviewer结论与问题；passed/(passed+failed)按运行记录计算，不是单测案例数量。原始终端日志不向浏览器开放。
- GitHub每60秒从官方GraphQL读取固定两个Public仓库的分支、Commit、Push、PR、CI与原生自动合并状态；失败保留旧值并标stale，超过120秒也标stale。不推断远端同步或绕过发布门禁。
- 知识库仍用KnowledgeReader只读、项目UUID隔离；显示实际schema、表数、计数、索引和备份文件时间；检索最多10条，附来源/Commit。备份存在不冒充已恢复验证。
- 设置只显示本地服务、工程路径与错误代码。密钥仅显示环境/Windows用户变量是否存在，不显示值，不把存在当有效连通。

## 独立监控数据库

`D:/CODEX/LADS-Workspace/control-center-data/control-center.sqlite3`，SQLAlchemy，专用application_id，user_version=1。实际三表：task_events、source_cache、system_events。Agent/run/usage是事件投影，QA和知识仍读取原工程事实，避免重复建立十张事实表。原工程库schema、产品和浏览器数据不变。

Host只写spool中的UUID信封，不写提示词/正文/原始输出/密钥。event_id唯一；原始timestamp与received_at分开。入库成功才确认移除信封；数据库暂时失败保留重试；格式错误保留.rejected并记录代码。服务建立本地开启标记；停服后outbox保留，重启补收。路径拒绝reparse；源代码、SQLite、sidecar、spool、日志和备份均不进入公开Git。

首次集成发现“仅存在开启标记”会让主控单元测试写入正式工程监控；已改为显式host上下文与线程继承的observer授权。旧v1来源记录保留、不作为在线/用量/时间线事实；当前页面与SSE仅读取v2来源。新增回归验证默认测试进程即使见到开启标记也不发事件。任务9925eb41-6e3c-4706-abe4-820ddf6b6915因主控发现此问题主动停止；原目录和计数保留，不能用其旧候选审批发布新代码。

离线工具在仓库根运行（仅工程监控备份，不是产品JSON备份）：

```powershell
& 'D:/CODEX/LADS-Workspace/control-center-data/venv/Scripts/python.exe' -B -m control_center.storage_cli backup --home D:/CODEX/LADS-Workspace/control-center-data
& 'D:/CODEX/LADS-Workspace/control-center-data/venv/Scripts/python.exe' -B -m control_center.storage_cli restore --home D:/CODEX/LADS-Workspace/control-center-restored-NEW --backup '<实际备份路径>'
```

恢复仅写全新目录；校验application_id、schema与integrity_check；拒绝覆盖。未知/更高schema不自动迁移。升级schema须另加版本迁移与恢复测试。停止脚本不删除数据库。

启动/停止/检查仍用[既有脚本](CONTROL_CENTER.md#本地运行边界)，默认http://127.0.0.1:8765/。没有新增依赖或业务服务。

## 分阶段实际验证

|阶段|主控实际结果|独立验收|
|---|---|---|
|2 Agent|21项新监控测试；四角色合成进程BUSY/心跳/OFFLINE，异常退出ERROR|真实DeepSeek链路待本轮机器回执|
|3 事件|16条真实SSE传输；去重、项目/任务隔离、续传游标、失败保留、重启与备份恢复通过|待本轮回执|
|4 用量|实际工程usage读取、筛选、带时区范围、价格生效/缺失缓存/不改原值测试通过；账单UNKNOWN|待本轮回执|
|5 QA|真实工程test_runs与审查读取；主控最终299自动化、此前51知识库+89 LADS通过；21项新监控已计入299|待本轮完整基线QA|
|6 GitHub|两个实际Public仓库API读取成功；失败保留快照测试通过|远端CI/PR交付待门禁|
|7 知识|两个项目只读schema/计数/检索成功，浏览器来源检查通过|待本轮回执|
|8 集成|TS/Vite构建、23项API/三尺寸浏览器通过|独立Review/QA、四角色真实监控、文档索引、PR尚待最终证据|

主控测试不等于独立QA。合成进程不是DeepSeek请求。首次SQLite句柄释放、旧测试路径失败保留，修复与复跑分开。当前修改未自动获得发布批准。

源码备份：`D:/CODEX/LADS-Workspace/backups/20261123-183604-control-center-completion/manifest.json`；私有证据：`D:/CODEX/LADS-Workspace/control-center-completion/`，包括automation/knowledge/lads日志、browser/browser-evidence.json、heartbeat-sse-evidence.json。不提交这些私人运行产物。

尚未接入：GPT桌面可信心跳/完整账单、实时供应商账单和精确HTTP请求数、未记录的扫描百分比/修改文件数。iPhone/PWA真机未测。本机服务没有远程身份认证，不允许LAN/公网访问。产品JSON备份不在本轮操作范围，没有读取真实备份或用户存储。
