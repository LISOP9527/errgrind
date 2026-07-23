from prompt_toolkit import Application, PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import FormattedTextControl, HSplit, VSplit, Layout, Window
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, Box, TextArea
from rich.console import Console
from rich.markdown import Markdown

console = Console()

_kb = KeyBindings()


@_kb.add("enter")
def _accept(event):
    event.current_buffer.validate_and_handle()


@_kb.add("escape", "enter")
def _newline(event):
    event.current_buffer.insert_text("\n")


_psession = PromptSession(multiline=True, key_bindings=_kb, history=InMemoryHistory())
_sline = PromptSession(history=InMemoryHistory())

_hint_shown = False
_completer = None


class SlashCompleter(Completer):
    def __init__(self, commands):
        self.commands = commands

    def get_completions(self, document, complete_event):
        text = document.text
        if not text.startswith("/"):
            return
        partial = text[1:].split()[0] if len(text) > 1 else ""
        for name, desc in self.commands.items():
            if name.startswith(partial):
                yield Completion(
                    f"/{name}",
                    start_position=-len(text),
                    display=f"/{name}",
                    display_meta=desc,
                )


def init_completer(commands: dict):
    global _completer
    _completer = SlashCompleter(commands)
    return _completer


def render_status_badge(status: str) -> tuple[str, str]:
    """返回 (style_class, text) 格式的 Badge"""
    if status == "pending-grill":
        return ("class:badge-grill", "[ ⏳ 待审讯 ]")
    elif status == "pending-teach":
        return ("class:badge-teach", "[ 📖 待讲解 ]")
    elif status == "done":
        return ("class:badge-done", "[ ✅ 已完成 ]")
    return ("class:dim", f"[{status}]")


def multiline_input(prompt: str = "") -> str:
    global _hint_shown
    if not _hint_shown:
        console.print(" [dim]💡 提示：Alt+Enter 换行，Enter 提交[/dim]")
        _hint_shown = True
    console.print(prompt)
    return _psession.prompt().strip()


def prompt_line(text: str = "") -> str:
    prompt = HTML("<ansicyan><b>errgrind</b></ansicyan> <ansibrightblack>❯</ansibrightblack> ")
    if _completer is not None:
        return _sline.prompt(prompt, completer=_completer, complete_while_typing=True)
    return _sline.prompt(prompt)


def sysmsg(text: str):
    console.print(f"  [cyan]✦[/cyan] [dim]{text}[/dim]")


def errmsg(text: str):
    console.print(f"  [bold red]✖ Error:[/bold red] {text}")


def successmsg(text: str):
    console.print(f"  [bold green]✔[/bold green] {text}")


GLOBAL_STYLE = Style([
    ("frame.border", "fg:ansicyan"),
    ("title", "fg:ansibrightcyan bold"),
    ("subtitle", "fg:ansiyellow bold"),
    ("selected", "fg:ansiblack bg:ansibrightcyan bold"),
    ("unselected", ""),
    ("footer", "fg:ansibrightblack"),
    ("badge-grill", "fg:ansiyellow bold"),
    ("badge-teach", "fg:ansicyan bold"),
    ("badge-done", "fg:ansigreen bold"),
    ("dim", "fg:ansibrightblack"),
    ("hint", "fg:ansiyellow"),
    ("nextstep", "fg:ansibrightcyan italic"),
    ("user-input", "fg:ansicyan"),
    ("label", "fg:ansibrightmagenta bold"),
    ("bar-fill", "fg:ansibrightcyan"),
    ("bar-empty", "fg:ansibrightblack"),
])


def select_from_list(items, render_fn, title="", footer="[↑↓] 选择   [Enter] 确认   [q/Esc] 返回"):
    if not items:
        return None

    state = {"current": 0}

    def get_text():
        lines = []
        if title:
            lines.append(("class:title", f"✦ {title}\n\n"))
        for i, item in enumerate(items):
            if i == state["current"]:
                lines.append(("class:selected", f" ▶ {render_fn(item)} \n"))
            else:
                lines.append(("class:unselected", f"   {render_fn(item)}\n"))
        lines.append(("", "\n"))
        lines.append(("class:footer", footer))
        return lines

    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        if state["current"] > 0:
            state["current"] -= 1

    @kb.add("down")
    def _(event):
        if state["current"] < len(items) - 1:
            state["current"] += 1

    @kb.add("enter")
    def _(event):
        event.app.exit(result=state["current"])

    @kb.add("q")
    def _(event):
        event.app.exit(result=None)

    @kb.add("escape")
    def _(event):
        event.app.exit(result=None)

    frame = Frame(Window(content=FormattedTextControl(get_text), wrap_lines=True), title=title or "选择对话框")
    container = Box(frame, padding=1)

    app = Application(
        layout=Layout(container),
        key_bindings=kb,
        style=GLOBAL_STYLE,
        full_screen=True,
        mouse_support=False,
    )
    return app.run()


