# Evidence 来源与 Drill Action Ledger

## Context

ErrGrind 已能保存 Error 与 Grill/Teach 对话，但原记录无法说明来源，Drill 判分也只在界面短暂显示。答错衍生题与历史 Error 没有 lineage，无法区分录入证据、OCR 证据和干预结果。

## Decision

- `error_records` 增加 `origin`（`unknown`、`record`、`ocr`、`drill`）、`source_error_id` 与 `source_drill_attempt_id`。旧行统一迁移为 `unknown`，不从文本推断来源。
- 新增 `drill_attempts`，原子保存选中的 source Error、完整 DrillSpec、题目、作答、判分与可选衍生 Error。
- `drill_attempts` 同时保存 provider/model 及未 format Judge 模板和 canonical Judge schema 的 SHA-256；旧 attempt 迁移为 `unknown`，不可与新版本直接合并比较。
- 数据库使用 SQLite `user_version` 记录当前 Schema 版本；旧程序遇到更高版本时必须停止写入并提示升级。
- Drill 正确和错误都记录为 Action 结果；取消、API 失败和非法判分不写结果。
- 删除 Error 时清除其子 Error 的结构化来源字段，并删除直接关联的 Drill ledger，明确表示来源已不可追溯。

## Rationale

这先保存 Evidence provenance 和 Action 结果的最小可审计链路，而不在本次伪造 Pattern 实体或固定分类体系。Drill 账本是原始干预记录，不会自动晋升为 Thinking Model 的 State，也不会被当作真实学习场景中的 Future Error。

## Consequences

Drill 结果可以按来源和 Judge 版本查询、统计和追溯，但 Drill 正确率只是干预记录，不是未来真实 Error 减少的证明。删除 Error 会改变累计统计；未来仍需真实学习事件与时间窗口分析，才能评估 Pattern 是否被改变。

新建数据库启用外键：删除源 Error 会级联删除相关 attempt，并将衍生 Error 的来源字段清空。旧库通过 `ALTER TABLE` 增列时无法给原表补上同等的外键约束，因此删除逻辑仍会显式清理关联；这是迁移兼容性的限制。
