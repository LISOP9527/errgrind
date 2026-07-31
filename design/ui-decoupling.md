# 架构解耦原则（TUI → GUI / Mobile）

为了保证从 TUI 平滑演进到 GUI 及 Mobile App，须遵循以下原则：

1. **Headless Engine（核心与 UI 解耦）**：Core Engine 纯粹负责逻辑（State 建模、Policy 决策、Action 触发），UI 仅作为渲染壳。
2. **结构化数据传递**：Engine 与 UI 之间只传递结构化数据（JSON/对象），禁止在 Core 逻辑中硬编码终端排版或颜色代码。
3. **标准数据协议**：文本与公式统一使用标准 Markdown + LaTeX 输出，确保跨平台（TUI / Web GUI / Mobile）无缝复用渲染。
4. **渲染能力由 UI 适配**：标准内容不因终端能力写回降级格式；TUI 在统一边界转换为 Unicode/线性公式，未来 GUI / Mobile 使用各自的原生 Markdown 与数学渲染器。具体约束见[终端内容渲染边界](decisions/2026-07-27-terminal-content-rendering.md)。
