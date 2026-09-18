"""Focused regressions for the Web Markdown/KaTeX boundary."""

import unittest

try:
    from errgrind.web.rendering import render_markdown
except ImportError:  # The Web extra is optional for core-only installs.
    render_markdown = None


@unittest.skipUnless(render_markdown is not None, "install the web extra")
class WebRenderingTests(unittest.TestCase):
    def test_cjk_adjacent_strong_text_renders_in_paragraphs_and_lists(self):
        paragraph = render_markdown("**文字。**例如")
        list_item = render_markdown("- **文字。**后续")

        self.assertIn("<strong>文字。\u200b</strong>例如", paragraph)
        self.assertIn("<strong>文字。\u200b</strong>后续", list_item)
        self.assertNotIn("**文字。**", paragraph)
        self.assertNotIn("**文字。**", list_item)

    def test_multiline_bracket_math_is_rendered_even_when_delimiter_is_doubled(self):
        normal = "前文\n\\[\n\\frac{x+1}{2}=3\n\\]\n后文"
        doubled = "前文\n\\\\[\n\\frac{x+1}{2}=3\n\\\\]\n后文"

        for source in (normal, doubled):
            with self.subTest(source=repr(source)):
                rendered = render_markdown(source)
                self.assertIn('class="math-display"', rendered)
                self.assertIn('data-tex="', rendered)
                self.assertNotIn("<p>\\[", rendered)

    def test_code_spans_and_fences_are_not_normalized_as_markdown_or_math(self):
        rendered = render_markdown(
            "`**文字。**例如`\n\n```tex\n**文字。**例如\n\\[x^2\\]\n```"
        )

        self.assertIn("<code>**文字。**例如</code>", rendered)
        self.assertIn("<pre><code class=\"language-tex\">**文字。**例如\n\\[x^2\\]", rendered)
        self.assertNotIn("<strong>", rendered)
        self.assertNotIn("class=\"math-", rendered)


if __name__ == "__main__":
    unittest.main()
