# Grill 与 Teach 会话生命周期

## Context

原实现允许已完成的 Grill 再次进入对话，并清空已有的 Teach 和 Grill 摘要；Teach 再次进入时则可能丢弃旧会话、从新上下文重新开始。这两种行为都会改写已经形成的 Evidence，也让用户无法把 Teach 当作可随时返回的持续讨论。

## Decision

- Grill 正常完成后即成为只读记录。在 `/resume` 中再次按 `g` 只查看完整对话，不再调用 LLM，也不改变状态、摘要或 Teach 记录。
- Grill 因 `Ctrl+C`、轮数上限或 API 错误中断时保持 `pending-grill`，再次按 `g` 从已保存对话继续。
- Teach 不再使用文本退出词。用户按 `Ctrl+C` 保存并退出，error 进入 `done`。
- Teach 会话可随时通过 `t` 再次进入，并始终沿用已有对话；如果上次停在用户消息，则先补全该回复，不提供覆盖旧会话的重开逻辑。
- `pending-grill` 状态禁止进入 Teach，即使已经保存了 partial Grill 对话。

## Rationale

完成的 Grill 是对当前 Error 形成的 episode-level diagnosis 及其 Evidence，应保持稳定和可审阅；它不是已确认的跨 Error Pattern。Teach 是基于这份诊断 Evidence 的干预讨论，允许持续追加比反复重置更符合用户自然复习和追问的方式。

## Consequences

- 状态机仍为 `pending-grill -> pending-teach -> done`，无需修改数据库 schema。
- `grilling_summary` 和已保存 Teach 不再因按 `g` 被清空。
- `done` 表示用户至少进入 Teach 并主动退出过，不表示 Teach 对话永久关闭。
- Teach 的退出按键统一为 `Ctrl+C`，该按键是正常保存操作，不显示错误。
