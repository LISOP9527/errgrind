# ErrGrind Design Index

这个目录用于保存 ErrGrind 的长期设计、原则和关键决策，方便 vibe coding 过程中持续 review。

## 设计文档

- [长期架构](long-term-architecture.md)：Evidence -> State -> Policy -> Action -> New Evidence 的系统闭环。
- [核心原则](core-principles.md)：ErrGrind 为什么围绕 Pattern、State、Policy 和减少未来 Error 设计。
- [架构解耦原则](ui-decoupling.md)：从 TUI 演进到 GUI / Mobile 时必须遵守的 Core/UI 边界。

## 决策记录

- [决策索引](decisions/README.md)：记录已经做出的设计决策、原因和影响。
- [Grill 与 Teach 会话生命周期](decisions/2026-07-27-conversation-lifecycle.md)：完成的 Grill 只读，Teach 持续追加。
- [终端内容渲染边界](decisions/2026-07-27-terminal-content-rendering.md)：标准 Markdown + LaTeX 保持在数据层，终端统一在 UI 边界降级渲染。
- [Drill 两阶段规格隔离](decisions/2026-07-28-drill-spec-isolation.md)：采用 Spec -> Draft 两阶段出题，保留原题信息边界和可人工调优的 Prompt。
- [Codex provider 通过官方 app-server 接入](decisions/2026-08-29-codex-app-server-provider.md)：使用官方 SDK 管理 ChatGPT 登录和 token，ErrGrind 只保留 provider/model 配置。

## 维护规则

- 新增功能前，先判断它是否帮助系统减少未来 Error。
- 设计说明写入 `design/` 下的主题文件。
- 具体决策写入 `design/decisions/`，并在决策索引中登记。
- `design/README.md` 是设计文档的统一入口，避免在根目录重复维护设计说明。
