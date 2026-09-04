# 结构化 Grill 诊断与变式 Probe

## Context

上一轮已经把 Grill 定义为 active diagnosis，但运行时仍只有自由文本
`grilling_summary`。候选 hypothesis、原始 Evidence 和问题为什么具有区分度都不可审计；
诊断变式也只是 Prompt 中可以离开原题的隐含能力。这样容易把 LLM 写出的 plausible
summary 误当成 Evidence-backed diagnosis。

## Decision

- 每个 Error 保存一个 nullable 的 structured `GrillDiagnosticState`。它只表示本次 Error
  的 episode-level diagnosis，不建立 `patterns` 表或长期 Pattern State。
- Grill 每轮给模型当前 state 和可引用的用户原话；模型只输出 `GrillTurnDecision` delta，
  由 Application 通过确定性 merge 追加 hypothesis、Evidence、Probe 并更新状态。
- Hypothesis claim 不可被模型改写或删除；Evidence 必须 grounded 到
  `initial_user_thoughts` 或真实 Grill user message 的精确片段。
- Probe 显式区分 `reasoning_question` 与 `variant_problem`。variant 是为了区分竞争解释的
  near-transfer 数学变式，属于 Grill，不进入普通 Drill ledger。
- `finish_supported` 与 `finish_undetermined` 取代新的 `[GRILLING_END]` 文本协议；
  `grilling_summary` 继续保存人类可读的兼容输出，Teach/Drill 仍可读取它。
- 长期 Pattern State、Candidate merge、跨 Error aggregation、人工 accepted/rejected review
  和 promotion 仍 deferred。

## Rationale

Plausible summary 不是用户行为 Evidence。不可变的原始引用加上确定性 delta merge，能防止
模型每轮重写历史并让诊断状态可回溯。诊断变式可以让竞争 hypothesis 对用户行为产生不同的
observable prediction，同时不需要现在建立长期 learner-model infrastructure。

## Consequences

- 正常 Grill turn 仍是一次 structured model call；malformed 或违反本地契约时最多再进行一次
  contract repair。
- Structured output 必须先完整校验并原子持久化，之后才通过保留的可选 callback 展示一次用户可见文本；
  Grill 不再是真正的 token-level streaming。
- 数据库 Schema 从 v2 升级到 v3。旧记录新增字段保持 NULL，不静默调用 LLM 补数据；旧 completed
  Grill 继续以 `grilling_summary` 只读，旧 partial Grill 可用完整历史 conversation 建立新 state。
- 普通 `/drill`、Teach 状态机、`drill_attempts` 和 Drill 衍生 Error 语义保持不变。

本决策只 supersede `2026-09-04-grill-as-active-diagnosis.md` 中“structured Hypothesis/Evidence
仍 deferred”的那一小部分；其 active diagnosis、episode 与长期 Pattern 边界保持有效。
