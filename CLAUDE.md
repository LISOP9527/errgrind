# ErrGrind 设计原则

## 设计记录约定

- 设计和决策统一记录在 `design/` 目录，并从 `design/README.md` 建索引，方便后续 review。
- 稳定的系统设计写入对应主题文件，例如架构、原则、数据流、UI 解耦等。
- 具体决策及其背景写入 `design/decisions/`，记录 Context、Decision、Rationale、Consequences。
- 后续 vibe coding 过程中，如果用户提出新的设计或决策，需要同步更新相关设计文件和索引。

## 项目目标（Vision）

ErrGrind 不是一个 AI 错题本。

它的目标是：

> 通过用户使用过程中的 Evidence 建立用户的 Thinking Model（认知模型），识别可能导致未来 Error 的 Pattern，并利用这个 Model 持续帮助用户优化，从而减少未来的 Error。

项目关注的不是某一道题为什么做错，而是：

1. 用户有哪些稳定的思维模式（Pattern）？
2. 哪些 Pattern 可能导致未来 Error？
3. 如何利用这些 Pattern 设计有效的 Action，帮助用户减少未来 Error？

因此，ErrGrind 的核心目标不是解释错误（Explain Error），而是优化用户的思考过程（Optimize Thinking Process），最终减少未来错误（Reduce Future Error）。

解释 Error 是发现 Pattern 的重要方式，但不是最终目的。

---

# mvp实现

## MVP 设计

MVP 采用固定工作流：

Error
→ Grill
→ Teach
→ Drill
→ Future Error

各阶段职责：

### Grill

分析当前 Error。

目标不是解释题目，而是尽可能发现背后的 Pattern。

### Teach

针对发现的 Pattern 进行解释和纠正。

Teach 的目标不是讲知识，而是帮助用户理解导致 Error 的真正原因。

### Drill

将用户 error 库作为上下文，生成综合练习题。

Drill 的目标是帮助用户建立新的思维模式，而不是立即验证 Teach 是否成功。

真正的验证，应更多来自未来真实学习过程中产生的新 Error。

---

### mvp目标

> 验证 Error Pattern 建模是否能够真正帮助用户减少未来的 Error。

---

## 命令式 CLI

新版本采用 slash command + 状态机的命令式 CLI（不再线性流程）。

### 命令集

| 命令 | 职责 |
|---|---|
| `/record` | 通过三步输入弹窗记录一个 error（只入库，状态 = `pending-grill`） |
| `/ocr [图片路径]` | OCR 识别数学错题图片，逐项校对后入库（状态 = `pending-grill`） |
| `/resume` | 双栏工作台浏览 error；方向键选择，Enter 默认处理，`g`/`t` 触发 grill/teach，`d` 删除 |
| `/drill` | 用最近 N 条 grilled error 作为上下文，出综合开放题 |
| `/status` | 全屏状态看板显示 pending-grill / pending-teach / done / 总计数量 |
| `/config` | 方向键+Enter 编辑：`drill_context_n`、`grill_max_turns`、`provider` |
| `/model` | 同 provider 内切换 model（高频命令，独立） |
| `/help` | 全屏命令指南 |
| `/exit` | 退出 |

工作流（grill → teach → drill）通过 `/record` 或 `/ocr` 录入后，在 `/resume` 双栏工作台内以 `g`/`t` 键按需触发，drill 是独立的 `/drill` 命令。`d` 会先请求确认，再永久删除当前 error。

### Error 状态机

```
pending-grill → pending-teach → done
```

- `pending-grill`：刚录入，尚未 grilling
- `pending-teach`：已完成 grilling，等待 teach
- `done`：grilling + teach 全部完成

完成后的 grill 为只读记录；再次按 `g` 只查看记录，不继续对话
partial grill（Ctrl+C、轮数上限或 API 错误）保持 `pending-grill`，再次按 `g` 继续
teach 使用同一段持久对话；Ctrl+C 保存退出并进入 `done`，之后按 `t` 可继续
teach 在 `pending-grill`（包括已有 partial grilling 对话）时禁止

### 中断处理

- grilling 中 Ctrl+C → 保存 partial 对话 + 状态回退 `pending-grill`，下次继续
- teach 中 Ctrl+C → 保存对话 + 状态 `done`，下次可继续

---