def select_error_split_view(errors):
    """
    Master-Detail 双栏 (Split View) 交互界面
    包含边框卡片包覆
    """
    if not errors:
        return None, None

    state = {"current": 0}

    def get_left_text():
        lines = []
        lines.append(("class:title", " 错题清单 \n"))
        lines.append(("class:dim", " ──────────────────────\n"))
        for i, e in enumerate(errors):
            badge_cls, badge_str = render_status_badge(e.status)
            q_snippet = e.question[:15].replace("\n", " ")
            if len(e.question) > 15:
                q_snippet += ".."

            if i == state["current"]:
                lines.append(("class:selected", f"▶ #{i+1:<2} "))
                lines.append((badge_cls + " class:selected", f"{badge_str} "))
                lines.append(("class:selected", f"{q_snippet}\n"))
            else:
                lines.append(("", f"  #{i+1:<2} "))
                lines.append((badge_cls, f"{badge_str} "))
                lines.append(("", f"{q_snippet}\n"))

        lines.append(("", "\n"))
        lines.append(("class:footer", " [↑/↓] 浏览列表"))
        return lines

    def get_right_text():
        if state["current"] >= len(errors):
            return [("", "无数据")]

        e = errors[state["current"]]
        badge_cls, badge_str = render_status_badge(e.status)

        lines = []
        lines.append(("class:title", f"Error #{state['current'] + 1}  "))
        lines.append((badge_cls, f"{badge_str}\n"))
        lines.append(("class:dim", f"创建时间: {e.created_at.strftime('%Y-%m-%d %H:%M')}\n"))
        lines.append(("class:dim", "─────────────────────────────────────────────────────\n\n"))

        # 题目
        lines.append(("class:subtitle", "📌 题目:\n"))
        q_lines = e.question.split("\n")
        display_q = "\n".join(q_lines[:6])
        if len(q_lines) > 6:
            display_q += "\n..."
        lines.append(("", f"{display_q}\n\n"))

        # 思路
        if e.user_thoughts:
            lines.append(("class:user-input", "💭 你的思路:\n"))
            t_lines = e.user_thoughts.split("\n")
            display_t = "\n".join(t_lines[:4])
            if len(t_lines) > 4:
                display_t += "\n..."
            lines.append(("", f"{display_t}\n\n"))

        # Grilling 摘要
        if e.grilling_summary:
            lines.append(("class:label", "💡 Grilling 审讯摘要:\n"))
            s_lines = e.grilling_summary.split("\n")
            display_s = "\n".join(s_lines[:5])
            if len(s_lines) > 5:
                display_s += "\n..."
            lines.append(("", f"{display_s}\n\n"))

        # 下一步指引
        if e.status == "pending-grill":
            lines.append(("class:nextstep", "⚡ 下一步建议: 按 [g] 或 [Enter] 开始思维审讯 (Grill)\n"))
        elif e.status == "pending-teach":
            lines.append(("class:nextstep", "⚡ 下一步建议: 按 [t] 或 [Enter] 开始针对性讲解 (Teach)\n"))
        elif e.status == "done":
            lines.append(("class:nextstep", "✓ 该错题已研讨完成！也可按 [g] / [t] 重新复习\n"))

        return lines

    def get_footer_text():
        return [
            ("class:footer", "  [Enter] 默认处理   [g] 审讯   [t] 讲解   [d] 删除   [q/Esc] 返回")
        ]

    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        if state["current"] > 0:
            state["current"] -= 1

    @kb.add("down")
    def _(event):
        if state["current"] < len(errors) - 1:
            state["current"] += 1

    @kb.add("enter")
    def _(event):
        e = errors[state["current"]]
        act = "g" if e.status == "pending-grill" else "t"
        event.app.exit(result=(state["current"], act))

    @kb.add("g")
    def _(event):
        event.app.exit(result=(state["current"], "g"))

    @kb.add("t")
    def _(event):
        event.app.exit(result=(state["current"], "t"))

    @kb.add("d")
    def _(event):
        event.app.exit(result=(state["current"], "d"))

    @kb.add("q")
    def _(event):
        event.app.exit(result=(None, None))

    @kb.add("escape")
    def _(event):
        event.app.exit(result=(None, None))

    left_win = Window(content=FormattedTextControl(get_left_text), width=42, wrap_lines=True)
    sep_win = Window(content=FormattedTextControl([("class:dim", "│\n" * 40)]), width=1)
    right_win = Window(content=FormattedTextControl(get_right_text), wrap_lines=True)
    footer_win = Window(content=FormattedTextControl(get_footer_text), height=1)

    split_layout = HSplit([
        VSplit([left_win, sep_win, right_win]),
        footer_win,
    ])

    frame = Frame(split_layout, title="ErrGrind - 错题工作台")
    container = Box(frame, padding=1)

    app = Application(
        layout=Layout(container),
        key_bindings=kb,
        style=GLOBAL_STYLE,
        full_screen=True,
        mouse_support=False,
    )
    return app.run()


