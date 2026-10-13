# ErrGrind Design Index

这个目录用于保存 ErrGrind 的长期设计、原则和关键决策，方便 vibe coding 过程中持续 review。

## 设计文档

- [版本规划 / Version Plan](version-plan.md)：V1 核心闭环、独立产品 fork 与 MCP 并行验证、V2 长期能力的开发方向。
- [长期架构](long-term-architecture.md)：Evidence -> State -> Policy -> Action -> New Evidence 的系统闭环。
- [核心原则](core-principles.md)：ErrGrind 如何围绕 Evidence、可更新 State、Policy 和减少未来 Error 设计。
- [Pattern State 演进提案](pattern-state-proposal.md)：从单次 Grill 假设逐步建立可审查、可证伪的 Pattern State，区分真实 Evidence 与干预结果。
- [“减少未来 Error”的验证策略](evaluation-strategy.md)：区分流程、模型、行为迁移和真实结果，说明 recurrence、盲法关联与学习机会分母。
- [架构解耦原则](ui-decoupling.md)：当前 Application/Web 边界及新 fork 的业务 Core 边界；[WebUI 安装与使用](../README.md)。
- [错题录入与图片输入](record-input.md)：当前 React 产品的三字段草稿与图片路径；新产品的统一调查见下方决策。

## 决策记录

- [统一 Error 调查与独立产品 agent fork](decisions/2026-09-23-error-episode-and-agent-fork.md)：Record 与 Grill 在新产品中合为连续调查，公开 Error 使用完整可修订描述；选择 dsh 作为首个 fork 验证基础，并保留与 MCP 共享的业务 Core。
- [Web Settings 编辑共享配置](decisions/2026-09-14-web-settings.md)：在 WebUI 动态读取 Provider 模型目录，选择模型和 Codex effort，并自动、安全地保存完整共享配置。
- [Drill 目标查询与判题结果展示](decisions/2026-09-08-drill-target-query-and-verdict.md)：按需查询历史题目的目标机制，判题后仅展示对错。
- [Drill 答题图片输入](decisions/2026-09-08-drill-answer-ocr.md)：历史 OCR 方案；Web 路径已由直接多模态附件决策 supersede，CLI 兼容能力可暂留。
- [决策索引](decisions/README.md)：记录已经做出的设计决策、原因和影响。
- [Grill 与 Teach 会话生命周期](decisions/2026-07-27-conversation-lifecycle.md)：完成的 Grill 只读，Teach 持续追加。
- [终端内容渲染边界](decisions/2026-07-27-terminal-content-rendering.md)：标准 Markdown + LaTeX 保持在数据层，终端统一在 UI 边界降级渲染。
- [Drill 两阶段规格隔离](decisions/2026-07-28-drill-spec-isolation.md)：采用 Spec -> Draft 两阶段出题，保留原题信息边界和可人工调优的 Prompt。
- [模型用量日志](decisions/2026-09-08-model-usage-logging.md)：记录各阶段服务端 token usage、修复与失败，先测量再优化。
- [Codex OAuth 生成直连 Responses](decisions/2026-09-07-codex-direct-responses.md)：生成与模型目录直连，隔离 Prompt；官方 SDK 仅保留登录与刷新。
- [原 Codex app-server 接入决策](decisions/2026-08-29-codex-app-server-provider.md)：历史方案；生成和凭据读取边界已由直连决策替代。
- [OCR 作为需人工校对的录题入口](decisions/2026-08-31-ocr-as-reviewed-input.md)：历史 OCR 方案；Web 路径已 supersede，CLI `/ocr` 兼容性仍可保留。
- [在录题字段内整合图片识别](decisions/2026-09-06-record-field-image-input.md)：历史 Web 字段 OCR 方案，已由直接多模态附件决策 supersede。
- [直接多模态 Web 输入](decisions/2026-09-11-direct-multimodal-web-input.md)：Web 的 Record、Error 对话和 Drill 直接发送图片；SQLite 保存附件 provenance 与恢复所需的字节。
- [assistant-ui conversation workspace spike](decisions/2026-09-14-assistant-ui-spike.md)：用 ExternalStoreRuntime 验证可替换的对话 UI 基础设施，并记录当前 Record 字段级图片语义的持久化限制。
- [React + assistant-ui WebUI 正式迁移](decisions/2026-09-14-assistant-ui-migration.md)：将 React 工作区接入单端口生产服务，保留 Flask/Application/SQLite 业务权威和原始附件限制。
- [Evidence 来源与 Drill Action Ledger](decisions/2026-09-01-evidence-provenance-drill-ledger.md)：记录 Error 来源、Drill 判分与衍生 Error 的可追溯关系。
- [暂缓长期 Pattern Observation 校验](decisions/2026-09-02-defer-pattern-observation-validation.md)：跨 Error 的 Observation 与 promotion 仍 deferred；本次 episode-level structured Grill 由 2026-09-04 ADR 约束。
- [Application 工作流边界](decisions/2026-09-03-application-workflow-boundary.md)：Grill、Teach、Drill 通过 UI 无关 façade 供未来前端复用。
- [Grill 作为主动诊断](decisions/2026-09-04-grill-as-active-diagnosis.md)：Grill 通过区分候选解释收集 Evidence；一次结果只是 episode-level diagnosis，长期 Pattern 需跨证据支持。
- [结构化 Grill 诊断与变式 Probe](decisions/2026-09-04-structured-grill-diagnosis-and-variant-probes.md)：每个 Error 保存 grounded Evidence、确定性诊断 delta merge 和显式 reasoning/variant Probe；长期 Pattern 仍 deferred。
- [回顾性 Grill 证据与干预后边界](decisions/2026-09-05-retrospective-grill-and-post-interference-boundary.md)：以 authentic Error 为锚点，区分回顾性 noisy Evidence 与当前理解；保存但不因果归因干预后观察，长期 Pattern 仍需未来独立 Evidence。

## 维护规则

- 新增功能前，先判断它是否帮助系统减少未来 Error。
- 设计说明写入 `design/` 下的主题文件。
- 具体决策写入 `design/decisions/`，并在决策索引中登记。
- `design/README.md` 是设计文档的统一入口，避免在根目录重复维护设计说明。
