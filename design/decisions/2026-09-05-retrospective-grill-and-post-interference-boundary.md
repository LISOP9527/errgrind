# 回顾性 Grill 证据与干预后边界

## Context

Grill 需要从一次真实 Error 及用户的回答中推断 Error 发生当时的 failure mechanism。录题时的思路通常是在错误发生之后回忆和重建，可能已经受到结果、提问或后续理解的影响；如果把它当作无噪声事实，或把当前理解当作过去思路，就会夸大诊断结论。Teach、Drill 等干预之后的表现也容易被误读为原始机制发生了因果改变。

## Decision

- authentic Error 是 episode diagnosis 的 anchor；Error-time failure mechanism 是 inference target。
- retrospective reconstruction（包括录题时的用户思路）作为 noisy Evidence 保存，并与用户当前理解、反思或后来产生的解释区分开。
- 当前 MVP 不追踪 post-interference state transition 或 causal chain。干预后的观察仍可保存用于审计和未来研究，但不作某次 Teach/Drill 导致变化的因果归因。
- Grill 的 primary objective 是 diagnosis 而非 Teach；Prompt 和评估不以零 learning effect 为要求，但也不把 incidental learning effect 当作诊断结论。
- 新的当前 Error 如果不能归因 original Error 的 failure mechanism，是 non-discriminating Evidence：可保存观察，但不能作为原始机制的支持、反驳或 recurrence 证据。
- 一次 episode-level diagnosis 不等于 confirmed、verified 或 mastered Pattern。长期 Pattern 需要未来真实且独立的 Evidence，才能逐步支持、更新或推翻。

## Rationale

这组边界保留了对真实错误的可审计锚点，同时承认回忆资料的噪声，避免把用户在诊断后说出的理解倒灌成错误发生时的事实。保存干预后观察可以避免丢失信息，但不在缺少独立机会、时间顺序和反事实设计时制造因果结论。把 Grill 与 Teach 分开，能够评估问题是否区分候选机制，而不是把用户是否学会回答当作诊断质量。

## Consequences

- Prompt、设计文档和评估必须使用 authentic Error、retrospective noisy Evidence 与 current understanding 的区分。
- 不新增 temporal/reliability 字段，也不改变现有 structured JSON contract；无可引用 Evidence 时仍输出空的 `new_evidence`。
- 不新增 schema、Pattern implementation 或 post-interference state tracking。
- 未来若要建立长期 Pattern，必须引入未来 authentic/independent Evidence 及可审查的关联规则；当前 episode diagnosis 仍是单次 Error 的结果。
