# Single-workspace WebUI

## Context

现有 CLI 的 slash commands 是功能逐步增加后的实现接口，不是 Web 产品的信息架构。把 `/record`、Grill、Teach、Drill、历史查询等逐一展开成页面或 tab，会把 CLI 的历史结构固化到 Web。

WebUI 应围绕用户正在处理的对象与任务组织，而不是围绕内部 workflow 名称组织。

## Decision

V1 WebUI 采用一个主 workspace + 左侧导航/history 的结构。

左侧长期保留：

- `New error`：创建 Error 的 action；
- `Drill`：启动一次临时练习的 action；
- Error history：长期存在的 Error 对象列表；
- `Config`：低频设置入口，固定在底部。

不把 Grill、Teach、Judge、Spec、Draft 等内部阶段做成独立主导航或工作台。

Error history 使用简短可读标题，不以数据库编号作为主要名称。raw workflow status（如 `pending-grill`）不直接作为主要用户文案。
## Error workspace

打开一个 Error 后，中间主区域使用连续 timeline，而不是 Grill / Teach tab。

逻辑顺序是：

```text
Original Error
→ Grill conversation
→ episode diagnosis result
→ Teach conversation
→ contextual next-step recommendation
→ composer
```

原题与原始作答作为 timeline 的起始内容，向下滚动后自然离开 viewport。顶部保留一个轻量“题目”入口，可随时重新打开当前 Error 的原题/作答内容；避免在手机上永久占据有效屏幕。

顶部另有轻量 metadata 入口，只展示来源、时间、关联 Drill/Error、内部状态的用户友好表达等元数据。不要把 Grill / Teach 做成 metadata tab。

Grill 和 Teach 在 backend 仍保持各自 conversation/state，但 UI 不把数据库或 application 的存储边界直接映射成页面结构。

## New Error

New Error 以统一 composer 为入口，允许文字与图片混合输入。目标体验是一次提交后得到可编辑的结构化 draft，再由用户确认保存，而不是要求用户先理解并分别填写多个底层字段。

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

## Next-step recommendation

Recommendation 是 contextual suggestion，不是新的 destination，因此不进入 sidebar。

在 Error workspace 中，它位于 timeline 末尾、composer 上方；在没有当前 Error 的空状态中，可作为中央区域的轻量 action card。

V1 recommendation 可以由当前确定性 workflow state 产生，例如继续未完成 interaction、开始 Teach、尝试 Drill 或录入 Error。不要声称它是“最优学习动作”。未来 V2 Policy 可以替换 recommendation 的来源，而无需改变 UI 位置和交互。

## Responsive principle

移动端优先节约有效屏幕：不固定大块原题、不常驻 inspector、不展示内部状态机。sidebar 在窄屏可折叠/抽屉化；主区域始终优先当前题目、当前对话或当前作答。
## Boundaries

This decision changes presentation and information architecture, not Core semantics.

Preserve the existing application boundary, persistence/recovery behavior, CSRF/submission-token protections, Markdown/KaTeX safety, Drill isolation, and derived-Error semantics unless a separate design decision explicitly changes them.

Do not use this redesign to implement Issue #4 (Grill → Drill structured handoff), the future Judge semantic split, V2 Pattern/State/Policy, or new long-term learner-model concepts.

## Rationale

The Web should expose a small number of user concepts: Error objects, temporary actions, the current activity, and contextual next steps. Internal workflow stages remain implementation details unless they are genuinely useful to the learner.

This keeps the UI compatible with future changes in action ordering: the central timeline can continue to accumulate interactions without hard-coding today's `Grill → Teach → Drill` sequence into page topology.