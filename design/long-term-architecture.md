# 长期架构

长期来看，整个系统可以抽象为：

Evidence
→ State
→ Policy
→ Action
→ New Evidence

形成一个持续优化的闭环。

当前 MVP 的最小可审计实现是：Error 记录保留 `origin`、`source_error_id` 与 `source_drill_attempt_id` provenance，Drill 判分保留为 Action ledger。该 ledger 记录干预结果，但不等同于 Future Error 减少的证明；后者需要后续真实学习事件和时间窗口分析。

---

## Evidence（证据）

Evidence 是用户行为或对话中可审查的原始观察。

Evidence 不只是真实 Error；当前 Grill 的回答和推理也是 Evidence。一次 Error 通常有多个可能成因，Grill 用问题收集能区分它们的新 Evidence。

一次 Grill 的结果是 episode-level diagnosis，不自动成为长期 Pattern。

未来还可以逐步扩展 Evidence，例如：

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

LLM 不应该替代 Policy 决定系统状态转换。

---

## Action（探查与干预）

Action 是真正作用于用户的行为，概念上分为两类：

- **Probe**：主要用于减少诊断不确定性，例如 Grill 问题或诊断变式；
- **Intervention**：主要用于改变未来行为，例如 Teach、Drill。

Drill 当前仍是针对 summary-derived mechanism 的干预；未来也可能同时承担 Probe 与 Intervention 的作用。

例如：

- Teach
- Drill
- Review
- Micro Check

Action 只是实现方式。

未来可能继续增加新的 Action，但整个系统不应该围绕某几个 Action 设计，而应该围绕 State 和 Policy 设计。