def popup_input(title: str, prompt_text: str, multiline: bool = True) -> str | None:
    text_area = TextArea(multiline=multiline, wrap_lines=True)
    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _(event):
        text_area.buffer.insert_text("\n")

    @kb.add("enter")
    def _(event):
        event.app.exit(result=text_area.text)

    @kb.add("escape")
    def _(event):
        event.app.exit(result=None)

    footer_hint = "Alt+Enter 换行  │  Enter 提交  │  Esc 取消" if multiline else "Enter 提交  │  Esc 取消"

    layout = HSplit([
        Window(height=3, content=FormattedTextControl([
            ("class:title", f"✦ {title}\n\n"),
            ("", f"{prompt_text}"),
        ])),
        Window(height=1, content=FormattedTextControl([("class:dim", "─" * 60)])),
        Window(text_area.control),
        Window(height=1, content=FormattedTextControl([("class:footer", f"{footer_hint}")]), align="center"),
    ])

    frame = Frame(layout, title=title)
    container = Box(frame, padding=1)

    app = Application(layout=Layout(container), key_bindings=kb, style=GLOBAL_STYLE, full_screen=True)
    return app.run()


def popup_content(body, title="", footer=""):
    lines = []
    if title:
        lines.append(("class:title", f"✦ {title}\n\n"))
    if isinstance(body, list):
        lines.extend(body)
    elif isinstance(body, str):
        lines.append(("", body))
    lines.append(("", "\n"))

    footer_text = footer or "按 Enter / Esc / q 关闭"
    layout = HSplit([
        Window(content=FormattedTextControl(lines), wrap_lines=True),
        Window(height=1, content=FormattedTextControl([("class:footer", footer_text)])),
    ])

    kb = KeyBindings()

    @kb.add("enter")
    def _(event):
        event.app.exit()

    @kb.add("escape")
    def _(event):
        event.app.exit()

    @kb.add("q")
    def _(event):
        event.app.exit()

    frame = Frame(layout, title=title or "提示")
    container = Box(frame, padding=1)

    app = Application(
        layout=Layout(container),
        key_bindings=kb,
        style=GLOBAL_STYLE,
        full_screen=True,
    )
    app.run()


def popup_drill_answer(question_text):
    text_area = TextArea(multiline=True, wrap_lines=True)
    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _(event):
        text_area.buffer.insert_text("\n")

    @kb.add("enter")
    def _(event):
        event.app.exit(result=text_area.text)

    @kb.add("escape")
    def _(event):
        event.app.exit(result=None)

    layout = HSplit([
        Window(content=FormattedTextControl([
            ("class:title", "🎯 综合演练 - 答题\n\n"),
            ("class:subtitle", f"{question_text}\n\n"),
            ("class:user-input", "输入你的答案与解题思路（Alt+Enter 换行，Enter 提交）：\n"),
        ]), height=9, wrap_lines=True),
        Window(height=1, content=FormattedTextControl([("class:dim", "─" * 60)])),
        Window(text_area.control),
        Window(height=1, content=FormattedTextControl([("class:footer", "Alt+Enter 换行  │  Enter 提交  │  Esc 取消")])),
    ])

    frame = Frame(layout, title="Drill 答题面板")
    container = Box(frame, padding=1)

    app = Application(
        layout=Layout(container),
        key_bindings=kb,
        style=GLOBAL_STYLE,
        full_screen=True,
    )
    return app.run()


def popup_confirm(body):
    lines = []
    if isinstance(body, list):
        lines.extend(body)
    elif isinstance(body, str):
        lines.append(("class:subtitle", f"⚠️ {body}"))
    lines.append(("", "\n\n"))

    layout = HSplit([
        Window(content=FormattedTextControl(lines), wrap_lines=True),
        Window(height=1, content=FormattedTextControl([("class:footer", "  [y] 确认    [n/Esc] 取消")])),
    ])

    kb = KeyBindings()

    @kb.add("y")
    def _(event):
        event.app.exit(result=True)

    @kb.add("n")
    def _(event):
        event.app.exit(result=False)

    @kb.add("enter")
    def _(event):
        event.app.exit(result=False)

    @kb.add("escape")
    def _(event):
        event.app.exit(result=False)

    frame = Frame(layout, title="确认操作")
    container = Box(frame, padding=1)

    app = Application(
        layout=Layout(container),
        key_bindings=kb,
        style=GLOBAL_STYLE,
        full_screen=True,
    )
    return app.run()
