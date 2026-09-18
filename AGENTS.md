# ErrGrind 项目指令

## 产品目标与 MVP 边界

ErrGrind 不是 AI 错题本。Error 提供关于思考过程的 Evidence；Grill 针对多个候选解释收集有区分度的新 Evidence，形成本次 Error 的 episode-level diagnosis；跨 Error 或后续行为的独立 Evidence 才能支持可证伪、可更新的长期 Pattern，并据此设计 Action，最终减少未来 Error。解释某道题的错误是诊断手段，不是最终目标。

原始 authentic Error 是本次诊断的 anchor。录题时的思路是对过去思考的 retrospective reconstruction，属于可能遗漏或偏差的 noisy Evidence；应区分过去的思路与用户在当前对话中的理解。Grill 诊断 Error-time failure mechanism，不追踪 Teach/Drill 等干预后的 post-interference causal chain；相关观察可以保存供审计，但不能据此作因果归因。Grill 的首要目标是 diagnosis 而非 Teach，不要求完全没有 incidental learning effect。一次 episode diagnosis 不能升级为 confirmed、verified 或 mastered Pattern；长期 Pattern 仍需未来真实且独立的 Evidence。

当前 MVP 只针对数学题。Prompt、测试和设计讨论均以数学题为范围，除非用户明确扩大范围。

概念阶段的方向是：

```text
Error → Grill → Teach → Drill → Future Error
```

阶段方向固定，但 CLI 采用 slash command 加状态机，操作是非线性的。Grill 区分当前 Error 的候选成因并形成 episode-level diagnosis；Teach 针对当前总结出的机制帮助用户理解并纠正思考过程；Drill 综合已有 Error 的机制假设，帮助建立新的思维模式。这个 Error → Grill → Teach → Drill → Future Error 是产品工作流，不是 Evidence 形成长期 Pattern 的认识论结构。一次 Grill 结果不是稳定 Pattern；长期 Pattern 需要跨 Error 或后续行为的独立 Evidence。Drill 不是 Teach 的即时考试，未来真实学习中产生的新 Error 才是更重要的验证证据。

## Error 状态机与持久化不变量

状态转换为：

```text
pending-grill → pending-teach → done
```

- 新录入的 Error 为 `pending-grill`。
- Grill 完成后为 `pending-teach`；完成后的 Grill 记录只读。
- 首次进入 Teach 并通过 Ctrl+C 保存退出后为 `done`；`done` 不表示 Teach 永久关闭，之后仍可继续对话。
- Grill 中的 Ctrl+C、轮数上限或 API 错误必须保存已有对话并保持 `pending-grill`，下次可以继续。
- Teach 中的 Ctrl+C 必须保存对话并进入 `done`，之后可以继续 Teach。
- `pending-grill`（包括存在 partial Grill 对话时）禁止 Teach。

Drill 遵循 `DrillSpec → Draft → Judge` 流程。每次完成判分都写入 `drill_attempts`；答错时根据结果派生一个新的 `pending-grill` Error，答对也必须保留判分记录。

## Core/Application 架构原则

业务工作流的依赖方向固定为：

```text
CLI / TUI 或未来其他 frontend
                 ↓
       errgrind.application
                 ↓
        DB / LLM / Prompt / Models
```

