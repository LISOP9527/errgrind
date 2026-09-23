# 架构解耦原则（TUI → GUI / Mobile）

为了保证从 TUI 平滑演进到 GUI 及 Mobile App，须遵循以下原则：

1. **Headless Engine（核心与 UI 解耦）**：Core Engine 纯粹负责逻辑（State 建模、Policy 决策、Action 触发），UI 仅作为渲染壳。
2. **结构化数据传递**：Engine 与 UI 之间只传递结构化数据（JSON/对象），禁止在 Core 逻辑中硬编码终端排版或颜色代码。
3. **标准数据协议**：文本与公式统一使用标准 Markdown + LaTeX 输出，确保跨平台（TUI / Web GUI / Mobile）无缝复用渲染。
4. **渲染能力由 UI 适配**：标准内容不因终端能力写回降级格式；TUI 在统一边界转换为 Unicode/线性公式，未来 GUI / Mobile 使用各自的原生 Markdown 与数学渲染器。具体约束见[终端内容渲染边界](decisions/2026-07-27-terminal-content-rendering.md)。

当前 Python/React 实现以 `errgrind.application.ErrGrindApplication` 作为前端可复用的
application 边界。它编排现有 DB、LLM 与 Prompt 基础设施，并返回结构化的对话和
Drill 结果；它不引用 CLI、Rich 或 prompt_toolkit。CLI 只负责终端输入循环、确认、
流式 token 的终端渲染和结果展示。当前其他 frontend 应调用同一 application 操作，
不复制 Grill / Teach / Drill 流程或直接组合数据库与模型调用。

新独立产品与 MCP 的共同边界见[统一 Error 调查与 agent fork 决策](decisions/2026-09-23-error-episode-and-agent-fork.md)。
agent runtime 可以接管编排，但业务 Core 仍统一负责证据来源、提交与状态规则、公开结果和
Drill 原子记录；不能让两种入口各自建立 Error 工作流事实来源。


## 本机 Web adapter

当前正式 WebUI 使用 React + TypeScript + Vite + assistant-ui 的 `ExternalStoreRuntime`，生产构建仍由
单个 Flask/Waitress 进程同源提供。React 只拥有 product UI；Flask 与 `ErrGrindApplication` 继续负责
业务调用、恢复、安全边界和持久化，SQLite 仍是 workflow 的事实来源。每个请求独立打开 SQLite 连接，
不跨线程共享连接；进程内操作保护与一次性 submission token 防止重复 mutation。

Web 只读取公开 projection，过滤 system 消息，不序列化 Grill 诊断账本、隐藏答案或未提交的内部规格。
Record 与 conversation 图片使用 durable attachment provenance；未判分 Drill preparation 仍暂存当前 Web
进程，最终 Judge attempt 与可能派生的 Error 通过 Application 原子写入 SQLite。旧 Jinja 页面只保留为
hidden fallback / compatibility projection，不定义当前产品信息架构。迁移决策见
[React + assistant-ui WebUI 正式迁移](decisions/2026-09-14-assistant-ui-migration.md)，运行说明与限制见
[WebUI 说明](../README.md)。
