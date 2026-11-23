# CONTROL CENTER Stage 1 Builder ACK

- 审阅基线：源码 HEAD `2a90194c5da48c1d45b219ff924ce266868c348c`；`docs/CONTROL_CENTER_SCOPE.json` SHA-256 `9afc4a2ee646d6e4d2957ec3a1dc8bc01b021d5d7370d691b316905bf1251a33`。清单 24 个文件均存在且哈希一致；控制器备份回执与本次 build/task 相符，ACK 原文件按 absent 记录。
- 阶段范围：仅交付 React/TypeScript 前端与 FastAPI 本机只读概览。后端使用 Registry/KnowledgeReader 的固定、按 `project_id` 过滤 SELECT；API 限制 loopback Host/Origin、GET 与安全响应头；前端获取实际快照。Windows 生命周期在检查或终止前核对 service/schema/instance、PID 归属与进程创建时间，普通 stop 不终止未匹配进程。
- 归属与限制：初始稿由独立 DeepSeek Builder 生成，主控随后修复 Windows MIME、基础依赖与真实子 PID 归属，并补齐测试和文档。主控 UI 证据仅为非独立补充，不构成本 Builder 或独立 QA 的结果。四角色当前状态均为 `UNKNOWN`，历史 Agent 数据只作历史标签，不代表在线状态。
- 明确未完成：Phase 2–8、heartbeat/事件持久化、实时成本、GitHub 在线监控与知识页面均不在本阶段；不宣称八阶段完成。未改动产品、用户数据、运行时或 schema，未提交、推送或部署。独立 QA、Reviewer 复核及原 GitHub 发布门禁仍需执行；先前两次 BLOCKED 记录保持原样。
