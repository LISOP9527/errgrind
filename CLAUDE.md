# ErrGrind 设计原则

## 项目目标（Vision）

ErrGrind 不是一个 AI 错题本。

它的目标是：

> **通过 Error 建立用户的 Thinking Model（认知模型），并利用这个 Model 持续帮助用户优化，从而减少未来的 Error。**

项目关注的不是某一道题为什么做错，而是：

1. 用户有哪些稳定的思维模式（Pattern）？
2. 哪些 Pattern 会持续导致 Error？
3. 如何利用这些 Pattern 帮助用户减少未来的 Error？

因此，ErrGrind 的核心目标不是**解释错误（Explain Error）**，而是**减少未来错误（Reduce Future Error）**。

解释只是过程，不是目的。

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
| `/record` | 记录一个 error（只入库，状态 = `pending-grill`） |
| `/resume` | 列表 → 方向键+Enter 选详情 → 详情页内 grill/teach |
| `/drill` | 用最近 N 条 grilled error 作为上下文，出综合开放题 |
| `/status` | Panel 显示 pending-grill / pending-teach / done / 总计 数量 |
| `/config` | 方向键+Enter 编辑：`drill_context_n`、`grill_max_turns`、`provider` |
| `/model` | 同 provider 内切换 model（高频命令，独立） |
| `/help` | Panel 显示命令列表 |
| `/exit` | 退出 |

工作流（grill → teach → drill）通过 `/record` 录入后，在 `/resume` 详情页内以 `g`/`t` 键按需触发，drill 是独立的 `/drill` 命令。

### Error 状态机

```
pending-grill → pending-teach → done
```

- `pending-grill`：刚录入，尚未 grilling
- `pending-teach`：已完成 grilling，等待 teach
- `done`：grilling + teach 全部完成

re-grill 任何状态 = 继续已有对话 → 状态 `pending-teach` + 清空 teach/summary
re-teach 任何状态 = 覆盖旧 teach，fresh 上下文 → 状态 `done`
teach 在 `pending-grill`（无 grilling 对话）时禁止

### 中断处理

- grilling 中 Ctrl+C → 保存 partial 对话 + 状态回退 `pending-grill`，下次继续
- teach 中 Ctrl+C → 保存 partial teach 对话 + 状态不变，下次继续

---

## 技术栈
- Python 3.11+
- rich（CLI 界面）
- SQLite（内置 sqlite3，无 ORM）
- prompt_toolkit（多行输入、快捷键、列表选择）
- Gemini API + DeepSeek API（OpenAI 兼容接口）+ OpenCode Go（OpenAI 兼容接口）
- httpx（HTTP 请求，已通过 openai 依赖引入）

## 目录结构
```
errgrind/
├── errgrind/                  # 主包
│   ├── cli/
│   │   ├── app.py             # 入口 + 事件循环 + 启动流程
│   │   ├── commands.py        # slash command 注册与处理
│   │   ├── state.py           # AppState（current_error_id, accessed_error_ids）
│   │   └── ui.py              # I/O 工具（user_input / multiline_input / select_from_list）
│   ├── db/
│   │   ├── schema.py          # 建表 SQL（单表 error_records）
│   │   └── ops.py             # CRUD 操作
│   ├── llm/
│   │   ├── prompts.py         # PromptManager（从 prompts/ 加载文件）
│   │   ├── gemini.py          # Gemini 客户端（httpx）
│   │   └── client.py          # DeepSeek / OpenCode 客户端（openai 库）
│   ├── models/
│   │   └── types.py           # ErrorRecord 数据模型
│   ├── config.py              # ~/.config/errgrind/config.json 读写
│   ├── __main__.py            # python -m errgrind 支持
│   └── main.py                # 入口
├── data/                      # SQLite 数据库文件
├── prompts/                   # prompt 模板文件
│   ├── grilling.md            # Socratic 审讯系统 prompt（针对数学，[GRILLING_END] 结束标记）
│   ├── teach.md               # 讲解 + Q&A 系统 prompt（针对数学）
│   ├── drill.md               # 出综合题 prompt（开放题，{summary_list}）
│   └── judge.md               # LLM 判对错 prompt
├── pyproject.toml
└── CLAUDE.md
```

## 核心流程

### `/record`
1. 用户输入题目（多行）
2. 用户输入思路概述（多行，可空）
3. 用户输入参考答案及解析（多行，可空）
4. 入库，状态 = `pending-grill`

### `/resume`
1. 列出所有 error（编号 + 状态标签 + 题目摘要 + 时间）
2. 方向键 + Enter 选 error
3. 详情页：状态、时间、题目、思路概述、参考答案、grilling 摘要（若有）、partial 中断提示（若有）
4. 按 `g` 开始/继续 grilling，按 `t` 开始/继续 teach，按 `b` 返回列表
5. grilling/teach 完成后留在详情页（状态已更新）

### `/drill`
1. 取最近 N 条（`pending-teach` + `done`）的（题目 + grilling_summary）作为上下文
2. AI 生成综合开放题（JSON: `{question, reference_answer}`）
3. 用户输入答案 + 思路（多行）
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
- 需 GEMINI_API_KEY 或 DEEPSEEK_API_KEY 或 OPENCODE_API_KEY，首次启动有配置向导
- 启动后提示「可通过 /help 查看命令」

## 配置（`~/.config/errgrind/config.json`）
```json
{
  "provider": "gemini",
  "model": "gemini-3.5-flash",
  "api_key": "...",
  "drill_context_n": 10,
  "grill_max_turns": 30
}
```

`/config` 可编辑 `drill_context_n`、`grill_max_turns`、`provider`（改 provider 连带问 api_key + 选 model）。

## 首次设置（新环境）
```bash
git clone <repo> ~/errgrind
cd ~/errgrind
bash install.sh
errgrind
```

## 已知问题
- Gemini 免费模型 `gemini-3.5-flash` 经常返回 503（高负载），`gemini-3.1-flash-lite` 更稳定
- Gemini API 需要一个 dummy user message（`"开始吧"`）来满足 `contents` 非空要求
- Gemini 用 `system_instruction` 字段传系统 prompt，不能放在 `contents` 数组里（已在 `gemini.py` 处理）
- 多行输入用 prompt_toolkit：Enter 提交，Alt+Enter 换行
- 数据库文件自动创建在 `data/errgrind.db`
- grilling 第一次回复就含 `[GRILLING_END]` 是允许的（AI 判断题目简单）
- grilling/teach 中 Ctrl+C 会被捕获并保存 partial 进度

## 快速验证
```bash
errgrind
# 在 CLI 内测试：
/record        # 录入 error
/resume        # 列表 → 详情 → grill → teach
/status        # 查看计数
/drill         # 出综合题
/config        # 编辑配置
/help          # 命令列表
/exit          # 退出
```

---

# 开发规范
- user 非职业开发者，首次做项目；交互和代码注释应清晰易懂
- 输出中文交互（用户中文）
- API 调用必须 try-catch + 重试
- 经典功能（如输入输出）尽量调用已有库
- 所有 prompt 应放入 `prompts/` 目录，用户可直接审阅
- 尽量保证任何时候crtl+c都能不报错

