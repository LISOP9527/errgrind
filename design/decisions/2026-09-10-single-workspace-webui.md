# Single-workspace WebUI

本文约束当前 React WebUI。新独立产品保留 Error 工作区与连续对话方向，但将 Record
与 Grill 合为调查、公开 Error 改为完整描述；见
[2026-09-23 决策](2026-09-23-error-episode-and-agent-fork.md)。

## Context

现有 CLI 的 slash commands 是功能逐步增加后的实现接口，不是 Web 产品的信息架构。把 `/record`、Grill、Teach、Drill、历史查询等逐一展开成页面或 tab，会把 CLI 的历史结构固化到 Web。

WebUI 应围绕用户正在处理的对象与任务组织，而不是围绕内部 workflow 名称组织。

## Decision

V1 WebUI 采用一个主 workspace + 左侧导航/history 的结构。

左侧长期保留：

- `New error`：创建 Error 的 action；
- `Drill`：启动一次临时练习的 action；
- Error history：长期存在的 Error 对象列表；
- `Settings`：低频设置入口，固定在底部；齿轮图标旁明确显示 `Settings`。

不把 Grill、Teach、Judge、Spec、Draft 等内部阶段做成独立主导航或工作台。

Error history 使用简短可读标题，不以数据库编号作为主要名称。raw workflow status（如 `pending-grill`）不直接作为主要用户文案。

Error history 需要用极轻量状态点表达当前处理状态：`pending-grill` 显示小红点，`pending-teach` 显示小黄点，`done` 不显示状态点。不要再在每一行附加状态文字、日期、来源等噪声。
## Error workspace

打开一个 Error 后，中间主区域使用连续 timeline，而不是 Grill / Teach tab。

逻辑顺序是：

```text
Original Error
→ Grill conversation
→ episode diagnosis result
→ Teach conversation
→ contextual next-step action
→ composer
```

原题与原始作答作为 timeline 的起始内容，向下滚动后自然离开 viewport。原题与原始作答采用 B 方案：只作为 timeline 起始内容出现一次，随滚动自然离开 viewport，不做悬浮题目栏。之后需要回看时，通过工作区左上角的当前 Error 标题这一轻量入口重新展开。

点击当前 Error 标题后展开 metadata/context，其中可查看原题、当时作答/思路、参考答案以及来源、时间和来源 Error 关系等信息。右上角三点不再承担 metadata 查看，而保留给当前会话/Error 的管理操作；只放真正支持且安全的管理动作，不把 Grill / Teach 做成菜单项或 metadata tab。

Grill 和 Teach 在 backend 仍保持各自 conversation/state，但 UI 不把数据库或 application 的存储边界直接映射成页面结构。

## New Error

New Error 以统一 composer 为入口，允许文字与图片混合输入。目标体验是通过多轮调整得到
可编辑的结构化 draft，再由用户确认保存，而不是要求用户先理解并分别填写多个底层字段。

每次调整都发送新的自然语言补充、当前预览字段和当前图片；模型保留未被明确修改的字段，
最新用户纠正覆盖旧草稿。更新成功后保留结构化预览并清空原始补充输入，返回确定性的
ready/incomplete 状态；题目缺失时仍是可继续调整的中间草稿，不是模型输出契约错误。
参考答案可选，缺失的 `user_thoughts` 必须保持缺失，且只有最终确认才进入 `record_error` 和
SQLite Error 持久化。

模型只能整理、提取用户提供的信息，不能凭空补写 `user_thoughts`。缺失的思路必须保持缺失并要求用户补充。进入 Error 的内容必须经过用户确认；模型推断不能伪装成用户 Evidence。
## Drill workspace

Drill 是一次短生命周期 action，不是长期工作台。

用户点击左侧 `Drill` 后，中间区域只需要：

```text
Drill question
→ answer composer
→ verdict
```

提交后立即 Judge：

- 正确：本次 Drill 结束，可开始下一题或返回；
- 错误：按现有 Core 语义创建 derived Error，并直接进入该 Error 的 workspace。

不要向用户展示 `Spec → Draft → Judge` 内部 pipeline，也不要把 Drill 画成某个 Error 的固定第四阶段。已判分 Drill ledger 可以继续存在于系统内部，但不是主导航对象。

## Next-step action

下一步是单个 contextual action，不是新的 destination，因此不进入 sidebar，也不附带推荐说明文字。

在 Error workspace 中，它位于 timeline 末尾、composer 上方。没有当前 Error 时不显示该 action card。

当前 action 只显示一个短标签：新 Error 的 Grill 为 `Grill`；Grill 正等待用户回答时不显示 action；Grill 完成且 Teach 尚未开始时为 `Teach`；Teach 已有对话时为 `Drill`；已完成且没有活动 Teach 对话时也为 `Drill`。活动 Teach 的 `Drill` 是一次受 CSRF 和一次性提交 token 保护的 Web mutation：先调用 `ErrGrindApplication.finish_teach()`，再进入 Drill。仅仅离开 Error 页面不会静默完成 Teach；Teach 尚未开始时，`Teach` action 仍通过 Application 正常开始/恢复。

不要声称它是“最优学习动作”。未来 V2 Policy 可以替换 action 的来源，而无需改变 UI 位置和交互。

## Responsive principle

移动端优先节约有效屏幕：不固定大块原题、不常驻 inspector、不展示内部状态机。sidebar 在窄屏可折叠/抽屉化；主区域始终优先当前题目、当前对话或当前作答。
## Boundaries

This decision changes presentation and information architecture, not Core semantics.

Preserve the existing application boundary, persistence/recovery behavior, CSRF/submission-token protections, Markdown/KaTeX safety, Drill isolation, and derived-Error semantics unless a separate design decision explicitly changes them.

Do not use this redesign to implement Issue #4 (Grill → Drill structured handoff), the future Judge semantic split, V2 Pattern/State/Policy, or new long-term learner-model concepts.

## Rationale

The Web should expose a small number of user concepts: Error objects, temporary actions, the current activity, and contextual next steps. Internal workflow stages remain implementation details unless they are genuinely useful to the learner.

This keeps the UI compatible with future changes in action ordering: the central timeline can continue to accumulate interactions without hard-coding today's `Grill → Teach → Drill` sequence into page topology.
