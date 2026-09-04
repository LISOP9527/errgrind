# 暂缓 Pattern Observation 校验

## Context

ErrGrind 当前已经通过 Grill 生成 `grilling_summary`。曾考虑在此基础上立即增加结构化
Pattern Observation、用户原话 Evidence 引用校验和人工 review，但这会提前引入新的数据模型、
交互和判断边界，而 MVP 尚未证明这些复杂度是必要的。

## Decision

- 当前 MVP 继续直接使用 `grilling_summary` 表达本次 Grill 的 episode-level diagnosis；不把它视为已确认的长期 Pattern。
- 现在不实现 Pattern Observation schema、Evidence quote 校验、Observation fixture、持久化或 review 流程。
- `design/pattern-state-proposal.md` 保留为未来演进方向，不是当前实现承诺。
- 只有用户再次明确决定进入结构化 Pattern 阶段后，才重新评估并实现 Stage 1。

## Rationale

MVP 首先需要验证 Grill 产出的自然语言 episode-level diagnosis 是否对 Teach、Drill 和未来真实使用有帮助。
在这之前增加严格 Observation 基础设施，会提高实现和审阅成本，却不一定改善当前核心实验。

## Consequences

当前系统不会验证 `grilling_summary` 中的判断是否逐字来自用户 Evidence，也不会把它升级为一等
Pattern State。界面和设计不得把 summary 描述成已确认、稳定或已被证明的用户 Pattern。
