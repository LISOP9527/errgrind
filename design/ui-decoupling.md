# 架构解耦原则（TUI → GUI / Mobile）

为了保证从 TUI 平滑演进到 GUI 及 Mobile App，须遵循以下原则：

1. **Headless Engine（核心与 UI 解耦）**：Core Engine 纯粹负责逻辑（State 建模、Policy 决策、Action 触发），UI 仅作为渲染壳。
2. **结构化数据传递**：Engine 与 UI 之间只传递结构化数据（JSON/对象），禁止在 Core 逻辑中硬编码终端排版或颜色代码。
3. **标准数据协议**：文本与公式统一使用标准 Markdown + LaTeX 输出，确保跨平台（TUI / Web GUI / Mobile）无缝复用渲染。
4. **渲染能力由 UI 适配**：标准内容不因终端能力写回降级格式；TUI 在统一边界转换为 Unicode/线性公式，未来 GUI / Mobile 使用各自的原生 Markdown 与数学渲染器。具体约束见[终端内容渲染边界](decisions/2026-07-27-terminal-content-rendering.md)。

当前 MVP 以 `errgrind.application.ErrGrindApplication` 作为前端可复用的
application 边界。它编排现有 DB、LLM 与 Prompt 基础设施，并返回结构化的对话和
Drill 结果；它不引用 CLI、Rich 或 prompt_toolkit。CLI 只负责终端输入循环、确认、
流式 token 的终端渲染和结果展示。未来 MCP、GUI 或 Mobile 应调用同一 application
操作，而不是复制 Grill / Teach / Drill 流程或直接组合数据库与模型调用。
