# Codex 真实工作流样本（2026-08-31）

这组 fixture 来自一次使用 `gpt-5.6-sol` 和隔离 SQLite 数据库完成的真实 ErrGrind
工作流，用于人工审阅，并为以后选择长期回归或语义评测样本提供候选材料。它不包含正式用户数据、
登录信息、token 或 API key。

## 文件

- `cases.json`：相对稳定的测试输入、模拟学生在 Grill 中的回答，以及建议审阅的 Pattern 性质。
- `snapshots.json`：当次真实运行产生的完整 ErrorRecord 内容，包括 Grill/Teach 对话和 Drill
  衍生 Error。模型措辞会漂移，因此它是审阅快照，不是逐字 golden output。

## 如何用于长期测试

适合做稳定断言的内容：

- 输入字段完整，学生思路确实是错误的但具有可理解的推理来源；
- Grill 只提一个问题、不提前教学，并最终产生 `[GRILLING_END]` 对应的状态迁移；
- 摘要覆盖 `review_targets` 中的思维机制，而不是简单归因于“粗心”或“计算错”；
- Teach 后状态进入 `done`；错误 Drill 作答只新增一条 `pending-grill` 记录。

不建议做逐字断言的内容：

- Grill 问题、摘要或 Teach 讲解的具体措辞；
- 对话轮数必须与快照完全相同；
- Drill 每次必须生成同一道题。

将某个样本提升为长期在线评测前，应为 `review_targets` 定义人工 rubric 或语义判定器，并记录
provider、model 和 prompt 版本。离线单元测试不得直接调用真实订阅接口。
