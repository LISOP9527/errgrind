# ErrGrind Design Index

这个目录用于保存 ErrGrind 的长期设计、原则和关键决策，方便 vibe coding 过程中持续 review。

## 设计文档

- [长期架构](long-term-architecture.md)：Evidence -> State -> Policy -> Action -> New Evidence 的系统闭环。
- [核心原则](core-principles.md)：ErrGrind 如何围绕 Evidence、可更新 State、Policy 和减少未来 Error 设计。
- [Pattern State 演进提案](pattern-state-proposal.md)：从单次 Grill 假设逐步建立可审查、可证伪的 Pattern State，区分真实 Evidence 与干预结果。
- [“减少未来 Error”的验证策略](evaluation-strategy.md)：区分流程、模型、行为迁移和真实结果，说明 recurrence、盲法关联与学习机会分母。
- [架构解耦原则](ui-decoupling.md)：从 TUI 演进到 GUI / Mobile 时必须遵守的 Core/UI 边界。

## 决策记录

- [决策索引](decisions/README.md)：记录已经做出的设计决策、原因和影响。
- [Grill 与 Teach 会话生命周期](decisions/2026-07-27-conversation-lifecycle.md)：完成的 Grill 只读，Teach 持续追加。
- [终端内容渲染边界](decisions/2026-07-27-terminal-content-rendering.md)：标准 Markdown + LaTeX 保持在数据层，终端统一在 UI 边界降级渲染。
- [Drill 两阶段规格隔离](decisions/2026-07-28-drill-spec-isolation.md)：采用 Spec -> Draft 两阶段出题，保留原题信息边界和可人工调优的 Prompt。
- [Codex provider 通过官方 app-server 接入](decisions/2026-08-29-codex-app-server-provider.md)：使用官方 SDK 管理 ChatGPT 登录和 token，ErrGrind 只保留 provider/model 配置。
- [OCR 作为需人工校对的录题入口](decisions/2026-08-31-ocr-as-reviewed-input.md)：provider 负责图片转录，用户确认后的文本才进入现有 Error 工作流。
- [Evidence 来源与 Drill Action Ledger](decisions/2026-09-01-evidence-provenance-drill-ledger.md)：记录 Error 来源、Drill 判分与衍生 Error 的可追溯关系。
- [暂缓 Pattern Observation 校验](decisions/2026-09-02-defer-pattern-observation-validation.md)：当前 MVP 直接使用 `grilling_summary`，暂不引入结构化 Observation 与 Evidence 校验。
- [Application 工作流边界](decisions/2026-09-03-application-workflow-boundary.md)：Grill、Teach、Drill 通过 UI 无关 façade 供未来前端复用。
- [Grill 作为主动诊断](decisions/2026-09-04-grill-as-active-diagnosis.md)：Grill 通过区分候选解释收集 Evidence；一次结果只是 episode-level diagnosis，长期 Pattern 需跨证据支持。
- [结构化 Grill 诊断与变式 Probe](decisions/2026-09-04-structured-grill-diagnosis-and-variant-probes.md)：每个 Error 保存 grounded Evidence、确定性诊断 delta merge 和显式 reasoning/variant Probe；长期 Pattern 仍 deferred。

## 维护规则

- 新增功能前，先判断它是否帮助系统减少未来 Error。
- 设计说明写入 `design/` 下的主题文件。
- 具体决策写入 `design/decisions/`，并在决策索引中登记。
- `design/README.md` 是设计文档的统一入口，避免在根目录重复维护设计说明。
