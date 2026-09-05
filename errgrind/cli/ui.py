from io import StringIO
import re
from prompt_toolkit import Application, PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML, ANSI, to_formatted_text
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Dimension, FormattedTextControl, HSplit, VSplit, Layout, Window
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, Box, TextArea
from rich.console import Console
from rich.markdown import Markdown

console = Console()


_LATEX_COMMANDS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
    "epsilon": "ε", "varepsilon": "ε", "theta": "θ", "lambda": "λ",
    "mu": "μ", "pi": "π", "rho": "ρ", "sigma": "σ", "phi": "φ",
    "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ",
    "Lambda": "Λ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Omega": "Ω",
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠",
    "neq": "≠", "approx": "≈", "equiv": "≡", "in": "∈", "notin": "∉",
    "subset": "⊂", "subseteq": "⊆", "supset": "⊃", "supseteq": "⊇",
    "cup": "∪", "cap": "∩", "to": "→", "rightarrow": "→",
    "leftarrow": "←", "Rightarrow": "⇒", "Leftarrow": "⇐",
    "leftrightarrow": "↔", "infty": "∞", "sum": "Σ", "prod": "Π",
    "int": "∫", "partial": "∂", "nabla": "∇", "forall": "∀",
    "exists": "∃", "angle": "∠", "perp": "⊥", "parallel": "∥",
    "degree": "°", "circ": "°", "sin": "sin", "cos": "cos",
    "tan": "tan", "cot": "cot", "sec": "sec", "csc": "csc",
    "log": "log", "ln": "ln", "exp": "exp", "lim": "lim",
    "max": "max", "min": "min", "left": "", "right": "",
    "quad": " ", "qquad": "  ",
}

