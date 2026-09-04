# Drill 使用两阶段规格隔离原题

## Context

原来的单阶段 `/drill` 同时读取历史原题并生成新题，容易退化成修改数字、字母或背景的“原题换皮”。为解决这个问题，曾尝试在出题前加入 Guard、Review、Audit 等多级自审，但这使一次 Drill 需要更多模型调用、Prompt 和工程校验；同一个基础模型反复审查自己的收益也不稳定，不适合作为 MVP 基线。

当前目标是先得到一个简单、可运行、可人工调 Prompt 的版本，不依赖更强模型、固定 Pattern 分类体系或题库。

产品的质量目标是让生成题达到足够高的可接受概率，而不是保证每一道题都通过严格的语义审计。即使进入正式版，也不应为了追求单题强保证恢复多级模型审查链路。

## Decision

`/drill` 的出题部分固定为两个阶段：

1. **Spec：理解 Error 并生成 DrillSpec。** 本阶段读取最近 N 条 Error 的原题和 Grill 摘要，只选择一个 summary-derived mechanism，输出 `source_error_number`、`target_pattern`、`new_problem` 和 `difficulty`。`target_pattern` 包含 `success_signal`，用于描述“判断学生是否在本次干预中展示目标思考行为时，应观察到的信号”。因为只有本阶段看得到原题，所以“不要复述原题或只做表面改写”的要求只放在 Spec Prompt。
2. **Draft：根据 DrillSpec 出题。** 本阶段只读取程序白名单重建后的 DrillSpec，看不到原题和完整 Grill 上下文，只输出 `question` 与 `reference_answer`。Draft Prompt 只描述如何落实规格，不再强调“禁止原题换皮”。

用户作答后仍调用 Judge 判分；Judge 是答题后的评估步骤，不属于出题阶段。完整流程为：

`Spec -> Draft -> 用户作答 -> Judge`

MVP 不再运行 Guard、Review、Audit，也不在质量检查失败后重做整条生成链。

程序仅保留必要的接口边界：

- DrillSpec 和 Draft 输出必须满足最小 JSON 字段及类型契约；
- Spec 响应通过字段白名单重建，额外字段不会传给 Draft；
- 程序阻止明显的原题特征文本进入公共 DrillSpec；
- `success_signal` 同时传给 Draft 和 Judge；
- Judge 的 `is_correct` 必须是 JSON 原生布尔值，`feedback` 必须是文本；
- 字段契约错误可在当前阶段做有限纠正，API 错误直接结束本次 Drill。

两份可人工调优的 Prompt 分别保存在 `prompts/drill_spec.md` 和 `prompts/drill.md`。

质量优化以 Prompt 调整、可接受样例和真实使用中的通过率为主。当前可接受样例之一是：给定实数参数 `t`，以 `(1-t²)/(1+t²)` 和 `2t/(1+t²)` 定义 `x`、`y`，要求化简 `(1-x+y)/(1+x+y)`；该题不需要额外审查阶段才能进入用户作答。

## Rationale

两阶段已经建立最关键的信息边界：负责理解历史 Error 的模型能看到原题，负责成题的模型看不到原题。相比让同一个基础模型进行多轮自审，这条链路更容易理解、测量和人工调优，也能保持 ErrGrind 在普通模型上的可运行性。

`success_signal` 被保留，因为它把抽象 Pattern 连接到可观察的学生行为；但 MVP 暂不增加自动审校来证明题目一定能测到该信号，先通过人工样例和真实使用改进 Prompt。

## Consequences

- 正常完成一次 `/drill` 需要三次模型调用：出题前的 Spec、Draft 两次，以及用户作答后的 Judge 一次。
- Draft 与原题之间有明确的信息隔离，但程序只能拦截明显文本泄漏，不能保证语义上绝不换皮，也不自动证明数学质量；这些问题先通过人工 Prompt 调优和真实样例观察。
- 产品验收关注一段时间内生成题的可接受率；偶发的不理想题目不自动触发运行时 Guard、Review 或 Audit。
- 字段契约纠正或底层非法 JSON 重试可能增加调用次数，但不会引入新的质量阶段。
- 更强模型、Pattern 固定分类、题库检索和额外审校链均不进入当前 MVP；只有在两阶段 Prompt 调优仍无法达到目标时再评估。