- `errgrind.application` 是 Grill、Teach 和 Drill 的统一应用边界，负责 Prompt 组装、LLM 编排、输出契约校验、状态转换、会话持久化，以及 Drill provenance、lineage 和原子记录。这些规则不得在 frontend 中重新组装或复制。
- frontend 只负责收集用户输入、渲染结构化结果、popup/确认、按键和进度展示。它可以调用 application 操作，但不得通过 `db.update_*() → llm.*() → db.update_*()` 自行实现业务流程。
- Application/Core 不得依赖 `rich`、`prompt_toolkit`、`errgrind.cli`、CLI state、popup、console rendering 或 key binding。跨边界返回 dataclass、enum、typed object 或简单 domain model，并用可区分的业务异常表达失败。
- SQLite 中的 ErrGrind 会话和业务记录是工作流的事实来源。Application 必须在可失败的模型调用前保存已接收的 bootstrap/用户输入，并统一维护上述状态与持久化不变量。UI 不得把直接 CRUD 当作完成 workflow 的业务 API。
- 为流式显示或交互进度提供的可选 callback，只能传递 token、生命周期或阶段事件；Application 不得接收或生成终端渲染对象。
- Application 的所有公开返回值（包括 Teach）与用户可见异常都必须隐藏 Grill 的内部诊断账本、variant 答案和预测；模型原始结构化输出只能用于内部校验和 repair。
- 优先保持当前实现所需的最小解耦。不为架构形式引入尚无现实消费者的 repository interface、DI framework 或复杂 class hierarchy。
- 未来 MCP、GUI 或 Mobile 都应作为 `ErrGrindApplication` 的薄 adapter，不得 import CLI、模拟终端交互或复制 workflow。本原则不表示现在需要实现 MCP SDK、MCP server 或 MCP-specific contract。
- 修改 Grill、Teach、Drill、会话恢复、状态转换或 Drill 时，优先直接测试 application boundary，同时保留必要的 CLI integration tests，确认 adapter 仍正确处理输入、渲染与中断。

## 复用、搜索与技术决策原则

ErrGrind 的独特价值在 Error / Evidence / diagnosis / intervention 的产品与认识论结构，不在重复实现通用基础设施。

**先判断目标，再判断实现路径。** 对任何非平凡方案，不要把用户提出的实现路径直接当成已选定方案。先还原真正目标与约束，再判断当前路径是否合理；主动检查项目已有能力、成熟实现/组件、标准方案、更简单的替代路径，以及可能被忽略的高杠杆决策。如果当前方案会造成明显不必要的复杂度、重复劳动或长期成本，应在实施前指出并选择更好的路径，而不是机械执行。不要假设用户熟悉工程生态；用户没有提出某个方案，不能视为该方案不存在。

完成这一步后，再判断问题属于哪一类：

- **产品 / domain semantics**：例如 Error、Grill、Teach、Drill、Evidence 边界与用户心智模型。这些必须由 ErrGrind 自己定义，不能因为某个框架已有 thread、memory、agent、course 等概念就迁就它。
- **commodity engineering**：例如 chat composer、附件、滚动、Markdown/LaTeX、安全上传、streaming、auth、缓存、retrieval、agent runtime 等。实现非平凡版本前，先检查项目已有能力、标准库/协议和成熟开源实现，再比较 reuse / adapt / fork / build。
- **research uncertainty**：机制是否有效、某种 Evidence 是否有诊断价值、Policy 是否改善学习等。先做可证伪的小实验和真实使用，不用工程复杂度替代证据。

遵守以下规则：

- **Search before build.** 对新的非平凡基础设施或 UI primitive，默认先做一次有针对性的生态检查。若仍选择自建，应能说明现成方案为何不适配、集成成本为何更高或会破坏产品边界。
- **Reuse implementations aggressively; import ontologies conservatively.** 可以大胆复用成熟实现，但框架必须适配 ErrGrind，而不是让 ErrGrind 的 domain model 适配框架。完整产品型框架尤其要警惕其信息架构和对象模型反向绑架产品。
- **比较总成本，不比较“有没有轮子”。** 引入第三方方案时同时计算迁移、构建链、运行依赖、安全面、测试、升级和退出成本；已有实现接近完成时，不因发现新框架就自动重写。
- **把模型调用当作昂贵且有损的 I/O。** 已有结构化状态应尽量结构化传递；避免 `structured state → prose summary → another LLM re-derivation` 这类无必要 round-trip。新增模型调用前先问：是否已有数据可直接复用，是否会丢 provenance / uncertainty，是否只是用 token 替代普通程序逻辑。
- **保留 provenance 和事实来源。** 用户行为、模型解释、派生摘要、附件、OCR/vision 结果和系统推断必须保持来源可区分；任何 transformation 都不能把模型生成内容伪装成用户 Evidence。
- **重复 workaround 是缺失 contract 的信号。** 如果多个 frontend 需要同一逻辑，或 adapter 为同一个 Core 限制反复打补丁，应优先补 Application/domain contract，而不是继续堆 Web/CLI 特例。
- **真实数据优先于漂亮 fixture。** 新 UI、恢复路径、长文本、数学渲染和状态流转至少用一个真实或足够脏/长的代表案例验收；短 fake fixture 通过不等于产品可用。
- **Implementation surface 不等于 product surface。** CLI command、数据库表、状态枚举、API endpoint、内部 pipeline stage 都只是实现能力或 contract，不能默认一一映射成页面、导航、tab 或用户心智模型。新 frontend 应先从用户对象、任务与动作设计信息架构，再映射到底层能力。
- **Measure before optimize.** 对 token、latency、context growth、provider 成本和性能的优化，优先基于 usage telemetry、真实 trace 和代表性工作流；不要仅凭直觉增加 cache、summary、压缩层或额外模型调用。
- **按可逆性分配设计成本。** CSS/文案等低成本决策可以快速试；schema、ontology、framework/runtime、Evidence contract 等高切换成本决策应先搜索、做小实验并记录依据。不要为了“未来可能需要”提前冻结复杂抽象。

