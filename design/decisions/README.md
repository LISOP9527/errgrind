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

- [统一 Error 调查与独立产品 agent fork](2026-09-23-error-episode-and-agent-fork.md)：新产品以可恢复 episode 承载连续调查，公开 Error 使用完整描述；dsh 是首个 fork 验证基础。当前 React/Application 仍按既有契约运行。
- [Web Settings 编辑共享配置](2026-09-14-web-settings.md)：Settings 编辑共享模型与工作流参数，密钥不回显，保存后更新当前 Web 进程。
- [Single-workspace WebUI](2026-09-10-single-workspace-webui.md)：Web 围绕 Error 对象、临时 action 与单一主 workspace 组织，不把 CLI slash commands 或内部 workflow 阶段展开成页面结构。
- [Drill 目标查询与判题结果展示](2026-09-08-drill-target-query-and-verdict.md)：通过 `/drills` 查看历史目标机制，判题后的即时结果仅展示正确或错误。
- [Drill 答题图片输入](2026-09-08-drill-answer-ocr.md)：复用字段图片输入交互，当前答案校对后进入 Judge。
- [直接多模态 Web 输入](2026-09-11-direct-multimodal-web-input.md)：Record、Error 对话和 Drill 直接发送图片，保留附件 provenance 与恢复所需的字节。
- [assistant-ui conversation workspace spike](2026-09-14-assistant-ui-spike.md)：历史基础设施 spike；正式迁移与 durable pending attachment 结论见后续 ADR。
- [React + assistant-ui WebUI 正式迁移](2026-09-14-assistant-ui-migration.md)：React 工作区接入单端口生产服务，保留 Flask/Application/SQLite 业务权威。
- [Grill 与 Teach 会话生命周期](2026-07-27-conversation-lifecycle.md)：完成的 Grill 只读，partial Grill 可恢复，Teach 作为可持续进入的单一会话。
- [终端内容渲染边界](2026-07-27-terminal-content-rendering.md)：所有动态内容通过统一 UI 入口渲染，终端适配不污染标准 Markdown + LaTeX 数据。
- [Drill 两阶段规格隔离](2026-07-28-drill-spec-isolation.md)：先从历史 Error 生成公共 DrillSpec，再由看不到原题的 Draft 出题；MVP 不引入多级自审。
- [模型用量日志](2026-09-08-model-usage-logging.md)：记录各阶段服务端 token usage、修复与失败，先测量再优化。
- [Codex OAuth 生成直连 Responses](2026-09-07-codex-direct-responses.md)：生成与模型目录直连，隔离 Prompt；官方 SDK 仅保留登录与刷新。
- [原 Codex app-server 接入决策](2026-08-29-codex-app-server-provider.md)：历史方案；生成和凭据读取边界已由直连决策替代。
- [OCR 作为需人工校对的录题入口](2026-08-31-ocr-as-reviewed-input.md)：历史 CLI/Web OCR 入口；Web 直接多模态输入已 supersede 其 Web 路径，CLI 兼容能力可暂留。
- [在录题字段内整合图片识别](2026-09-06-record-field-image-input.md)：历史 Web 字段 OCR 方案，已由直接多模态附件决策 supersede；保留历史背景。
- [Evidence 来源与 Drill Action Ledger](2026-09-01-evidence-provenance-drill-ledger.md)：记录来源 provenance 与 Drill 干预账本；不把正确率当作未来 Error 减少证明。
- [暂缓长期 Pattern Observation 校验](2026-09-02-defer-pattern-observation-validation.md)：跨 Error 的 Pattern Observation 与 promotion 仍 deferred；episode-level structured Grill 已由后续 ADR 纳入当前实现。
- [Application 工作流边界](2026-09-03-application-workflow-boundary.md)：以 UI 无关的 application façade 编排 Grill、Teach、Drill，CLI 退为终端 adapter。
- [Grill 作为主动诊断](2026-09-04-grill-as-active-diagnosis.md)：Grill 区分候选解释并允许不确定结果；一次 Grill 不是已确认的长期 Pattern。
- [结构化 Grill 诊断与变式 Probe](2026-09-04-structured-grill-diagnosis-and-variant-probes.md)：结构化保存单次 episode diagnosis，Evidence 必须 grounded，variant 属于 Grill 而非 Drill；取代 defer ADR 中对本次 episode 校验的旧范围。
- [回顾性 Grill 证据与干预后边界](2026-09-05-retrospective-grill-and-post-interference-boundary.md)：以 authentic Error 为锚点，回顾性思路是 noisy Evidence；不追踪干预后因果链，长期 Pattern 需未来独立 Evidence。
