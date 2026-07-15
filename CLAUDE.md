# errgrind — 错题训练器

## 项目概述
CLI 刷题软件。核心闭环：用户提交错题 → AI grilling 审讯 → 错误入库 → AI 出同类题（略难）→ 重做。

## 技术栈
- Python 3.11+
- rich（CLI 界面）
- SQLite（内置 sqlite3，无 ORM）
- prompt_toolkit（多行输入、快捷键）
- Gemini API（主） + DeepSeek API（备，OpenAI 兼容接口）

## 目录结构
```
errgrind/
├── errgrind/           # 主包
│   ├── cli/app.py      # rich 交互层（整个 CLI 流程）
│   ├── db/
│   │   ├── schema.py   # 建表 SQL（4 张表）
│   │   └── ops.py      # CRUD 操作
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── prompts.py  # PromptManager（从 prompts/ 加载文件）
│   │   ├── gemini.py   # Gemini 客户端（httpx）
│   │   └── client.py   # DeepSeek 客户端（openai 库）
│   ├── models/
│   │   └── types.py    # 数据模型 + ErrorCategory 枚举
│   ├── config.py       # ~/.config/errgrind/config.json 读写
│   └── main.py         # 入口
├── data/               # SQLite 数据库文件
├── prompts/            # prompt 模板文件
│   ├── grilling.md     # 审讯系统 prompt
│   ├── drill.md        # 出题 prompt
│   ├── goal-exam.md    # 考试提分模式审讯重点
│   └── goal-deep.md    # 理解性学习模式审讯重点
├── pyproject.toml
└── CLAUDE.md
```

## 核心流程（MVP）
1. 用户启动 → 配置（首次） → 选模型 → 设定 goal + goal type（考试提分/理解性学习）
2. 用户提交错题（题目 + 错误答案 + 正确答案可选）
3. Socratic grilling — 每次一问，深挖思维路径，直到用户无法提供更多信息或 AI 判断可总结
4. 基于审讯对话，AI 讲解原题正确解法
5. 错误总结入库（6 类错误类型 + 审讯对话全文 + 压缩摘要）
6. AI 出同类题（JSON：question + 4 options + correct_answer + explanation），难度略高于原题
7. 用户作答 → 答对→结束 / 答错→回到步骤 3

## 数据库 Schema（MVP）
- `sessions` — 会话记录（goal, created_at）
- `questions` — 题目库（含用户提交和 AI 生成，source 区分）
- `attempts` — 作答记录（is_correct, linked to question + session）
- `error_records` — 错误总结（grilling 对话全文、压缩摘要、错误类型）

## Prompt 文件（全在 prompts/ 下，用户可直接审阅）
- `grilling.md` — Socratic 审讯系统 prompt（含规则 + {goal_instructions} 占位符）
- `goal-exam.md` — 考试提分模式：追问决策过程、考场技巧、常见陷阱
- `goal-deep.md` — 理解性学习模式：追问原理、因果关系、延伸问题
- `drill.md` — 出题 prompt：基于错误记录生成 JSON 格式同类题

## 开发规范
- user 不是开发者，若有问题直接提出
- 不加注释，除非逻辑必须解释
- 输出中文交互（用户中文）
- prompt 调优占开发 50% 时间
- API 调用必须 try-catch + 重试
- MVP 不做间隔重复，不做 OCR，不做题库导入
- 先跑通最小闭环，再优化

## 运行命令
- `errgrind` — 启动一次练习会话
- 需 GEMINI_API_KEY 或 DEEPSEEK_API_KEY，首次启动有配置向导

## 首次设置（新环境）
```bash
git clone <repo> ~/errgrind
cd ~/errgrind
python3 -m venv .venv
.venv/bin/pip install -e .
ln -s ~/errgrind/.venv/bin/errgrind ~/.local/bin/errgrind  # 或加入 PATH
errgrind  # 首次启动会自动进入配置向导
```

## 已知问题
- Gemini 免费模型 `gemini-3.5-flash` 经常返回 503（高负载），`gemini-3.1-flash-lite` 更稳定
- Gemini API 需要一个 dummy user message（`"开始吧"`）来满足 `contents` 非空要求
- Gemini 用 `system_instruction` 字段传系统 prompt，不能放在 `contents` 数组里（已在 `gemini.py` 处理）
- 多行输入用 prompt_toolkit：Enter 提交，Alt+Enter 换行
- 数据库文件自动创建在 `data/errgrind.db`

## Prompt 模板变量
| 文件 | 变量 | 来源 |
|------|------|------|
| `grilling.md` | `{goal_instructions}` | `prompts/goal-exam.md` 或 `goal-deep.md` |
| `grilling.md` | `{question}`, `{user_answer}`, `{correct_answer}`, `{user_goal}`, `{goal_type}` | 用户输入 + session 参数 |
| `drill.md` | `{user_goal}`, `{compressed_summary}`, `{original_question}`, `{user_answer}`, `{error_type}` | error_record + session |

## 快速验证
```bash
errgrind  # 跑一次完整流程
```

## TODO（下次开发）
- [ ] 讲解后加追问环节：当前流程是审讯→讲解→出题，用户听完讲解可能有疑问。应该在 `_explain_original()` 之后加一轮问答，用户问清楚了再进入 drill
- [ ] prompt 敏感性：Gemini 对语气词非常敏感，"严厉"二字就能让它从 Socratic 变成责骂。调 prompt 时注意渐变调试，不要一次性加过多修饰词

## 关键文件关系
- `cli/app.py` → `_run_grilling()` 调用 `prompts.load("grilling.md")` + `prompts.load("goal-exam.md"|"goal-deep.md")` 拼接系统 prompt
- `cli/app.py` → `_drill_question()` 调用 `prompts.load("drill.md")`
- `llm/gemini.py` → `GeminiClient.chat()` 从 messages 中提取 system role 到 `system_instruction` 字段
- `llm/client.py` → `LLMClient.chat()` 直接传 messages 给 openai 库
- `llm/prompts.py` → `PromptManager.load()` 读取 `prompts/` 目录下文件
