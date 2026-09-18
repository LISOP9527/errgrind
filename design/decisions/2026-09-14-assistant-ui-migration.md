# React + assistant-ui WebUI 正式迁移

## Context

ErrGrind WebUI 需要在一个 `errgrind web` 进程内提供生产可用的 React 主工作区。现有 Flask、`ErrGrindApplication` 与 SQLite 继续拥有业务工作流、状态、持久化、CSRF、一次性 token、附件来源与恢复语义。

## Decision

- `/` 与 `/errors/<id>` 提供包内构建的 React + assistant-ui shell；`/drill` 与 `/config` 暂时保留 Jinja。
- React 只负责对话呈现、输入、附件预览与导航；Record 草稿、Error finalize、Grill、Teach、Next Step 与 Teach → Drill 仍调用现有 Application 操作。
- Flask 提供最小公开 bootstrap、Error workspace 与同源附件读取投影。公开投影不包含 `grilling_diagnostic_state`、隐藏答案键或内部 prompt 状态；附件读取必须按 Error 所有权校验。
- 当前 TEXT 字段与初始附件持久化契约保持不变。图片作为可编辑/确认的原始附件保留，不伪造字段级多模态语义，也不通过 OCR 扁平化来填充旧字段。
- assistant-ui attachment adapter 按附件 id/ref 保存 File，并从 `incoming.attachments` 与 image parts 两处解析，以支持纯图片 Record、Grill 与 Teach 消息。

## Rationale

这保持了 WebUI 的 Error-centric 信息架构和 Application 薄适配边界，同时允许 assistant-ui 复用成熟的 composer、消息、附件与 Markdown/KaTeX 呈现能力。正式构建产物位于 `errgrind.web` 包内，生产运行不依赖 Node/Vite。

## Consequences

Record 是连续对话中的结构化草稿展示，而不是三个 textarea；题目缺失是可继续的中间状态。现有 Error 的上下文、Grill/Teach 对话、单一诊断摘要、附件与 Next Step 在同一 React 工作区呈现。字段级图片含义与更深的多模态 schema 留给后续 Core 变更。

## Follow-up status

后续迁移已把 Drill 与 Settings 的可见界面也纳入 React workspace；对应 Jinja 仍只作为 hidden
fallback / compatibility projection。Record 图片也改为 durable pending attachment：上传后由后端暂存，
finalize 时归属新 Error，因此不再依赖浏览器一直持有最初的 `File`。这些变化不改变本 ADR 的核心边界：
React 负责 UI，Flask / `ErrGrindApplication` / SQLite 继续拥有 workflow 与 persistence authority。
