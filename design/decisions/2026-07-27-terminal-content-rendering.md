# 终端内容渲染边界

## Context

ErrGrind 的题目、用户思路和 LLM 回复使用标准 Markdown + LaTeX。Rich 支持 Markdown，
但不支持 LaTeX；prompt_toolkit 也不能直接消费 Rich 的渲染对象。此前不同界面各自处理
文本，导致 Drill 题面可以降级部分公式，而 Grill、Teach、工作台和历史记录会显示原始
的 `$\tan 55^\circ$` 等源码。

## Decision

- 数据库、Prompt 和会话记录继续保存原始 Markdown + LaTeX，不写入终端专用字符。
- 所有动态内容必须在 `errgrind.cli.ui` 的统一渲染入口转换。
- Rich 输出使用 `render_terminal_markdown`；prompt_toolkit 输出使用
  `render_markdown_to_formatted_text`；列表摘要使用 `render_markdown_to_plain_text`。
- 终端不做二维公式排版，而是将常见 LaTeX 降级为可读的 Unicode/线性文本，例如
  `$\tan 55^\circ$` 显示为 `tan 55°`，分式显示为 `(分子) / (分母)`。
- Markdown 代码区保持原样，避免把示例代码中的 `$` 误判为公式边界。

## Rationale

标准内容格式可以继续供未来 Web、GUI 和 Mobile 的原生数学渲染器使用。终端转换只属于
当前 TUI 的展示适配，不污染核心数据，也让新增输出区域复用同一套行为和测试。

## Consequences

- 新增显示题目、用户内容或 LLM 内容的界面时，不能直接把原始文本交给 Rich Markdown
  或 prompt_toolkit。
- 终端数学转换以可读性为目标，不等同于完整 TeX 排版；未知命令会降级为可读命令名。
- 支持新的 LaTeX 写法时，应在统一转换器和回归测试中增加规则，而不是在调用点修补。
