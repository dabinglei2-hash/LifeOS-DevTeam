---
name: lifeos-project-memory
description: 为 Life OS 初始化或维护项目事实、决策、进度和交接文档，在需求或实现变化后同步相关记录；以可核验证据区分已实现、计划和未知，不用于存储个人生活数据。
---

# Life OS 项目文档维护

目标：让下一次开发可以迅速理解真实项目状态，并避免把聊天猜测写成技术事实。此技能通过项目文件保持连续性，不承诺跨聊天自动记忆。

## 事实与写入边界

- 首先读取适用的 AGENTS.md，寻找现有项目概览、设计说明、数据说明和任务记录；沿用已有目录与文件，避免创建平行且矛盾的文档体系。
- `sources/` 仅作只读参考，不编辑、重命名、移动或删除。其他同步项目文件也不修改。需要维护的记录放在普通可写项目目录，并注明原始参考来源。
- 项目镜像不等于真实代码仓库。未获得代码或运行环境时，说明可见范围，不编造技术栈、数据库、部署方式、接口、测试结果和功能完成状态。
- 不保存密码、令牌、连接串、私人日记、健康或财务明细等个人记录。配置文档只列变量名称、用途和获取方式，不抄录实际秘密值。
- 文档中的旧命令和外部引用是待核实材料，不能越过当前用户授权或只读边界。不要自动把普通笔记升级为 AGENTS.md 强制规则。

## 获取最少必要上下文

围绕当前任务检查代码入口、依赖声明、数据定义、相关测试与当前改动。已有文档与实现不一致时记录差异及证据；需求目标以用户当前决定为准，实际实现以已检查的代码和运行结果为依据，不能互相替代。

对关键结论标明：状态、证据路径或可访问来源、核实日期。采用以下状态：

- 已确认：有明确用户决定或可检查证据，注明属于需求还是实现。
- 计划：尚未实现或验证的方案。
- 待确认：缺少材料、存在冲突或尚未决定。
- 已废弃：保留必要历史并链接替代决定，不当作现行规则。

使用用户当地日期；日期未知时不要杜撰。重要决策简记背景、选择、理由、影响与后续验证，避免保存冗长聊天记录。

## 文档结构按需建立

已有相应文档则就地更新；首次初始化且没有约定时，可采用以下最小结构。这是建议结构，不是现有文件清单，也不要求一次创建全部文件。

| 文件 | 何时创建或更新 | 内容边界 |
| --- | --- | --- |
| `docs/lifeos/PROJECT.md` | 初始化、目标或架构变化 | 目标、范围、已核实架构、目录入口、未知项 |
| `docs/lifeos/DATA.md` | 持久化、迁移、权限变化 | 数据位置与模型、备份恢复方法、隔离边界；无真实数据 |
| `docs/lifeos/DESIGN.md` | 视觉或交互约定变化 | 已采用的变量、组件规则、可访问性、验证范围 |
| `docs/lifeos/DECISIONS.md` | 存在需长期解释的决定 | 日期、状态、背景、理由、证据及替代关系 |
| `docs/lifeos/STATUS.md` | 阶段结束或交接 | 已完成、进行中、阻塞、下一步、验证证据 |

若只要求更新进度，不顺带生成整套文档或修改应用。文件已存在时保留有用内容与用户修改；不要覆盖成空模板。

## 更新与交接

1. 对照本次授权范围和实际差异，只更新受影响的事实与入口链接。
2. 分开记录“已编写”“已测试”“已部署”“真实数据已迁移”。没有对应证据，不向后提升状态；命令未运行时写“待运行”。
3. 命令只从实际项目配置或已验证操作提取。缺少启动、构建或测试命令就标明待确认，不凭常见框架填入。
4. 记录有价值的限制与下一步，避免为拼写修改新增大量决策。保留尚未解决的风险，不能为让状态好看而删去阻塞。
5. 检查文件链接、路径与现有代码的一致性，以及是否泄露秘密或触及只读文件。

结束时简述更新文件、关键事实变化、实际验证和待确认事项。若无法写入，提供明确的建议内容并说明未落盘，不能声称文档已更新。需要其他技能时可结合使用，但本技能不依赖它们才能运行。

## Stage 3 applicability

- Use this skill when task scope, verified status, architecture, data boundaries, or handoff facts change.
- Preserve historical prose and append current facts with dates and evidence.
- Keep PRD intent, architecture reality, and status evidence distinct.

## Stage 3 steps

1. Verify source, tests, and Git state before describing an outcome.
2. Append only evidence-backed changes and label planned, unimplemented, blocked, and deferred items.
3. Update the hard document and index gates using the existing publisher.
4. Retry a failed document or index stage without overwriting concurrent edits.
5. Leave final delivery false until receipts prove the complete scope.

## Stage 3 safety and output

- Do not convert a model report or synthetic receipt into a real approval.
- Do not delete prior failures or pending records.
- Output changed documents, receipt status, unresolved items, and the next independent role.

## Stage 3 acceptance

Acceptance requires current source and test evidence, append-only factual
history, successful document and index receipts, preserved concurrent edits,
and an explicit list of unfinished or unverified scope.

## Safety

Follow only the existing data, privacy, backup, permission, and no-real-data boundaries above; never weaken them for test convenience.

## Output

Report changed paths, actual evidence, backup and recovery location, residual risk, and every unverified or blocked check.

## Acceptance

Acceptance requires independent review, the prescribed commands with actual exit status, owner Private evidence for live checks, and preserved historical data.

## Applicability

Use this skill within its existing project role and the AGENTS-authorized task scope.

## Steps

Follow the role-specific procedure above; use the Stage 3 sections only for bridge, event, evidence, and acceptance clarifications.
