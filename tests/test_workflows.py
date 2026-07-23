import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from errgrind.cli.commands import _cmd_drill
from errgrind.cli.state import AppState
from errgrind.db.ops import Database
from errgrind.llm.prompts import PromptManager


class DatabaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "errgrind.db"))

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_error_moves_through_grill_and_teach(self):
        error_id = self.db.create_error("求 x", "先移项", "x = 2")
        self.assertEqual(self.db.get_error(error_id).status, "pending-grill")

        grilling = json.dumps([
            {"role": "system", "content": "prompt"},
            {"role": "assistant", "content": "忽略了条件"},
        ])
        self.db.update_grilling(error_id, grilling, "忽略了条件")
        grilled = self.db.get_error(error_id)
        self.assertEqual(grilled.status, "pending-teach")
        self.assertEqual(grilled.grilling_summary, "忽略了条件")
        self.assertEqual(self.db.get_drill_context(1), [("求 x", "忽略了条件")])

        self.db.update_teach(error_id, json.dumps([{ "role": "assistant", "content": "讲解" }]))
        taught = self.db.get_error(error_id)
        self.assertEqual(taught.status, "done")
        self.assertIsNotNone(taught.teach_conversation)

    def test_regrill_cleanup_and_delete_keep_other_errors(self):
        first = self.db.create_error("第一题")
        second = self.db.create_error("第二题")
        self.db.update_grilling(first, "[]", "旧摘要")
        self.db.update_teach(first, "[]")

        self.db.clear_teach_and_summary(first)
        self.db.set_status(first, "pending-teach")
        self.db.delete_error(second)

        record = self.db.get_error(first)
        self.assertEqual(record.status, "pending-teach")
        self.assertIsNone(record.grilling_summary)
        self.assertIsNone(record.teach_conversation)
        self.assertIsNone(self.db.get_error(second))


class _FakeLLM:
    def __init__(self, judgment):
        self.judgment = judgment
        self.prompts = []

    def chat_json(self, messages):
        self.prompts.append(messages[0]["content"])
        if len(self.prompts) == 1:
            return {"question": "新的练习题", "reference_answer": "标准答案"}
        return self.judgment


class DrillWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "errgrind.db"))
        error_id = self.db.create_error("原题")
        self.db.update_grilling(error_id, "[]", "忽略条件")

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def _state(self, judgment):
        return AppState(
            db=self.db,
            llm=_FakeLLM(judgment),
            prompts=PromptManager(),
            cfg={"drill_context_n": 10},
        )

    def test_correct_drill_answer_does_not_create_error(self):
        state = self._state({"is_correct": True, "feedback": "正确"})

        with (
            patch("errgrind.cli.commands.popup_drill_answer", return_value="我的答案"),
            patch("errgrind.cli.commands.popup_content"),
            patch("errgrind.cli.commands.sysmsg"),
            patch("errgrind.cli.commands.successmsg"),
        ):
            _cmd_drill(state, "")

        self.assertEqual(self.db.count_by_status()["total"], 1)
        self.assertIn("[审讯摘要]\n忽略条件", state.llm.prompts[0])

    def test_incorrect_drill_answer_creates_follow_up_error(self):
        state = self._state({"is_correct": False, "feedback": "遗漏条件"})

        with (
            patch("errgrind.cli.commands.popup_drill_answer", return_value="错误答案"),
            patch("errgrind.cli.commands.popup_content"),
            patch("errgrind.cli.commands.sysmsg"),
            patch("errgrind.cli.commands.errmsg"),
        ):
            _cmd_drill(state, "")

        errors = self.db.list_all_errors()
        self.assertEqual(len(errors), 2)
        follow_up = next(error for error in errors if error.question == "新的练习题")
        self.assertEqual(follow_up.status, "pending-grill")
        self.assertEqual(follow_up.user_thoughts, "错误答案")
        self.assertEqual(follow_up.reference_answer, "标准答案")


if __name__ == "__main__":
    unittest.main()