_SUPERSCRIPTS = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
_SUBSCRIPTS = str.maketrans("0123456789+-=()aeioxhklmnpst", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑᵢₒₓₕₖₗₘₙₚₛₜ")
_MARKDOWN_CODE = re.compile(r"(```.*?```|~~~.*?~~~|`[^`\n]*`)", re.DOTALL)


def _format_math_for_terminal(text: str) -> str:
    """将 Markdown 中的 LaTeX 公式降级为终端可读文本。"""

    def group_end(value: str, start: int) -> int | None:
        if start >= len(value) or value[start] != "{":
            return None
        depth = 0
        for index in range(start, len(value)):
            if value[index] == "{":
                depth += 1
            elif value[index] == "}":
                depth -= 1
                if depth == 0:
                    return index
        return None

    def replace_group_command(value: str, command: str, replacement) -> str:
        marker = f"\\{command}"
        search_from = 0
        while marker in value[search_from:]:
            start = value.find(marker, search_from)
            group_start = start + len(marker)
            while group_start < len(value) and value[group_start].isspace():
                group_start += 1
            end = group_end(value, group_start)
            if end is None:
                search_from = group_start
                continue
            content = latex_to_terminal(value[group_start + 1:end])
            rendered = replacement(content)
            value = value[:start] + rendered + value[end + 1:]
            search_from = start + len(rendered)
        return value

    def replace_fractions(value: str) -> str:
        marker = "\\frac"
        while marker in value:
            start = value.rfind(marker)
            numerator_start = start + len(marker)
            while numerator_start < len(value) and value[numerator_start].isspace():
                numerator_start += 1
            numerator_end = group_end(value, numerator_start)
            if numerator_end is None:
                break
            denominator_start = numerator_end + 1
            while denominator_start < len(value) and value[denominator_start].isspace():
                denominator_start += 1
            denominator_end = group_end(value, denominator_start)
            if denominator_end is None:
                break
            numerator = latex_to_terminal(value[numerator_start + 1:numerator_end])
            denominator = latex_to_terminal(value[denominator_start + 1:denominator_end])
            value = value[:start] + f"({numerator}) / ({denominator})" + value[denominator_end + 1:]
        return value

    def replace_scripts(value: str, marker: str, table: dict[int, str]) -> str:
        pattern = rf"\{marker}(?:\{{([^{{}}]+)\}}|([^\s]))"

        def replace(match: re.Match) -> str:
            content = match.group(1) or match.group(2)
            translated = content.translate(table)
            if marker == "^" and translated == "°":
                return "°"
            if len(translated) == len(content) and translated != content:
                return translated
            return f"{marker}({content})"

        return re.sub(pattern, replace, value)

    def latex_to_terminal(value: str) -> str:
        value = replace_fractions(value)
        value = replace_group_command(value, "sqrt", lambda content: f"√({content})")
        for command in ("text", "textrm", "mathrm", "mathbf", "mathit", "operatorname"):
            value = replace_group_command(value, command, lambda content: content)
        for command in ("overline", "underline", "vec", "hat", "bar"):
            value = replace_group_command(value, command, lambda content, name=command: f"{name}({content})")

        value = re.sub(r"\\begin\{[^{}]+\}|\\end\{[^{}]+\}", "", value)
        value = value.replace("\\\\", "\n").replace("&", "")
        value = re.sub(r"\\[,;:!]", " ", value)
        value = re.sub(
            r"\\([A-Za-z]+)",
            lambda match: _LATEX_COMMANDS.get(match.group(1), match.group(1)),
            value,
        )
        value = replace_scripts(value, "^", _SUPERSCRIPTS)
        value = replace_scripts(value, "_", _SUBSCRIPTS)
        value = value.replace("\\{", "{").replace("\\}", "}")
        value = value.replace("{", "").replace("}", "")
        return re.sub(r"[ \t]+", " ", value).strip()

    def format_non_code(part: str) -> str:
        patterns = (
            re.compile(r"\$\$(.*?)\$\$", re.DOTALL),
            re.compile(r"\\\[(.*?)\\\]", re.DOTALL),
            re.compile(r"(?<!\\)\$(?!\$)([^$\n]+?)(?<!\\)\$(?!\$)"),
            re.compile(r"\\\((.*?)\\\)", re.DOTALL),
        )
        for pattern in patterns:
            part = pattern.sub(lambda match: latex_to_terminal(match.group(1)), part)
        return part

    return "".join(
        part if index % 2 else format_non_code(part)
        for index, part in enumerate(_MARKDOWN_CODE.split(text))
    )


def render_terminal_markdown(text: str, style: str = "none") -> Markdown:
    """创建供 Rich 终端使用的 Markdown 渲染对象。"""
    return Markdown(_format_math_for_terminal(text), style=style)


def render_markdown_to_formatted_text(text: str, width: int = 80):
    """将 Rich Markdown 转换成 prompt_toolkit 可以显示的格式片段。"""
    buf = StringIO()
    c = Console(file=buf, force_terminal=True, color_system="truecolor", width=width)
    c.print(render_terminal_markdown(text))
    rendered = buf.getvalue()
    if rendered.endswith("\n"):
        rendered = rendered[:-1]
    return to_formatted_text(ANSI(rendered))


def render_markdown_to_plain_text(text: str, width: int = 160) -> str:
    """渲染 Markdown 后返回适合摘要和列表预览的单行纯文本。"""
    buf = StringIO()
    c = Console(file=buf, color_system=None, width=width)
    c.print(render_terminal_markdown(text))
    return " ".join(buf.getvalue().split())


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
        return ("class:badge-grill", "[ ⏳ 待诊断 ]")
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


def select_from_list(items, render_fn, title="", footer="[↑↓/PgUp/PgDn] 选择   [Enter] 编辑   [q/Esc] 返回"):
    if not items:
        return None

    state = {
        "current": 0,
        "offset": 0,
        "terminal_rows": 24,
    }

    def visible_rows():
        return max(3, state["terminal_rows"] - 8)

    def sync_offset():
        page_size = visible_rows()
        if state["current"] < state["offset"]:
            state["offset"] = state["current"]
        elif state["current"] >= state["offset"] + page_size:
            state["offset"] = state["current"] - page_size + 1

    def get_text():
        sync_offset()
        lines = []
        if title:
            lines.append(("class:title", f"✦ {title}\n\n"))

        page_size = visible_rows()
        start = state["offset"]
        end = min(len(items), start + page_size)

        if start > 0:
            lines.append(("class:dim", f"  ↑ 还有 {start} 项\n"))

        for i in range(start, end):
            item = items[i]
            if i == state["current"]:
                lines.append(("class:selected", f" ▶ {render_fn(item)} \n"))
            else:
                lines.append(("class:unselected", f"   {render_fn(item)}\n"))

        if end < len(items):
            lines.append(("class:dim", f"  ↓ 还有 {len(items) - end} 项\n"))

        lines.append(("", "\n"))
        lines.append(("class:footer", footer))
        return lines

    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        state["terminal_rows"] = event.app.output.get_size().rows
        if state["current"] > 0:
            state["current"] -= 1
            sync_offset()

    @kb.add("down")
    def _(event):
        state["terminal_rows"] = event.app.output.get_size().rows
        if state["current"] < len(items) - 1:
            state["current"] += 1
            sync_offset()

    @kb.add("pageup")
    def _(event):
        state["terminal_rows"] = event.app.output.get_size().rows
        page_size = visible_rows()
        state["current"] = max(0, state["current"] - page_size)
        sync_offset()

    @kb.add("pagedown")
    def _(event):
        state["terminal_rows"] = event.app.output.get_size().rows
        page_size = visible_rows()
        state["current"] = min(len(items) - 1, state["current"] + page_size)
        sync_offset()

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


def render_error_detail(error, error_number: int, width: int = 72):
    """渲染工作台右栏，动态内容统一支持 Markdown 和终端数学格式。"""
    _, badge = render_status_badge(error.status)
    lines = [("class:title", f"Error #{error_number}  {badge}\n")]
    origin_labels = {
        "record": "手动录入",
        "ocr": "OCR 校对",
        "drill": "Drill 衍生",
        "unknown": "历史来源未知",
    }
    origin = getattr(error, "origin", "unknown")
    lines.append(
        ("class:dim", f"来源: {origin_labels.get(origin, '历史来源未知')}\n")
    )
    source_id = getattr(error, "source_error_id", None)
    attempt_id = getattr(error, "source_drill_attempt_id", None)
    if source_id or attempt_id:
        lines.append(
            (
                "class:dim",
                "数据库关联 ID: "
                f"source_error_id={source_id or '-'}，"
                f"source_drill_attempt_id={attempt_id or '-'}\n",
            )
        )
    lines.append(("class:dim", f"创建时间: {error.created_at.strftime('%Y-%m-%d %H:%M')}\n"))
    lines.append(("class:dim", "─────────────────────────────────────────────────────\n\n"))

    def append_markdown(label_style: str, label: str, content: str):
        lines.append((label_style, label))
        lines.extend(render_markdown_to_formatted_text(content, width=width))
        lines.append(("", "\n\n"))

    append_markdown("class:subtitle", "📌 题目:\n", error.question)
    append_markdown(
        "class:user-input",
        "💭 你的思路:\n",
        error.user_thoughts or "未记录用户思路",
    )

    lines.append(("class:subtitle", "📚 参考答案及解析:\n"))
    if error.reference_answer:
        lines.extend(render_markdown_to_formatted_text(error.reference_answer, width=width))
        lines.append(("", "\n\n"))
    else:
        lines.append(("class:hint", "暂无参考答案\n\n"))

    if error.grilling_summary:
        append_markdown(
            "class:label",
            "💡 Grill 诊断摘要:\n",
            error.grilling_summary,
        )

    if error.status == "pending-grill":
        action = "继续未完成的 Grill 诊断" if error.grilling_conversation else "开始 Grill 诊断"
        lines.append(("class:nextstep", f"⚡ 下一步建议: 按 [g] 或 [Enter] {action} (Grill)\n"))
    elif error.status == "pending-teach":
        lines.append(("class:nextstep", "⚡ 下一步建议: 按 [t] 或 [Enter] 开始讲解；按 [g] 查看 Grill 记录\n"))
    elif error.status == "done":
        lines.append(("class:nextstep", "✓ 该错题已研讨完成！按 [g] 查看 Grill 记录，按 [t] 继续讲解\n"))
    return lines


def select_error_split_view(errors):
    """Master-detail workspace with list and detail scrolling."""
    if not errors:
        return None, None

    state = {
        "current": 0,
        "left_offset": 0,
        "terminal_rows": 24,
    }

    def visible_rows(reserved: int = 7):
        return max(3, state["terminal_rows"] - reserved)

    def sync_left_offset():
        page_size = visible_rows()
        if state["current"] < state["left_offset"]:
            state["left_offset"] = state["current"]
        elif state["current"] >= state["left_offset"] + page_size:
            state["left_offset"] = state["current"] - page_size + 1

    def get_left_text():
        sync_left_offset()
        lines = []
        lines.append(("class:title", " 错题清单 \n"))
        lines.append(("class:dim", " ──────────────────────\n"))
        start = state["left_offset"]
        end = min(len(errors), start + visible_rows())
        if start:
            lines.append(("class:dim", f" ↑ 还有 {start} 条\n"))
        for i in range(start, end):
            e = errors[i]
            badge_cls, badge_str = render_status_badge(e.status)
            question_text = render_markdown_to_plain_text(e.question)
            q_snippet = question_text[:15]
            if len(question_text) > 15:
                q_snippet += ".."

            if i == state["current"]:
                lines.append(("class:selected", f"▶ #{i+1:<2} "))
                lines.append((badge_cls + " class:selected", f"{badge_str} "))
                lines.append(("class:selected", f"{q_snippet}\n"))
            else:
                lines.append(("", f"  #{i+1:<2} "))
                lines.append((badge_cls, f"{badge_str} "))
                lines.append(("", f"{q_snippet}\n"))

        if end < len(errors):
            lines.append(("class:dim", f" ↓ 还有 {len(errors) - end} 条\n"))
        return lines

    def get_right_text():
        if state["current"] >= len(errors):
            return [("", "无数据")]
        return render_error_detail(errors[state["current"]], state["current"] + 1)

    def get_footer_text():
        return [
            ("class:footer", " [↑/↓] 列表  [PgUp/PgDn] 详情  [Enter] 默认处理  [g] Grill 诊断/记录  [t] 讲解  [d] 删除  [q] 返回")
        ]

    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        state["terminal_rows"] = event.app.output.get_size().rows
        if state["current"] > 0:
            state["current"] -= 1
            right_win.vertical_scroll = 0
            sync_left_offset()

    @kb.add("down")
    def _(event):
        state["terminal_rows"] = event.app.output.get_size().rows
        if state["current"] < len(errors) - 1:
            state["current"] += 1
            right_win.vertical_scroll = 0
            sync_left_offset()

    @kb.add("pageup")
    def _(event):
        state["terminal_rows"] = event.app.output.get_size().rows
        right_win.vertical_scroll = max(0, right_win.vertical_scroll - visible_rows(5))

    @kb.add("pagedown")
    def _(event):
        state["terminal_rows"] = event.app.output.get_size().rows
        right_win.vertical_scroll += visible_rows(5)

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

    left_win = Window(content=FormattedTextControl(get_left_text), width=42, wrap_lines=False)
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


def popup_input(
    title: str,
    prompt_text: str,
    multiline: bool = True,
    initial_text: str = "",
) -> str | None:
    text_area = TextArea(text=initial_text, multiline=multiline, wrap_lines=True)
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
        Window(content=FormattedTextControl([
            ("class:title", f"✦ {title}\n\n"),
            ("", f"{prompt_text}"),
        ]), dont_extend_height=True, wrap_lines=True),
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
        lines.extend(render_markdown_to_formatted_text(body))
    lines.append(("", "\n"))

    footer_text = footer or "↑ / ↓ / PgUp / PgDn 滚动  │  Enter / Esc / q 关闭"
    body_window = Window(content=FormattedTextControl(lines), wrap_lines=True)
    layout = HSplit([
        body_window,
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

    @kb.add("up")
    def _(event):
        body_window.vertical_scroll = max(0, body_window.vertical_scroll - 1)

    @kb.add("down")
    def _(event):
        body_window.vertical_scroll += 1

    @kb.add("pageup")
    def _(event):
        page_size = max(1, event.app.output.get_size().rows - 5)
        body_window.vertical_scroll = max(0, body_window.vertical_scroll - page_size)

    @kb.add("pagedown")
    def _(event):
        page_size = max(1, event.app.output.get_size().rows - 5)
        body_window.vertical_scroll += page_size

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

    content_lines = [
        ("class:title", "🎯 综合演练 - 答题\n\n"),
    ]
    content_lines.extend(render_markdown_to_formatted_text(question_text))
    content_lines.append(("", "\n\n"))
    content_lines.append(("class:user-input", "输入你的答案与解题思路（Alt+Enter 换行，Enter 提交）：\n"))

    question_window = Window(
        content=FormattedTextControl(content_lines),
        dont_extend_height=True,
        wrap_lines=True,
    )

    layout = HSplit([
        question_window,
        Window(height=1, content=FormattedTextControl([("class:dim", "─" * 60)])),
        Window(text_area.control, height=Dimension(min=5, max=15)),
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

    body_window = Window(content=FormattedTextControl(lines), wrap_lines=True)

    layout = HSplit([
        body_window,
        Window(height=1, content=FormattedTextControl([("class:footer", "  [y] 确认    [n/Esc] 取消")]), dont_extend_height=True),
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

    @kb.add("up")
    def _(event):
        body_window.vertical_scroll = max(0, body_window.vertical_scroll - 1)

    @kb.add("down")
    def _(event):
        body_window.vertical_scroll += 1

    @kb.add("pageup")
    def _(event):
        page_size = max(1, event.app.output.get_size().rows - 5)
        body_window.vertical_scroll = max(0, body_window.vertical_scroll - page_size)

    @kb.add("pagedown")
    def _(event):
        page_size = max(1, event.app.output.get_size().rows - 5)
        body_window.vertical_scroll += page_size

    frame = Frame(layout, title="确认操作")
    container = Box(frame, padding=1)

    app = Application(
        layout=Layout(container),
        key_bindings=kb,
        style=GLOBAL_STYLE,
        full_screen=True,
    )
    return app.run()
