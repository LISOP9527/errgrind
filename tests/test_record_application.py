"""Field OCR is a draft input operation, including for thoughts-only images."""

import unittest
import tempfile
from unittest.mock import Mock, patch
from pathlib import Path

from errgrind.application import (
    ErrGrindApplication,
    OutputContractError,
    WorkflowModelError,
    WorkflowPersistenceError,
)
from errgrind.db.ops import Database
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


class RecordErrorApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "errors.db"))
        self.app = ErrGrindApplication(self.db, Mock(), Mock())

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_record_error_validates_normalizes_and_returns_public_record(self):
        result = self.app.record_error(
            "  求 x  ", "  我直接套公式  ", "  x=2  ", origin="ocr"
        )
        self.assertEqual(result.question, "求 x")
        self.assertEqual(result.user_thoughts, "我直接套公式")
        self.assertEqual(result.reference_answer, "x=2")
        self.assertEqual(result.origin, "ocr")
        self.assertEqual(result.status, "pending-grill")
        self.assertIsNone(result.grilling_diagnostic_state)

    def test_record_error_rejects_missing_required_fields_or_bad_origin(self):
        cases = (
            ("", "思路", None, "题目不能为空"),
            ("题目", "  ", None, "用户思路不能为空"),
            ("题目", "思路", 42, "参考答案必须是文字"),
            ("题目", "思路", None, "录题来源必须是 record 或 ocr"),
        )
        for question, thoughts, answer, expected in cases:
            with self.subTest(expected=expected):
                origin = "drill" if expected.startswith("录题") else "record"
                with self.assertRaisesRegex(OutputContractError, expected):
                    self.app.record_error(question, thoughts, answer, origin=origin)
        self.assertEqual(self.db.list_all_errors(), [])

    def test_plain_record_defaults_and_optional_answer(self):
        for answer in (None, "", "  "):
            result = self.app.record_error("题目", "没有思路", answer)
            self.assertEqual(result.origin, "record")
            self.assertIsNone(result.reference_answer)
            self.assertIsNone(result.source_error_id)
            self.assertIsNone(result.source_drill_attempt_id)

    def test_record_error_wraps_persistence_failure(self):
        db = Mock()
        db.create_error.side_effect = RuntimeError("SQLITE_INTERNAL_DETAILS")
        app = ErrGrindApplication(db, Mock(), Mock())
        with self.assertRaisesRegex(WorkflowPersistenceError, "保存 Error 失败") as raised:
            app.record_error("题目", "思路")
        self.assertNotIn("SQLITE_INTERNAL_DETAILS", str(raised.exception))
