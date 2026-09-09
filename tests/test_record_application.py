"""Field OCR is a draft input operation, including for thoughts-only images."""

import unittest
from unittest.mock import Mock, patch

from errgrind.application import ErrGrindApplication, OutputContractError, WorkflowModelError
from errgrind.llm.ocr import OcrError
from errgrind.llm.prompts import PromptManager


class RecordImageApplicationTests(unittest.TestCase):
    def setUp(self):
        self.db = Mock()
        self.llm = Mock()
        self.app = ErrGrindApplication(self.db, self.llm, PromptManager())

    def test_each_field_gets_targeted_prompt_and_never_writes_a_record(self):
        for field, label, draft in (
            ("question", "题目", "求 x 的值"),
            ("user_thoughts", "用户当时的思路或作答", "我直接除以 x"),
            ("reference_answer", "参考答案", "x = 0 或 x = 1"),
        ):
            with self.subTest(field=field):
                self.llm.transcribe_image.return_value = f"  {draft}\n"
                result = self.app.transcribe_record_field("/tmp/独立截图.png", field)
                self.assertEqual(result, draft)
                path, prompt = self.llm.transcribe_image.call_args.args
                self.assertEqual(path, "/tmp/独立截图.png")
                self.assertIn(f"**{label}**", prompt)
                self.assertIn('{"text":', prompt)
        self.assertEqual(self.db.mock_calls, [])

    def test_drill_answer_uses_current_answer_semantics_and_never_persists(self):
        self.llm.transcribe_image.return_value = "  当前答案与思路  "
        result = self.app.transcribe_drill_answer("/tmp/drill.png")
        self.assertEqual(result, "当前答案与思路")
        path, prompt = self.llm.transcribe_image.call_args.args
        self.assertEqual(path, "/tmp/drill.png")
        self.assertIn("当前 Drill 的答案与解题思路", prompt)
        self.assertIn("不是过去 Error 的思路", prompt)
        self.assertIn("忽略图片中的印刷题目和参考解析", prompt)
        self.assertEqual(self.db.mock_calls, [])

    def test_drill_answer_usage_is_tagged_as_drill_ocr(self):
        self.llm.transcribe_image.return_value = "答案"
        with patch("errgrind.application.service.usage_scope") as scope:
            scope.return_value.__enter__.return_value = None
            self.app.transcribe_drill_answer("answer.png")
        self.assertTrue(
            any(
                call.kwargs.get("action") == "drill"
                and call.kwargs.get("stage") == "ocr_drill_answer"
                for call in scope.call_args_list
            )
        )

    def test_unknown_field_does_not_call_model(self):
        with self.assertRaises(OutputContractError):
            self.app.transcribe_record_field("a.png", "grilling_summary")
        self.llm.transcribe_image.assert_not_called()

    def test_missing_image_capability_is_clear(self):
        app = ErrGrindApplication(self.db, object(), PromptManager())
        with self.assertRaisesRegex(WorkflowModelError, "不支持"):
            app.transcribe_record_field("a.png", "question")

    def test_invalid_drafts_are_rejected_without_persistence(self):
        for value in (None, {}, "", "  \n"):
            with self.subTest(value=value):
                self.llm.transcribe_image.return_value = value
                with self.assertRaises(OutputContractError):
                    self.app.transcribe_record_field("a.png", "user_thoughts")
        self.assertEqual(self.db.mock_calls, [])

    def test_image_validation_error_is_explained(self):
        self.llm.transcribe_image.side_effect = OcrError("图片不存在或无法访问")
        with self.assertRaisesRegex(OutputContractError, "图片不存在"):
            self.app.transcribe_record_field("missing.png", "question")

    def test_provider_failure_does_not_expose_raw_response(self):
        self.llm.transcribe_image.side_effect = RuntimeError("RAW_RESPONSE")
        with self.assertRaises(WorkflowModelError) as raised:
            self.app.transcribe_record_field("a.png", "reference_answer")
        self.assertNotIn("RAW_RESPONSE", str(raised.exception))
        self.assertEqual(self.db.mock_calls, [])

    def test_interruptions_propagate_without_writing(self):
        for interruption in (KeyboardInterrupt, EOFError):
            with self.subTest(interruption=interruption):
                self.llm.transcribe_image.side_effect = interruption()
                with self.assertRaises(interruption):
                    self.app.transcribe_record_field("a.png", "user_thoughts")
        self.assertEqual(self.db.mock_calls, [])
