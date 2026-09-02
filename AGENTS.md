# ErrGrind 项目指令

## 产品目标与 MVP 边界

ErrGrind 不是 AI 错题本。它通过用户使用过程中的 Evidence 建立 Thinking Model，识别可能导致未来 Error 的 Pattern，并设计 Action 优化用户的思考过程，最终减少未来 Error。解释某道题的错误是发现 Pattern 的手段，不是最终目标。

当前 MVP 只针对数学题。Prompt、测试和设计讨论均以数学题为范围，除非用户明确扩大范围。

概念阶段的方向是：

```text
Error → Grill → Teach → Drill → Future Error
```

阶段方向固定，但 CLI 采用 slash command 加状态机，操作是非线性的。Grill 发现当前 Error 背后的 Pattern；Teach 针对 Pattern 帮助用户理解并纠正思考过程；Drill 综合已有 Error 的 Pattern，帮助建立新的思维模式。Drill 不是 Teach 的即时考试，未来真实学习中产生的新 Error 才是更重要的验证证据。

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

实现细节以源码为准，不在此处复制易漂移的命令、配置、数据库 SQL 或模型列表：

- CLI 入口、slash command、工作流与状态交互：`errgrind/cli/app.py`、`errgrind/cli/commands.py`、`errgrind/cli/state.py`、`errgrind/cli/ui.py`
- 数据模型、数据库 schema、迁移与 CRUD：`errgrind/models/`、`errgrind/db/schema.py`、`errgrind/db/ops.py`
- LLM provider、OCR 与 Prompt 加载：`errgrind/llm/`、`prompts/`
- 配置读写：`errgrind/config.py`
- 长期设计与决策索引：`design/README.md`
- 当前实现进度与验证记录：`todo.md`

## 测试

运行离线回归测试：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

测试不调用真实 API；修改 Prompt、provider、状态流转、会话恢复、数据库或 Drill 时，应运行相关测试，并在可能时运行完整测试集。测试命令和覆盖范围以当前 `tests/` 为准，不在本文件维护固定数量。