## 技术栈
- Python 3.11+
- rich（CLI 界面）
- SQLite（内置 sqlite3，无 ORM）
- prompt_toolkit（多行输入、快捷键、列表选择）
- Gemini API + DeepSeek API（OpenAI 兼容接口）+ OpenCode Go（OpenAI 兼容接口）
- OpenAI 官方 `openai-codex` SDK/app-server（ChatGPT 订阅登录）
- httpx（HTTP 请求，项目直接依赖）

## 目录结构
```
errgrind/
├── errgrind/                  # 主包
│   ├── cli/
│   │   ├── app.py             # 入口 + 事件循环 + 启动流程
│   │   ├── commands.py        # slash command 注册与处理
│   │   ├── state.py           # AppState（current_error_id, accessed_error_ids）
│   │   └── ui.py              # I/O 工具、全屏选择器与输入/确认弹窗
│   ├── db/
│   │   ├── schema.py          # 建表 SQL（单表 error_records）
│   │   └── ops.py             # CRUD 操作
│   ├── llm/
│   │   ├── prompts.py         # PromptManager（从 prompts/ 加载文件）
│   │   ├── ocr.py             # 图片校验与 OCR 输出契约
│   │   ├── gemini.py          # Gemini 客户端（httpx）
│   │   ├── codex.py           # Codex 官方 SDK/app-server 适配器（OAuth 由 SDK 管理）
│   │   └── client.py          # DeepSeek / OpenCode 客户端（openai 库）
│   ├── models/
│   │   └── types.py           # ErrorRecord 数据模型
│   ├── config.py              # ~/.config/errgrind/config.json 读写
│   ├── __main__.py            # python -m errgrind 支持
│   └── main.py                # 入口
├── data/                      # 旧版 SQLite 数据库位置，仅用于首次迁移
├── design/                    # 长期设计、原则与关键决策记录
├── prompts/                   # prompt 模板文件
│   ├── grilling.md            # Socratic 审讯系统 prompt（针对数学，[GRILLING_END] 结束标记）
│   ├── teach.md               # 讲解 + Q&A 系统 prompt（针对数学）
│   ├── drill.md               # 出综合题 prompt（开放题，{summary_list}）
│   ├── judge.md               # LLM 判对错 prompt
│   └── ocr.md                 # 数学图片三字段忠实转录 prompt
├── tests/                     # 离线自动化回归测试
├── install.sh                 # 本地安装脚本
├── pyproject.toml
├── todo.md                    # 当前实现进度与验证记录
└── CLAUDE.md
```

## 核心流程

### `/record`
1. 用户输入题目（多行）
2. 用户输入思路概述（多行，必填；确实没有思路时填写「没有思路」）
3. 用户输入参考答案及解析（多行，可空）
4. 入库，状态 = `pending-grill`

### `/ocr [图片路径]`
1. 校验本地图片（PNG、JPEG、WebP，最大 20 MB）
2. 当前 provider 识别题目、学生思路、参考答案三个字段
3. 用户逐项校对；题目与思路必填，参考答案可空
4. 仅保存确认后的文本，状态 = `pending-grill`；取消或失败不入库

### `/resume`
1. 打开全屏双栏工作台：左侧为 error 列表，右侧为当前 error 的摘要、思路与 grilling 摘要
2. 方向键浏览；Enter 对 `pending-grill` 默认开始 grill，对其他状态默认开始 teach
3. 按 `g` 开始/继续 partial grilling；grill 完成后按 `g` 只查看记录；按 `t` 开始/继续 teach，按 `d` 删除当前 error（需确认），按 `q`/Esc 返回
4. grilling/teach/删除完成后返回并刷新工作台

### `/drill`
1. 取最近 N 条（`pending-teach` + `done`）的（题目 + grilling_summary）作为上下文
2. AI 生成综合开放题（JSON 至少含 `question`、`reference_answer`，可附带 Pattern 元数据）
3. 在答题弹窗内输入答案 + 思路（多行）
4. LLM 判对错（JSON: `{is_correct, feedback}`）
5. 答对 → 显示 ✓，什么也不做
6. 答错 → 显示 ✗ + feedback + 新 error 入库（`pending-grill`）

## MVP 范围限定
- **MVP 只针对数学题**。所有 prompt、测试、设计都以数学题为目标。

