"""Safe Markdown and math rendering for the local WebUI.

Math is deliberately emitted as escaped TeX in a data attribute.  The browser
side KaTeX helper can turn those nodes into equations; until then the escaped
source remains readable.  Keeping TeX out of HTML source also means a model
response such as ``$<img onerror=...>$`` cannot become executable markup.
"""
from __future__ import annotations

from html import escape
import re

from markdown_it import MarkdownIt
from mdit_py_plugins.texmath import texmath_plugin


_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af"
_CJK_FOLLOWING_STRONG = re.compile(
    rf"(?P<marker>\*\*|__)(?P<body>[^\r\n]*?)(?P=marker)(?=[{_CJK}])"
)


def _fenced_code_end(value: str, start: int) -> int | None:
    """Return the end of a fenced code block beginning at ``start``."""
    line_end = value.find("\n", start)
    if line_end < 0:
        line_end = len(value)
    opening = re.match(r"[ \t]{0,3}(`{3,}|~{3,})", value[start:line_end])
    if not opening:
        return None
    marker = opening.group(1)
    marker_char, marker_length = marker[0], len(marker)
    cursor = line_end + 1 if line_end < len(value) else len(value)
    while cursor < len(value):
        next_end = value.find("\n", cursor)
        if next_end < 0:
            next_end = len(value)
        closing = re.match(
            rf"[ \t]{{0,3}}{re.escape(marker_char)}{{{marker_length},}}[ \t]*$",
            value[cursor:next_end],
        )
        if closing:
            return next_end + (1 if next_end < len(value) else 0)
        cursor = next_end + (1 if next_end < len(value) else 0)
    return len(value)


def _inline_code_end(value: str, start: int) -> int | None:
    run = re.match(r"`+", value[start:])
    if not run:
        return None
    marker = run.group(0)
    end = value.find(marker, start + len(marker))
    return end + len(marker) if end >= 0 else None


def _math_end(value: str, start: int) -> tuple[int, str] | None:
    """Recognize bracket/dollar math while allowing one accidental extra slash.

    Some stored model Markdown contains ``\\[ ... \\]`` (two literal
    backslashes) instead of ``\[ ... \]``.  Treat that exact delimiter typo as
    a display-math delimiter, but leave the math body untouched.
    """
    delimiters = (
        ("\\\\[", "\\\\]", "\\[", "\\]"),
        ("\\[", "\\]", "\\[", "\\]"),
        ("\\\\(", "\\\\)", "\\(", "\\)"),
        ("\\(", "\\)", "\\(", "\\)"),
        ("$$", "$$", "$$", "$$"),
    )
    for opening, closing, normalized_opening, normalized_closing in delimiters:
        if not value.startswith(opening, start):
            continue
        end = value.find(closing, start + len(opening))
        if end < 0:
            return None
        return end + len(closing), normalized_opening + value[start + len(opening):end] + normalized_closing

    if value.startswith("$", start) and not value.startswith("$$", start):
        if start > 0 and value[start - 1] == "\\":
            return None
        cursor = start + 1
        while cursor < len(value):
            cursor = value.find("$", cursor)
            if cursor < 0:
                return None
            if value[cursor - 1] != "\\" and not value.startswith("$$", cursor):
                return cursor + 1, "$" + value[start + 1:cursor] + "$"
            cursor += 1
    return None


def _normalize_markdown(value: str) -> str:
    """Normalize known model Markdown quirks outside code and math.

    CommonMark deliberately rejects some strong delimiters when CJK
    punctuation is immediately before the closing ``**``/``__`` and another
    CJK character follows.  A zero-width space inside the strong span gives
    the parser an unambiguous boundary without adding visible whitespace.
    Code spans/fences and math expressions are copied byte-for-byte (apart
    from the explicitly supported doubled bracket delimiter typo).  A bracket
    display block is separated from surrounding paragraph text when needed so
    Markdown-It can recognize a model response that starts the block on a new
    line without a blank paragraph boundary.
    """
    output: list[str] = []
    plain: list[str] = []

    def flush_plain() -> None:
        if plain:
            output.append(_CJK_FOLLOWING_STRONG.sub(
                lambda match: (
                    ("**" if match.group("marker") == "__" else match.group("marker"))
                    + match.group("body")
                    + "\u200b"
                    + ("**" if match.group("marker") == "__" else match.group("marker"))
                ),
                "".join(plain),
            ))
            plain.clear()

    def append_display_math(normalized: str, start: int) -> None:
        line_start = value.rfind("\n", 0, start) + 1
        starts_at_line = not value[line_start:start].strip()
        if starts_at_line:
            current = "".join(output)
            if current and not current.endswith("\n\n"):
                output.append("\n" if current.endswith("\n") else "\n\n")
            output.append(normalized)
            output.append("\n\n")
        else:
            output.append(normalized)

    cursor = 0
    while cursor < len(value):
        at_line_start = cursor == 0 or value[cursor - 1] == "\n"
        if at_line_start:
            end = _fenced_code_end(value, cursor)
            if end is not None:
                flush_plain()
                output.append(value[cursor:end])
                cursor = end
                continue

        if value[cursor] == "`":
            end = _inline_code_end(value, cursor)
            if end is not None:
                flush_plain()
                output.append(value[cursor:end])
                cursor = end
                continue

        math = _math_end(value, cursor)
        if math is not None:
            end, normalized = math
            flush_plain()
            if normalized.startswith("\\["):
                append_display_math(normalized, cursor)
            else:
                output.append(normalized)
            cursor = end
            continue

        plain.append(value[cursor])
        cursor += 1

    flush_plain()
    return "".join(output)


def _math_node(content: str, display: bool) -> str:
    safe = escape(content, quote=True)
    cls = "math-display" if display else "math-inline"
    tag = "div" if display else "span"
    return f'<{tag} class="{cls}" data-tex="{safe}">{escape(content)}</{tag}>'


def _inline_math(renderer, tokens, idx, options, env):
    return _math_node(tokens[idx].content, False)


def _single_math(renderer, tokens, idx, options, env):
    return _math_node(tokens[idx].content, False)


def _block_math(renderer, tokens, idx, options, env):
    return _math_node(tokens[idx].content, True) + "\n"


def build_markdown() -> MarkdownIt:
    # html=False is essential: model output must be treated as text, including
    # HTML-looking content nested inside a math delimiter.
    parser = MarkdownIt("commonmark", {"html": False, "linkify": True})
    parser.enable("table")
    # The two registrations cover both dollar delimiters and \(...\)/\[...\].
    # mdit-py-plugins uses the same token names and the latter registration
    # retains both rule sets in the parser.
    texmath_plugin(parser, delimiters="brackets")
    texmath_plugin(parser, delimiters="dollars")
    parser.add_render_rule("math_inline", _inline_math)
    parser.add_render_rule("math_single", _single_math)
    parser.add_render_rule("math_block", _block_math)
    parser.add_render_rule("math_block_eqno", _block_math)
    return parser


_MARKDOWN = build_markdown()


def render_markdown(value: str | None) -> str:
    """Render standard Markdown and math into safe HTML."""
    return _MARKDOWN.render(_normalize_markdown(value or ""))
