from prompt_toolkit import Application, PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import FormattedTextControl, HSplit, Layout, Window
from prompt_toolkit.styles import Style
from rich.console import Console

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


def user_input(prompt: str = "", default: str = "") -> str:
    if default:
        console.print(f"{prompt} [dim]({default})[/dim]")
    else:
        console.print(prompt)
    val = _sline.prompt("> ")
    return val if val else default


def multiline_input(prompt: str = "") -> str:
    global _hint_shown
    if not _hint_shown:
        console.print("[dim]提示：Alt+Enter 换行，Enter 提交[/dim]")
        _hint_shown = True
    console.print(prompt)
    return _psession.prompt().strip()


def prompt_line(text: str = "") -> str:
    prompt = HTML("<b>❯ </b>")
    if _completer is not None:
        return _sline.prompt(prompt, completer=_completer, complete_while_typing=True)
    return _sline.prompt(prompt)


def sysmsg(text: str):
    console.print(f"  [dim]⎿[/dim]  {text}")


def errmsg(text: str):
    console.print(f"  [dim]⎿[/dim]  [red]{text}[/red]")


def select_from_list(items, render_fn, title="", footer="[↑↓]选择 [Enter]查看 [q]返回"):
    if not items:
        return None

    state = {"current": 0}

    def get_text():
        lines = []
        if title:
            lines.append(("class:title", title + "\n\n"))
        for i, item in enumerate(items):
            if i == state["current"]:
                lines.append(("class:selected", "▶ " + render_fn(item) + "\n"))
            else:
                lines.append(("class:unselected", "  " + render_fn(item) + "\n"))
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

    style = Style([
        ("title", "bold"),
        ("selected", "fg:ansicyan bold"),
        ("unselected", ""),
        ("footer", "fg:ansibrightblack"),
    ])

    app = Application(
        layout=Layout(HSplit([Window(content=FormattedTextControl(get_text), wrap_lines=True)])),
        key_bindings=kb,
        style=style,
        full_screen=True,
        mouse_support=False,
    )
    return app.run()
