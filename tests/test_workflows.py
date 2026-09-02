import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from errgrind.cli.commands import (
    _cmd_drill,
    _cmd_ocr,
    _cmd_record,
    _run_grilling,
    _run_teaching,
    _source_leak_terms,
)
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

    def test_delete_keeps_other_errors(self):
        first = self.db.create_error("第一题")
        second = self.db.create_error("第二题")
        self.db.update_grilling(first, "[]", "旧摘要")
        self.db.update_teach(first, "[]")

        self.db.delete_error(second)

        record = self.db.get_error(first)
        self.assertEqual(record.status, "done")
        self.assertEqual(record.grilling_summary, "旧摘要")
        self.assertEqual(record.teach_conversation, "[]")
        self.assertIsNone(self.db.get_error(second))

    def test_drill_context_uses_id_as_same_second_tiebreaker(self):
        first = self.db.create_error("较早题目")
        second = self.db.create_error("较新题目")
        self.db.update_grilling(first, "[]", "较早摘要")
        self.db.update_grilling(second, "[]", "较新摘要")
        self.db.conn.execute(
            "UPDATE error_records SET updated_at = '2026-07-28 12:00:00' "
            "WHERE id IN (?, ?)",
            (first, second),
        )
        self.db.conn.commit()

        self.assertEqual(
            self.db.get_drill_context(1),
            [("较新题目", "较新摘要")],
        )


class _FakeLLM:
    def __init__(self, responses, fail_on_call=None):
        self.responses = responses
        self.fail_on_call = fail_on_call
        self.prompts = []

    def chat_json(self, messages, **_kwargs):
        self.prompts.append("\n".join(message["content"] for message in messages))
        if len(self.prompts) == self.fail_on_call:
            raise RuntimeError("模拟 API 错误")
        return self.responses[len(self.prompts) - 1]


class DrillWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "errgrind.db"))
        self.source_marker = "SOURCE_ONLY_7F31"
        error_id = self.db.create_error(f"{self.source_marker}：原题")
        self.db.update_grilling(error_id, "[]", "忽略适用条件就直接套用方法")

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def _valid_spec(self):
        return {
            "source_error_number": 1,
            "target_pattern": {
                "mechanism": "未检查适用条件就直接使用熟悉方法",
                "trigger": "题面出现熟悉形式",
                "failure_behavior": "立即套用方法",
                "desired_behavior": "先检查所有适用条件",
                "success_signal": "在使用方法前主动列出并核对适用条件",
            },
            "new_problem": {
                "domain": "概率",
                "task_type": "calculate",
                "setting": "从受限制的样本空间计算事件概率",
                "task_goal": "确定样本空间后计算概率",
                "essential_trigger": "熟悉公式的适用条件并不自动成立",
                "solution_strategy": "先根据限制确定样本空间，再计算概率",
                "avoid": ["复杂排列组合"],
            },
            "difficulty": {
                "level": 3,
                "reasoning_depth": 3,
                "calculation_load": 2,
            },
        }

    @staticmethod
    def _valid_draft():
        return {
            "question": "新的练习题",
            "reference_answer": "先核对条件，再完成计算。",
        }

    def _run(self, llm, answer="先检查条件，再计算"):
        state = AppState(
            db=self.db,
            llm=llm,
            prompts=PromptManager(),
            cfg={"drill_context_n": 10},
        )
        with (
            patch(
                "errgrind.cli.commands.popup_drill_answer",
                return_value=answer,
            ) as answer_popup,
            patch("errgrind.cli.commands.popup_content") as result_popup,
            patch("errgrind.cli.commands.sysmsg"),
            patch("errgrind.cli.commands.successmsg"),
            patch("errgrind.cli.commands.errmsg") as error_message,
        ):
            _cmd_drill(state, "")
        return answer_popup, result_popup, error_message

    def test_correct_answer_uses_spec_draft_then_judge_without_writing(self):
        llm = _FakeLLM([
            self._valid_spec(),
            self._valid_draft(),
            {"is_correct": True, "feedback": "正确"},
        ])

        answer_popup, result_popup, _ = self._run(llm)

        self.assertEqual(len(llm.prompts), 3)
        self.assertEqual(len(self.db.list_all_errors()), 1)
        answer_popup.assert_called_once_with("新的练习题")
        result_popup.assert_called_once()

    def test_source_leak_terms_ignore_workflow_words_and_math_functions(self):
        terms = _source_leak_terms(
            "Error Pattern: student used sqrt and forgot SOURCE_ONLY_7F31"
        )

        self.assertNotIn("error", terms)
        self.assertNotIn("pattern", terms)
        self.assertNotIn("student", terms)
        self.assertNotIn("sqrt", terms)
        self.assertIn("source_only_7f31", terms)

    def test_wrong_answer_is_saved_as_new_error(self):
        llm = _FakeLLM([
            self._valid_spec(),
            self._valid_draft(),
            {"is_correct": False, "feedback": "请先核对条件"},
        ])

        self._run(llm, answer="直接套公式")

        records = self.db.list_all_errors()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].question, "新的练习题")
        self.assertEqual(records[0].user_thoughts, "直接套公式")
        self.assertEqual(records[0].reference_answer, "先核对条件，再完成计算。")
        self.assertEqual(records[0].status, "pending-grill")

    def test_spec_api_failure_stops_before_draft(self):
        llm = _FakeLLM([], fail_on_call=1)

        answer_popup, _, error_message = self._run(llm)

        self.assertEqual(len(llm.prompts), 1)
        answer_popup.assert_not_called()
        self.assertIn("提炼出题规格失败", error_message.call_args.args[0])

    def test_draft_api_failure_stops_before_answering(self):
        llm = _FakeLLM([self._valid_spec()], fail_on_call=2)

        answer_popup, _, error_message = self._run(llm)

        self.assertEqual(len(llm.prompts), 2)
        answer_popup.assert_not_called()
        self.assertIn("模拟 API 错误", error_message.call_args.args[0])

    def test_judge_api_failure_does_not_write_error(self):
        llm = _FakeLLM(
            [self._valid_spec(), self._valid_draft()],
            fail_on_call=3,
        )

        self._run(llm)

        self.assertEqual(len(llm.prompts), 3)
        self.assertEqual(len(self.db.list_all_errors()), 1)

    def test_invalid_spec_contract_is_retried(self):
        invalid_spec = self._valid_spec()
        del invalid_spec["target_pattern"]["success_signal"]
        llm = _FakeLLM([
            invalid_spec,
            self._valid_spec(),
            self._valid_draft(),
            {"is_correct": True, "feedback": "正确"},
        ])

        self._run(llm)

        self.assertEqual(len(llm.prompts), 4)
        self.assertIn("不符合 DrillSpec 契约", llm.prompts[1])

    def test_source_text_leaking_into_spec_is_retried(self):
        leaky_spec = self._valid_spec()
        leaky_spec["target_pattern"]["mechanism"] = self.source_marker
        llm = _FakeLLM([
            leaky_spec,
            self._valid_spec(),
            self._valid_draft(),
            {"is_correct": True, "feedback": "正确"},
        ])

        self._run(llm)

        self.assertEqual(len(llm.prompts), 4)
        self.assertNotIn(self.source_marker, llm.prompts[2])

    def test_invalid_draft_contract_is_retried_once(self):
        llm = _FakeLLM([
            self._valid_spec(),
            {"question": "缺少参考答案"},
            self._valid_draft(),
            {"is_correct": True, "feedback": "正确"},
        ])

        self._run(llm)

        self.assertEqual(len(llm.prompts), 4)
        self.assertIn("不符合输出契约", llm.prompts[2])

    def test_draft_cannot_see_historical_question(self):
        llm = _FakeLLM([
            self._valid_spec(),
            self._valid_draft(),
            {"is_correct": True, "feedback": "正确"},
        ])

        self._run(llm)

        self.assertIn(self.source_marker, llm.prompts[0])
        self.assertNotIn(self.source_marker, llm.prompts[1])

    def test_unselected_error_content_does_not_enter_draft(self):
        other_marker = "UNSELECTED_ONLY_9A22"
        error_id = self.db.create_error(f"{other_marker}：另一道原题")
        self.db.update_grilling(error_id, "[]", "另一个摘要")
        llm = _FakeLLM([
            self._valid_spec(),
            self._valid_draft(),
            {"is_correct": True, "feedback": "正确"},
        ])

        self._run(llm)

        self.assertIn(other_marker, llm.prompts[0])
        self.assertNotIn(other_marker, llm.prompts[1])
        self.assertNotIn(self.source_marker, llm.prompts[1])

    def test_success_signal_reaches_draft_and_judge(self):
        llm = _FakeLLM([
            self._valid_spec(),
            self._valid_draft(),
            {"is_correct": True, "feedback": "正确"},
        ])

        self._run(llm)

        success_signal = "在使用方法前主动列出并核对适用条件"
        self.assertIn(success_signal, llm.prompts[1])
        self.assertIn(success_signal, llm.prompts[2])

    def test_judge_rejects_string_boolean(self):
        llm = _FakeLLM([
            self._valid_spec(),
            self._valid_draft(),
            {"is_correct": "false", "feedback": "错误"},
        ])

        _, _, error_message = self._run(llm)

        self.assertEqual(len(self.db.list_all_errors()), 1)
        error_message.assert_called_once_with(
            "判分失败: is_correct 必须是 JSON 布尔值"
        )

    def test_judge_rejects_non_text_feedback(self):
        llm = _FakeLLM([
            self._valid_spec(),
            self._valid_draft(),
            {"is_correct": False, "feedback": ["错误"]},
        ])

        _, _, error_message = self._run(llm)

        self.assertEqual(len(self.db.list_all_errors()), 1)
        error_message.assert_called_once_with("判分失败: feedback 必须是文本")



class RecordWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "errgrind.db"))
        self.state = AppState(db=self.db)

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_empty_user_thoughts_are_rejected_until_filled(self):
        with (
            patch(
                "errgrind.cli.commands.popup_input",
                side_effect=["题目", "", "   ", "没有思路", ""],
            ) as popup,
            patch("errgrind.cli.commands.errmsg") as error_message,
            patch("errgrind.cli.commands.successmsg"),
            patch("errgrind.cli.commands.sysmsg"),
        ):
            _cmd_record(self.state, "")

        records = self.db.list_all_errors()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].user_thoughts, "没有思路")
        self.assertIsNone(records[0].reference_answer)
        self.assertEqual(error_message.call_count, 2)
        self.assertEqual(popup.call_count, 5)

    def test_ocr_prefills_review_and_saves_edited_text(self):
        class OcrLLM:
            def ocr_image(self, path, prompt):
                self.path = path
                self.prompt = prompt
                return {
                    "question": "识别题目",
                    "user_thoughts": "识别思路",
                    "reference_answer": "识别答案",
                }

        llm = OcrLLM()
        self.state.llm = llm
        self.state.prompts = PromptManager()
        with (
            patch(
                "errgrind.cli.commands.popup_input",
                side_effect=["校对后的题目", "校对后的思路", "校对后的答案"],
            ) as popup,
            patch("errgrind.cli.commands.successmsg"),
            patch("errgrind.cli.commands.sysmsg"),
        ):
            _cmd_ocr(self.state, "/tmp/problem.png")

        record = self.db.list_all_errors()[0]
        self.assertEqual(record.question, "校对后的题目")
        self.assertEqual(record.user_thoughts, "校对后的思路")
        self.assertEqual(record.reference_answer, "校对后的答案")
        self.assertEqual(llm.path, "/tmp/problem.png")
        self.assertIn("数学错题图片转录器", llm.prompt)
        self.assertEqual(popup.call_args_list[0].kwargs["initial_text"], "识别题目")
        self.assertEqual(popup.call_args_list[1].kwargs["initial_text"], "识别思路")
        self.assertEqual(popup.call_args_list[2].kwargs["initial_text"], "识别答案")

    def test_ocr_cancel_during_review_does_not_write(self):
        class OcrLLM:
            def ocr_image(self, path, prompt):
                return {
                    "question": "识别题目",
                    "user_thoughts": "",
                    "reference_answer": "",
                }

        self.state.llm = OcrLLM()
        self.state.prompts = PromptManager()
        with (
            patch("errgrind.cli.commands.popup_input", return_value=None),
            patch("errgrind.cli.commands.sysmsg"),
        ):
            _cmd_ocr(self.state, "/tmp/problem.png")

        self.assertEqual(self.db.list_all_errors(), [])


