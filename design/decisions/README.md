# Decision Index

这个目录用于记录关键设计决策。

## 记录格式

每个决策建议单独建文件：

```text
YYYY-MM-DD-short-title.md
```

建议包含：

- Context：当时的问题和约束
- Decision：做出的选择
- Rationale：为什么这样选
- Consequences：带来的影响和后续注意事项

## 当前决策

- [Grill 与 Teach 会话生命周期](2026-07-27-conversation-lifecycle.md)：完成的 Grill 只读，partial Grill 可恢复，Teach 作为可持续进入的单一会话。
- [终端内容渲染边界](2026-07-27-terminal-content-rendering.md)：所有动态内容通过统一 UI 入口渲染，终端适配不污染标准 Markdown + LaTeX 数据。
- [Drill 两阶段规格隔离](2026-07-28-drill-spec-isolation.md)：先从历史 Error 生成公共 DrillSpec，再由看不到原题的 Draft 出题；MVP 不引入多级自审。
- [Codex OAuth 生成直连 Responses](2026-09-07-codex-direct-responses.md)：独立构造 Prompt 与原生消息，官方 SDK 保留登录、刷新和模型目录。
- [原 Codex app-server 接入决策](2026-08-29-codex-app-server-provider.md)：历史方案；生成和凭据读取边界已由直连决策替代。
- [OCR 作为需人工校对的录题入口](2026-08-31-ocr-as-reviewed-input.md)：图片识别结果必须经人工编辑确认，数据库只保存确认后的文本。
- [在录题字段内整合图片识别](2026-09-06-record-field-image-input.md)：三个字段独立添加图片，识别后编辑，最终只保存一条 Error。
- [Evidence 来源与 Drill Action Ledger](2026-09-01-evidence-provenance-drill-ledger.md)：记录来源 provenance 与 Drill 干预账本；不把正确率当作未来 Error 减少证明。
- [暂缓 Pattern Observation 校验](2026-09-02-defer-pattern-observation-validation.md)：当前 MVP 继续使用 `grilling_summary`，结构化 Observation 与 Evidence 校验留待以后明确决定。
- [Application 工作流边界](2026-09-03-application-workflow-boundary.md)：以 UI 无关的 application façade 编排 Grill、Teach、Drill，CLI 退为终端 adapter。
- [Grill 作为主动诊断](2026-09-04-grill-as-active-diagnosis.md)：Grill 区分候选解释并允许不确定结果；一次 Grill 不是已确认的长期 Pattern。
- [结构化 Grill 诊断与变式 Probe](2026-09-04-structured-grill-diagnosis-and-variant-probes.md)：结构化保存单次 episode diagnosis，Evidence 必须 grounded，variant 属于 Grill 而非 Drill。
- [回顾性 Grill 证据与干预后边界](2026-09-05-retrospective-grill-and-post-interference-boundary.md)：以 authentic Error 为锚点，回顾性思路是 noisy Evidence；不追踪干预后因果链，长期 Pattern 需未来独立 Evidence。
