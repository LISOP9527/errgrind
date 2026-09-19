# 暂缓长期 Pattern Observation 校验

> 状态：部分 superseded。2026-09-04 的[结构化 Grill 诊断与变式 Probe](2026-09-04-structured-grill-diagnosis-and-variant-probes.md)
> 已将本次 Error 的 episode-level diagnosis、grounded Evidence 和 Probe 校验纳入当前实现；
> 本 ADR 仍适用于跨 Error 的长期 Pattern Observation 与 promotion。

## Context

ErrGrind 当时已经通过 Grill 生成 `grilling_summary`。曾考虑在此基础上立即增加跨 Error 的
结构化 Pattern Observation、用户原话 Evidence 引用校验和人工 review，但这会提前引入新的
数据模型、交互和判断边界，而 MVP 尚未证明这些复杂度是必要的。

## Decision

- 当前 MVP 继续使用 `grilling_summary` 作为人类可读兼容摘要；结构化 Grill state 负责本次 Error 的
  episode-level diagnosis，但不把它视为已确认的长期 Pattern。
- 现在不实现跨 Error 的 Pattern Observation schema、长期 Evidence quote 校验、Observation fixture、
  持久化或 review/promotion 流程。
- `design/pattern-state-proposal.md` 保留为未来演进方向，不是当前实现承诺。
- 只有用户再次明确决定进入结构化 Pattern 阶段后，才重新评估并实现 Stage 1。

## Rationale

MVP 首先需要验证 Grill 产出的自然语言 episode-level diagnosis 是否对 Teach、Drill 和未来真实使用有帮助。
在这之前增加严格 Observation 基础设施，会提高实现和审阅成本，却不一定改善当前核心实验。

## Consequences

当前系统不会把 `grilling_summary` 升级为一等 Pattern State，也不会对跨 Error 的 Pattern Observation
提供 review/promotion 流程。本次 Grill 的结构化 Evidence 仍按后续 ADR 做 grounded 校验；界面和设计
不得把 summary 或单次诊断描述成已确认、稳定或已被证明的用户 Pattern。
