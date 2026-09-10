"""Safe Markdown and math rendering for the local WebUI.

Math is deliberately emitted as escaped TeX in a data attribute.  The browser
side KaTeX helper can turn those nodes into equations; until then the escaped
source remains readable.  Keeping TeX out of HTML source also means a model
response such as ``$<img onerror=...>$`` cannot become executable markup.
"""
from __future__ import annotations

from html import escape

from markdown_it import MarkdownIt
from mdit_py_plugins.texmath import texmath_plugin


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
    return _MARKDOWN.render(value or "")
