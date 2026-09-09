"""Exercise actual terminal key bindings without timing-based input threads."""

import asyncio
import unittest
from unittest.mock import Mock, patch

from prompt_toolkit import Application
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from errgrind.cli.ui import popup_drill_answer, popup_input


class RecordPopupInputTests(unittest.TestCase):
    def _run_popup(self, screens, loader=None, initial_text="", popup=popup_input):
        # Each entry is sent only once its editor is running. None closes the
        # input pipe, exercising real EOF handling instead of Ctrl+D text.
        remaining = iter(screens)
        with create_pipe_input() as pipe:
            def make_application(**kwargs):
                app = Application(input=pipe, output=DummyOutput(), **kwargs)
                original_run = app.run

                def run():
                    timer = None

                    def start():
                        nonlocal timer
                        chunk = next(remaining)
                        if chunk is None:
                            pipe.close()
                        else:
                            pipe.send_text(chunk)
                        timer = asyncio.get_running_loop().call_later(
                            2, lambda: app.exit(exception=TimeoutError("popup did not finish"))
                        )

                    try:
                        return original_run(pre_run=start)
                    finally:
                        if timer is not None:
                            timer.cancel()

                app.run = run
                return app

            with patch("errgrind.cli.ui.Application", side_effect=make_application):
                value = popup(
                    "记录", "输入：", initial_text=initial_text, image_loader=loader
                )
        self.assertEqual(list(remaining), [], "the editor submitted before consuming edits")
        return value

    def test_f2_appends_ocr_and_keeps_text_editable(self):
        value = self._run_popup(
            ["原草稿\x1b[12~", "修改\r"], loader=lambda: "识别内容"
        )
        self.assertEqual(value, "原草稿\n识别内容修改")

    def test_repeated_images_and_ctrl_o_append_in_order(self):
        loader = Mock(side_effect=["第一张", "第二张"])
        value = self._run_popup(
            ["\x0f", "\x1b[12~", "补充\r"], loader=loader, initial_text="已有文字"
        )
        self.assertEqual(value, "已有文字\n第一张\n第二张补充")
        self.assertEqual(loader.call_count, 2)

    def test_cancelled_loader_preserves_draft_and_allows_another_image(self):
        loader = Mock(side_effect=[None, "识别"])
        value = self._run_popup(
            ["草稿\x1b[12~", "保留\x1b[12~", "\r"], loader=loader
        )
        self.assertEqual(value, "草稿保留\n识别")

    def test_ocr_interruptions_reopen_editor_without_submitting(self):
        for interruption in (KeyboardInterrupt, EOFError):
            with self.subTest(interruption=interruption):
                value = self._run_popup(
                    ["草稿\x1b[12~", "继续编辑\r"],
                    loader=Mock(side_effect=interruption()),
                )
                self.assertEqual(value, "草稿继续编辑")

    def test_ctrl_c_and_eof_cancel_editor(self):
        for screens in (["草稿\x03"], [None]):
            with self.subTest(screens=screens):
                self.assertIsNone(self._run_popup(screens, loader=lambda: "不会使用"))

    def test_esc_after_ocr_cancels_instead_of_submitting_draft(self):
        self.assertIsNone(self._run_popup(
            ["\x1b[12~", "\x1b"], loader=lambda: "识别内容"
        ))

    def test_plain_popup_keeps_multiline_editing(self):
        self.assertEqual(self._run_popup(["一\x1b\r二\r"]), "一\n二")

    def test_drill_f2_and_ctrl_o_append_editable_drafts(self):
        loader = Mock(side_effect=["第一张", "第二张"])
        value = self._run_popup(
            ["原答案\x1b[12~", "修改\x0f", "补充\r"],
            loader=loader,
            popup=lambda title, prompt, **kwargs: popup_drill_answer(
                "题目", image_loader=kwargs.get("image_loader")
            ),
        )
        self.assertEqual(value, "原答案\n第一张修改\n第二张补充")
        self.assertEqual(loader.call_count, 2)

    def test_drill_cancelled_or_interrupted_loader_keeps_draft(self):
        for interruption in (None, KeyboardInterrupt(), EOFError()):
            with self.subTest(interruption=type(interruption).__name__):
                loader = Mock(side_effect=[interruption, "识别"] if interruption is not None else [None, "识别"])
                value = self._run_popup(
                    ["草稿\x1b[12~", "继续\x1b[12~", "\r"],
                    loader=loader,
                    popup=lambda title, prompt, **kwargs: popup_drill_answer(
                        "题目", image_loader=kwargs.get("image_loader")
                    ),
                )
                self.assertEqual(value, "草稿继续\n识别")

    def test_drill_ctrl_c_and_eof_cancel_panel(self):
        for screens in (["草稿\x03"], [None]):
            with self.subTest(screens=screens):
                value = self._run_popup(
                    screens, loader=lambda: "不会使用",
                    popup=lambda title, prompt, **kwargs: popup_drill_answer(
                        "题目", image_loader=kwargs.get("image_loader")
                    ),
                )
                self.assertIsNone(value)

    def test_drill_esc_after_ocr_discards_draft(self):
        value = self._run_popup(
            ["草稿\x1b[12~", "\x1b"],
            loader=lambda: "识别内容",
            popup=lambda title, prompt, **kwargs: popup_drill_answer(
                "题目", image_loader=kwargs.get("image_loader")
            ),
        )
        self.assertIsNone(value)
