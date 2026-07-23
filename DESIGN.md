# 长期架构

长期来看，整个系统可以抽象为：

Evidence
→ State
→ Policy
→ Action
→ New Evidence

形成一个持续优化的闭环。

---

## Evidence（证据）

Evidence 是用户行为的原始观察。

MVP 中，Evidence **只来自真实 Error**。

这样可以保证 Pattern 的准确性，降低误报。

未来可以逐步扩展 Evidence，例如：

- 思考过程
- Chat
- Coding
- Near Miss（差点犯错）
- Micro Check
- 其它行为数据

但这些都不是 MVP 的内容。

---

## State（认知模型）

State 是系统对用户当前 Thinking Model 的内部表示。

State 不应该记录所有信息。

**只有能够预测未来 Error，或者能够影响未来优化决策的信息，才应该进入 State。**

State 是内部数据结构，而不是用户界面。

用户看到的永远应该是优化后的结果，而不是 State 本身。

---

## Policy（策略）

Policy 根据当前 State 决定下一步应该做什么。

例如：

- 是否继续收集 Evidence
- 是否进行 Grill
- 是否 Teach
- 是否生成 Drill
- 是否安排复习
- 是否进行 Pattern Review

Policy 决定 **做什么（What）**。

LLM 更负责 **如何做好（How）**。

---

## Action（干预）

Action 是真正作用于用户的行为。

例如：

- Grill
- Teach
- Drill
- Review
- Micro Check

Action 只是实现方式。

未来可能继续增加新的 Action，但整个系统不应该围绕某几个 Action 设计，而应该围绕 State 和 Policy 设计。

---

# 核心原则

Error 的价值，在于它暴露了 Pattern。

Pattern 的价值，在于它能够预测未来的 Error。

State 的价值，在于保存这些 Pattern。

Policy 的价值，在于决定最有效的下一步。

Action 的价值，在于真正减少未来的 Error。

因此：

> **ErrGrind 并不是在分析 Error，而是在利用 Error 建立用户模型，并利用这个模型帮助用户减少未来的 Error。**

以后所有新增功能，都应该回答一个问题：

> **它是否能够帮助系统减少未来的 Error？**

如果不能，那么即使它很有趣，也不应该优先实现。

# 架构解耦原则（TUI → GUI / Mobile）

为了保证从 TUI 平滑演进到 GUI 及 Mobile App，须遵循以下原则：

1. **Headless Engine（核心与 UI 解耦）**：Core Engine 纯粹负责逻辑（State 建模、Policy 决策、Action 触发），UI 仅作为渲染壳。
2. **结构化数据传递**：Engine 与 UI 之间只传递结构化数据（JSON/对象），禁止在 Core 逻辑中硬编码终端排版或颜色代码。
3. **标准数据协议**：文本与公式统一使用标准 Markdown + LaTeX 输出，确保跨平台（TUI / Web GUI / Mobile）无缝复用渲染。

# 一些待考虑的问题

- mvp做完后，是否fork一个agent？
- 是否考虑做成某个agent的插件？