## 设计记录

- 设计和决策统一记录在 `design/`，并从 `design/README.md` 建索引，便于 review。
- 稳定设计写入对应主题文件；具体决策写入 `design/decisions/`，包含 Context、Decision、Rationale、Consequences。
- 用户提出新的设计或决策时，同步更新相关设计文件和 `design/README.md` 索引。
- 不要把会持续变化的完整实现快照复制到本文件；需要了解当前实现时阅读权威源码和文档。

## 开发规范

- 面向非职业开发者，不假设用户熟悉 Python 项目结构；面向用户的交互和错误信息使用清晰易懂的中文，代码注释应清楚说明必要的原因与约束。
- 所有用户可审阅的 Prompt 放在 `prompts/` 目录。
- API 调用使用 Python `try/except` 处理异常；仅对可重试的瞬态错误进行有限重试。流式响应已经产生输出后，不得自动重放整个请求。
- 在交互边界妥善捕获 `KeyboardInterrupt`（Ctrl+C）和 `EOFError`，并遵守上面的 Grill/Teach 对话与状态持久化语义。
- 输入输出等基础功能优先使用项目已有依赖，避免无必要地重复实现。

## 权威来源导航

发生冲突时按以下层级判断：当前源码是“现在实际如何运行”的实现事实；当前主题设计与最新适用 ADR 是“应该如何运行”的设计权威；被后续决策 supersede 的旧 ADR、历史报告和旧实现说明只提供历史背景。新决策替代旧契约时，应更新相关主题文档或显式标明 supersession，避免让 agent 在互相冲突的文档中自行猜测。

实现细节以源码为准，不在此处复制易漂移的命令、配置、数据库 SQL 或模型列表：

- Grill、Teach、Drill 工作流、状态转换与结构化结果：`errgrind/application/`
- CLI 入口、slash command、终端交互、渲染与 application adapter：`errgrind/cli/app.py`、`errgrind/cli/commands.py`、`errgrind/cli/state.py`、`errgrind/cli/ui.py`
- 数据模型、数据库 schema、迁移与 CRUD：`errgrind/models/`、`errgrind/db/schema.py`、`errgrind/db/ops.py`
- LLM provider、OCR 与 Prompt 加载：`errgrind/llm/`、`prompts/`
- 配置读写：`errgrind/config.py`
- 长期设计与决策索引：`design/README.md`
- 当前实现进度与验证记录：`todo.md`

当前 Grill 的 structured episode diagnosis 事实来源是
`errgrind/application/grill_diagnosis.py` 和 `error_records.grilling_diagnostic_state`。
它只属于单次 Error：不要在这里或普通 `/drill` 中推导长期 Pattern、跨 Error 合并或
Bayesian confidence。`grilling_summary` 继续作为 Teach/Drill 的人类可读兼容输出；
variant Probe 只进入 Grill diagnosis，不进入 `drill_attempts`。

## 测试

运行离线回归测试：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

测试不调用真实 API；修改 Prompt、provider、状态流转、会话恢复、数据库或 Drill 时，应运行相关测试，并在可能时运行完整测试集。测试命令和覆盖范围以当前 `tests/` 为准，不在本文件维护固定数量。