class _ConversationLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        return next(self.responses)


class ConversationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "errgrind.db"))

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_ctrl_c_exits_teach_and_marks_error_done(self):
        error_id = self.db.create_error("题目", "思路")
        self.db.update_grilling(error_id, json.dumps([
            {"role": "system", "content": "prompt"},
            {"role": "assistant", "content": "摘要"},
        ]), "摘要")
        llm = _ConversationLLM(["针对性讲解"])
        state = AppState(db=self.db, llm=llm, prompts=PromptManager())

        with (
            patch("errgrind.cli.commands.multiline_input", side_effect=KeyboardInterrupt),
            patch("errgrind.cli.commands.console.print"),
            patch("errgrind.cli.commands.successmsg"),
        ):
            _run_teaching(state, self.db.get_error(error_id))

        record = self.db.get_error(error_id)
        self.assertEqual(record.status, "done")
        self.assertEqual(llm.calls, 1)
        self.assertIn("针对性讲解", record.teach_conversation)

    def test_reentering_teach_continues_the_same_conversation(self):
        error_id = self.db.create_error("题目", "思路")
        self.db.update_grilling(error_id, json.dumps([
            {"role": "system", "content": "grill prompt"},
            {"role": "assistant", "content": "摘要"},
        ]), "摘要")
        original_teach = [
            {"role": "system", "content": "原 teach prompt"},
            {"role": "user", "content": "请开始讲解"},
            {"role": "assistant", "content": "第一次讲解"},
        ]
        self.db.update_teach(error_id, json.dumps(original_teach, ensure_ascii=False))
        llm = _ConversationLLM(["继续解答"])
        state = AppState(db=self.db, llm=llm, prompts=PromptManager())

        with (
            patch(
                "errgrind.cli.commands.multiline_input",
                side_effect=["我还有一个问题", KeyboardInterrupt],
            ),
            patch("errgrind.cli.commands._show_context_recap"),
            patch("errgrind.cli.commands.console.print"),
            patch("errgrind.cli.commands.successmsg"),
        ):
            _run_teaching(state, self.db.get_error(error_id))

        messages = json.loads(self.db.get_error(error_id).teach_conversation)
        self.assertEqual(messages[0]["content"], "原 teach prompt")
        self.assertIn({"role": "assistant", "content": "第一次讲解"}, messages)
        self.assertIn({"role": "user", "content": "我还有一个问题"}, messages)
        self.assertIn({"role": "assistant", "content": "继续解答"}, messages)
        self.assertEqual(llm.calls, 1)

    def test_completed_grilling_g_only_opens_read_only_record(self):
        error_id = self.db.create_error("题目", "思路")
        conversation = json.dumps([
            {"role": "system", "content": "prompt"},
            {"role": "assistant", "content": "发现的模式"},
        ], ensure_ascii=False)
        self.db.update_grilling(error_id, conversation, "发现的模式")
        state = AppState(
            db=self.db,
            llm=_ConversationLLM([]),
            prompts=PromptManager(),
        )

        with patch("errgrind.cli.commands._show_grilling_record") as show_record:
            _run_grilling(state, self.db.get_error(error_id))

        record = self.db.get_error(error_id)
        show_record.assert_called_once()
        self.assertEqual(record.status, "pending-teach")
        self.assertEqual(record.grilling_conversation, conversation)
        self.assertEqual(record.grilling_summary, "发现的模式")

    def test_completed_grilling_can_start_teach_immediately(self):
        error_id = self.db.create_error("题目", "思路")
        state = AppState(
            db=self.db,
            llm=_ConversationLLM([]),
            prompts=PromptManager(),
            cfg={"grill_max_turns": 1},
        )

        with (
            patch("errgrind.cli.commands._llm_chat", return_value="发现的模式\n[GRILLING_END]"),
            patch("errgrind.cli.commands.popup_confirm", return_value=True),
            patch("errgrind.cli.commands._run_teaching") as run_teaching,
            patch("errgrind.cli.commands.console.print"),
            patch("errgrind.cli.commands.successmsg"),
        ):
            _run_grilling(state, self.db.get_error(error_id))

        record = self.db.get_error(error_id)
        self.assertEqual(record.status, "pending-teach")
        self.assertEqual(record.grilling_summary, "发现的模式")
        run_teaching.assert_called_once()
        self.assertEqual(run_teaching.call_args.args[1].status, "pending-teach")

    def test_grilling_turn_limit_saves_pending_conversation(self):
        error_id = self.db.create_error("题目", "思路")
        state = AppState(
            db=self.db,
            llm=_ConversationLLM([]),
            prompts=PromptManager(),
            cfg={"grill_max_turns": 1},
        )

        with (
            patch("errgrind.cli.commands._llm_chat", side_effect=["首个问题", "继续追问"]),
            patch("errgrind.cli.commands.multiline_input", return_value="学生回答"),
            patch("errgrind.cli.commands.console.print"),
            patch("errgrind.cli.commands.sysmsg") as system_message,
        ):
            _run_grilling(state, self.db.get_error(error_id))

        record = self.db.get_error(error_id)
        self.assertEqual(record.status, "pending-grill")
        self.assertIn("继续追问", record.grilling_conversation)
        system_message.assert_called_with("已达到最大对话轮数，对话已保存")

    def test_interrupted_grilling_can_continue_to_completion(self):
        error_id = self.db.create_error("题目", "思路")
        state = AppState(
            db=self.db,
            llm=_ConversationLLM([]),
            prompts=PromptManager(),
            cfg={"grill_max_turns": 1},
        )

        with (
            patch("errgrind.cli.commands._llm_chat", return_value="先说说你的判断"),
            patch("errgrind.cli.commands.multiline_input", side_effect=KeyboardInterrupt),
            patch("errgrind.cli.commands.console.print"),
            patch("errgrind.cli.commands.sysmsg"),
        ):
            _run_grilling(state, self.db.get_error(error_id))

        interrupted = self.db.get_error(error_id)
        self.assertEqual(interrupted.status, "pending-grill")
        self.assertIn("先说说你的判断", interrupted.grilling_conversation)

        state.accessed_error_ids.clear()
        with (
            patch(
                "errgrind.cli.commands._llm_chat",
                return_value="忽略了边界条件\n[GRILLING_END]",
            ),
            patch("errgrind.cli.commands.multiline_input", return_value="我直接套了公式"),
            patch("errgrind.cli.commands.popup_confirm", return_value=False),
            patch("errgrind.cli.commands._show_context_recap"),
            patch("errgrind.cli.commands.console.print"),
            patch("errgrind.cli.commands.successmsg"),
        ):
            _run_grilling(state, interrupted)

        completed = self.db.get_error(error_id)
        self.assertEqual(completed.status, "pending-teach")
        self.assertEqual(completed.grilling_summary, "忽略了边界条件")
        self.assertIn("我直接套了公式", completed.grilling_conversation)

    def test_grilling_interrupted_during_ai_response_can_retry(self):
        error_id = self.db.create_error("题目", "思路")
        state = AppState(
            db=self.db,
            llm=_ConversationLLM([]),
            prompts=PromptManager(),
            cfg={"grill_max_turns": 1},
        )

        with (
            patch("errgrind.cli.commands._llm_chat", side_effect=KeyboardInterrupt),
            patch("errgrind.cli.commands.sysmsg"),
        ):
            _run_grilling(state, self.db.get_error(error_id))

        interrupted = self.db.get_error(error_id)
        self.assertEqual(interrupted.status, "pending-grill")
        self.assertEqual(json.loads(interrupted.grilling_conversation)[-1]["content"], "开始吧")

        with (
            patch(
                "errgrind.cli.commands._llm_chat",
                return_value="忽略了定义域\n[GRILLING_END]",
            ),
            patch("errgrind.cli.commands.popup_confirm", return_value=False),
            patch("errgrind.cli.commands._show_context_recap"),
            patch("errgrind.cli.commands.console.print"),
            patch("errgrind.cli.commands.successmsg"),
        ):
            _run_grilling(state, interrupted)

        completed = self.db.get_error(error_id)
        self.assertEqual(completed.status, "pending-teach")
        self.assertEqual(completed.grilling_summary, "忽略了定义域")


if __name__ == "__main__":
    unittest.main()
