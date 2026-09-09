# Drill 目标查询与判题结果展示

## Context

已完成判分的 Drill 在 `drill_attempts.drill_spec` 中保留 `target_pattern`，但用户没有查询入口。用户需要查看每道历史 Drill 针对的机制，同时要求判题完成后仅展示对错。

## Decision

- 新增 `/drills`，列出已完成判分的历史 Drill 并支持选择查看；`/drills <ID>` 按真实 Drill 记录 ID 直接查询，旧记录也可访问。
- 查询详情展示题目，以及目标机制、触发条件、错误行为、期望行为和成功信号。缺失的目标字段明确显示未记录，不重新调用模型补全。
- 查询中的 `target_pattern` 是来源 Error 的机制假设，不代表已确认的长期 Pattern。
- 判分成功后，结果弹窗只显示“正确”或“错误”，不自动展示反馈、参考答案、派生 Error ID 或附加说明。
- 判分与持久化保持原有语义：每次完成判分都记录 attempt，答错仍原子派生 `pending-grill` Error。完整判分字段继续留存于数据库。
- 查询经 Application 提供只读的最小结构化结果；CLI 负责选择和渲染，不直接展开数据库中的完整 DrillSpec。历史目标查询不展示判分反馈、参考答案或 Judge 元数据。

## Rationale

用户可按需了解练习目标，判题后的即时输出则遵循其对简洁结果的要求。复用已保存的数据能保持目标与当次出题一致，无需再次调用模型。

## Consequences

未完成判分、仅生成后取消的题目仍不属于历史查询记录。查询不改变会话状态，不更新长期 Pattern；删除 Error 对关联 Drill 历史的既有影响不变。
