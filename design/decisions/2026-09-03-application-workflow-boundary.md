# Application 工作流边界

## Context

Grill、Teach 与 Drill 的状态转换、Prompt 组装、LLM 调用、输出契约校验和数据库
持久化曾直接写在 CLI command handler 中。任何新前端若要复用这些工作流，都必须 import
终端模块、模拟输入弹窗，或自行重新组合数据库和模型调用；这也使持久化不变量容易在不同
入口漂移。

## Decision

- 新增轻量的 `errgrind.application.ErrGrindApplication` façade 作为当前前端调用的
  application 边界，并按 Grill、Teach、Drill 拆分内部工作流模块。
- application 返回 dataclass / enum 结果并抛出业务异常；不得依赖 CLI、Rich、
  prompt_toolkit 或终端状态。
- Grill 的 application API 接受可选 `on_token` callback，以保留 provider stream_chat
  能力而不把渲染带入核心。每次调用从 SQLite 会话重建事实，并在 bootstrap、用户回答和
  模型失败等安全点持久化。
- CLI 保留输入循环、Ctrl+C / EOF 的交互语义、终端流式渲染、popup、确认和中文展示，
  但通过 façade 完成工作流。Drill 仍严格经过 `Spec -> normalized/sanitized DrillSpec
  -> Draft -> Judge`，不把历史原题泄漏给 Draft。

## Rationale

这是让下一种前端成为薄 adapter 的最小拆分：当前基础设施和产品协议保持不变，且不引入
repository interface、DI framework 或尚未需要的服务层次。会话与 provenance 的唯一事实
来源仍是 ErrGrind 数据库。

## Consequences

- 未来 MCP 可以调用 application 操作，不需要 import CLI 或直接写 DB/LLM 编排；本决策
  不实现 MCP server 或 MCP transport。
- Provider/model 由 CLI 配置切换时，`AppState.application()` 为每次调用创建 façade，避免
  持有已被替换的 LLM client。
- OCR、record、config 与 status 仍主要是当前 CLI/bootstrap 职责；后续若有第二前端需要
  它们，再以同样方式单独抽取，不在此重构扩大范围。