## 数据库 Schema（MVP）
单表 `error_records`：
```sql
CREATE TABLE error_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'pending-grill',
    question TEXT NOT NULL,
    user_thoughts TEXT,
    reference_answer TEXT,
    grilling_conversation TEXT,   -- JSON
    grilling_summary TEXT,
    teach_conversation TEXT,      -- JSON
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

## Prompt 文件
- `ocr.md` — 数学错题图片转录 prompt（严格区分题目、学生思路、参考答案）
- `grilling.md` — Socratic 审讯系统 prompt（针对数学，结束标记 `[GRILLING_END]`）
  - 占位符：`{question}`, `{user_thoughts}`, `{reference_answer}`
- `teach.md` — 讲解 + Q&A 系统 prompt（针对数学）
  - 占位符：`{question}`, `{user_thoughts}`, `{reference_answer}`, `{grilling_history}`
- `drill.md` — 出综合题 prompt（开放题）
  - 占位符：`{summary_list}`（多个 error 的"题目 + 审讯摘要"拼接）
- `judge.md` — LLM 判对错 prompt
  - 占位符：`{question}`, `{reference_answer}`, `{user_response}`

## 运行命令
- `errgrind` — 启动交互式 CLI
- 首次启动通过配置向导选择 provider 和 model；Gemini / DeepSeek / OpenCode 填 API key，Codex 在浏览器或设备码登录，并保存配置到 `~/.config/errgrind/config.json`
- 启动后提示「可通过 /help 查看命令」

## 配置（`~/.config/errgrind/config.json`）
```json
{
  "provider": "gemini",
  "model": "gemini-3.6-flash",
  "api_key": "...",
  "drill_context_n": 10,
  "grill_max_turns": 30
}
```

`/config` 可编辑 `drill_context_n`、`grill_max_turns`、`provider`（普通 provider 会填写 API key，Codex 会进入登录流程，并统一选择 model）。

选择 Codex 时不填写 API key；ChatGPT OAuth、token 保存与刷新由 Codex app-server 管理，ErrGrind 不读取 `~/.codex/auth.json`。

## 首次设置（新环境）
```bash
git clone <repo> ~/errgrind
cd ~/errgrind
bash install.sh
errgrind
```

## 已知问题
- 2026-07-27 使用项目配置的免费 API 实测：`gemini-3.6-flash`、`gemini-3.5-flash-lite` 等模型均可用；当前默认推荐 `gemini-3.6-flash`
- Gemini API 需要一个 dummy user message（`"开始吧"`）来满足 `contents` 非空要求
- Gemini 用 `system_instruction` 字段传系统 prompt，不能放在 `contents` 数组里（已在 `gemini.py` 处理）
- Codex provider 依赖官方 `openai-codex` SDK 及其匹配的 CLI runtime；部分非主流平台可能没有可安装的 runtime wheel
- 多行输入用 prompt_toolkit：Enter 提交，Alt+Enter 换行
- 数据库文件自动创建在 `~/.local/share/errgrind/errgrind.db`（设置 `XDG_DATA_HOME` 时遵循该目录）；首次使用新路径时会复制旧的 `data/errgrind.db`，旧文件保留
- grilling 第一次回复就含 `[GRILLING_END]` 是允许的（AI 判断题目简单）
- grilling 中 Ctrl+C 会保存 partial 进度；teach 中 Ctrl+C 会保存退出，之后可继续进入

## 快速验证
```bash
errgrind
# 在 CLI 内测试：
/record        # 录入 error
/ocr <图片路径> # OCR 识别、校对并录入 error
/resume        # 双栏工作台 → grill / teach / 删除
/status        # 查看计数
/drill         # 出综合题
/config        # 编辑配置
/help          # 命令列表
/exit          # 退出
```

## 自动化回归测试

轻量离线测试用于防止 prompt 格式化、终端渲染、数据迁移、状态流转、会话生命周期、流式输出和 `/drill` 分支回归；不调用真实 API，也不评估产品是否真正减少未来 Error。后者以实际使用和长期观察为准。

```bash
.venv/bin/python -m unittest discover -s tests -v
```

当前 60 项测试覆盖：prompt 模板格式化、Markdown / LaTeX 终端渲染、Grill 结束标记、OpenAI 兼容接口与 Gemini SSE 流式输出、数据库路径迁移、Error 状态机、删除、`/record` 必填思路、OCR 图片校验与 provider 请求格式、OCR 校对/取消、Codex provider、Grill / Teach 会话恢复、严格 JSON schema，以及 `/drill` 答对/答错分支。

---

# 开发规范
- user 非职业开发者，首次做项目；交互和代码注释应清晰易懂
- 输出中文交互（用户中文）
- API 调用必须 try-catch + 重试
- 经典功能（如输入输出）尽量调用已有库
- 所有 prompt 应放入 `prompts/` 目录，用户可直接审阅
- 尽量保证任何时候crtl+c都能不报错
