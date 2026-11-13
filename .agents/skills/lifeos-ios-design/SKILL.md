---
name: lifeos-ios-design
description: 为 Life OS 网站设计或改进 Apple 风格的简洁界面、移动布局与 iPhone 交互；适用于页面、组件和 PWA 使用体验，不自动引入原生应用框架或重建技术栈。
---

# Life OS Apple 风格与 iOS 交互

目标：让个人生活管理界面清晰、克制、易操作，同时保持 Web 可访问性和现有业务行为。风格参考 Apple 的设计思想，不宣称获得 Apple 认证或达到原生应用能力。

## 开始前

- 读取适用的 AGENTS.md，检查现有页面、组件、样式、路由与设计约定。`sources/` 及其他同步项目文件只读，不编辑、移动、重命名或删除。
- 区分现状、用户期望和设计提案。不假设 React、Vue、Tailwind、组件库、PWA 或原生容器已存在；沿用实际技术栈。只在任务需要时提出新增依赖，并说明代价。
- 用户要改某页时，聚焦该页及必要共享组件。视觉改造不授权更换数据库、改变记录结构、重写权限或删除功能。
- 有截图或设计稿时先检查；不可访问时明确说明，不能声称已经还原。

## 视觉与信息层级

- 优先呈现本页最重要的信息与主要操作。用字号、留白、分组和语义区分层级，减少无意义的卡片、边框、渐变与装饰。
- 优先复用已有设计变量；没有体系时，为当前范围建立少量可复用的颜色、间距、圆角、字体和阴影变量。所有数值作为项目设计选择，不冒充 Apple 强制标准。
- 使用系统字体回退，确保中文、数字、长标题和日期可读。不擅自下载或分发 Apple 专有字体、图标素材；沿用项目已获许可的资源。
- 色彩以中性背景、明确主色和语义状态色为主。浅色、深色模式仅在任务要求或项目已有支持时落实，不因视觉提案强行增加功能。
- 不用颜色或图标单独表达状态。显示清楚的加载、空白、错误、成功、禁用和保存中状态；避免“保存成功”早于真实持久化成功。

## Web 与 iPhone 交互

- 保留正确的链接、按钮、表单标签、键盘顺序和可见焦点；弹层支持关闭、焦点管理及返回原触发点。图标按钮提供可访问名称。
- 将约 44×44 CSS 像素的可点击区域作为本项目触控起点，结合密度验证；不要把 CSS 像素与原生 pt 视为相同规范。
- 避免只能悬停或滑动才能发现的关键操作；手势提供可见替代入口。删除等破坏性操作清晰区分，并根据可逆性提供撤销或确认。
- 检查窄屏、横屏、长文本、文字放大、滚动和软键盘弹出状态。底部栏与输入区不能遮挡内容；按现有布局合理处理 safe-area 与动态视口，并保留兼容回退。
- 不禁用页面缩放。尊重减少动态效果的偏好；动画只用于说明状态与空间关系，不阻碍操作。
- 优先保留浏览器正常的返回、刷新和滚动习惯。只有一级目的地需要时才使用底部导航，不把所有动作塞进标签栏。

## PWA 条件分支

只有用户要求 PWA 或项目已存在相关实现时才检查 manifest、Service Worker、离线状态和独立窗口体验。核实目标 iOS/Safari 的实际支持；对安装提示、推送、后台执行等版本相关能力查阅当前官方资料，不承诺所有 iPhone 都支持。

视觉修改不得顺带清理用户存储。新增或更新缓存涉及个人数据时，遵循数据保护流程；若 `lifeos-data-guard` 可用可调用，不以其安装为本技能运行前提。

## 验证与交付

在可用的本地预览中检查主要流程，至少覆盖窄屏和桌面布局、键盘操作、状态反馈、文本溢出及本次涉及的表单。对比度采用项目适用的可访问性标准核验，不以“看起来像 iOS”代替可用性检查。

涉及 iOS 特性的改动尽可能在 Safari/iPhone 上验证；只有桌面模拟时明确写“未在 iPhone 真机验证”。未运行预览不能声称视觉检查通过。交付说明改动页面、沿用的设计选择、验证范围与已知限制，必要时附预览或截图；不将示例数据呈现为用户真实记录。

## Stage 3 applicability

- Use this skill for mobile-first Life OS pages, sheets, controls, and PWA-facing interaction changes.
- Preserve existing styles, routes, semantics, and data behavior.
- Treat iPhone claims as unverified unless real Safari/device evidence exists.

## Stage 3 steps

1. Inspect the current markup, CSS variables, icons, and shared interaction helpers.
2. Keep controls at least 44 by 44 CSS pixels and retain accessible names and focus.
3. Check narrow, short, and desktop layouts, bottom-navigation overlap, reduced motion, and console errors.
4. Keep all existing markup and behavior unless the approved scope explicitly changes one element.
5. Record screenshots as private evidence and do not present them as model-readable public data.

## Stage 3 safety and output

- Do not rebuild the stack or remove navigation to improve layout.
- Do not claim physical iPhone verification from Chromium emulation.
- Output the viewport matrix, overflow results, interaction evidence, and unverified device limits.

## Stage 3 acceptance

Acceptance requires evidence for the affected viewport and interaction, preserved
existing markup and behavior, accessible controls, no horizontal overflow or
console error, and an explicit unverified-device limitation.

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
