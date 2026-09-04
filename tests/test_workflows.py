import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from errgrind.application.drill import JUDGE_SCHEMA, source_leak_terms as _source_leak_terms
from errgrind.cli.commands import (
    _cmd_drill,
    _cmd_ocr,
    _cmd_record,
    _run_grilling,
    _run_teaching,
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
        context = self.db.get_drill_context(1)
        self.assertEqual(context[0].question, "求 x")
        self.assertEqual(context[0].grilling_summary, "忽略了条件")

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

        context = self.db.get_drill_context(1)
        self.assertEqual(len(context), 1)
        self.assertEqual(context[0].error_id, second)
        self.assertEqual(context[0].question, "较新题目")
        self.assertEqual(context[0].grilling_summary, "较新摘要")

    def test_legacy_schema_migrates_without_guessing_origin(self):
        path = Path(self.temp_dir.name) / "legacy.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE error_records ("
            "id INTEGER PRIMARY KEY, "
            "status TEXT NOT NULL DEFAULT 'pending-grill', "
            "question TEXT NOT NULL, user_thoughts TEXT, "
            "reference_answer TEXT, grilling_conversation TEXT, "
            "grilling_summary TEXT, teach_conversation TEXT, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        conn.execute("INSERT INTO error_records (question) VALUES ('历史题')")
        conn.execute(
            "CREATE TABLE drill_attempts ("
            "id INTEGER PRIMARY KEY, source_error_id INTEGER NOT NULL, "
            "drill_spec TEXT NOT NULL, question TEXT NOT NULL, "
            "reference_answer TEXT NOT NULL, user_response TEXT NOT NULL, "
            "is_correct INTEGER NOT NULL, feedback TEXT NOT NULL, "
            "derived_error_id INTEGER, created_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        conn.execute(
            "INSERT INTO drill_attempts "
            "(source_error_id, drill_spec, question, reference_answer, "
            "user_response, is_correct, feedback) "
            "VALUES (1, '{}', '题', '答', '作答', 1, '旧记录')"
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()

        migrated = Database(str(path))
        try:
            record = migrated.get_error(1)
            self.assertEqual(record.question, "历史题")
            self.assertEqual(record.origin, "unknown")
            self.assertIsNone(record.source_error_id)
            columns = {
                row[1]
                for row in migrated.conn.execute("PRAGMA table_info(drill_attempts)")
            }
            self.assertIn("source_error_id", columns)
            self.assertIn("derived_error_id", columns)
            attempt = migrated.list_drill_attempts()[0]
            self.assertEqual(attempt.judge_provider, "unknown")
            self.assertEqual(attempt.judge_model, "unknown")
            self.assertEqual(attempt.judge_prompt_sha256, "unknown")
            self.assertEqual(attempt.judge_schema_sha256, "unknown")
            self.assertEqual(
                migrated.conn.execute("PRAGMA user_version").fetchone()[0],
                3,
            )
        finally:
            migrated.close()

    def test_newer_database_version_is_rejected_before_migration(self):
        path = Path(self.temp_dir.name) / "future.db"
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA user_version = 999")
        conn.execute("CREATE TABLE future_only (value TEXT)")
        conn.commit()
        conn.close()

        with self.assertRaisesRegex(RuntimeError, "数据库版本 999"):
            Database(str(path))

        check = sqlite3.connect(path)
        try:
            tables = {
                row[0]
                for row in check.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertEqual(tables, {"future_only"})
            self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 999)
        finally:
            check.close()

    def test_drill_attempt_is_atomic_and_keeps_lineage(self):
        source = self.db.create_error("原题", origin="record")
        self.db.update_grilling(source, "[]", "摘要")
        result = self.db.record_drill_attempt(
            source,
            {"target_pattern": {}},
            "衍生题",
            "参考答案",
            "我的回答",
            False,
            "请检查条件",
        )
        attempt = self.db.list_drill_attempts()[0]
        self.assertEqual(attempt.id, result.attempt_id)
        self.assertEqual(attempt.derived_error_id, result.derived_error_id)
        self.assertEqual(attempt.drill_spec, {"target_pattern": {}})
        self.assertFalse(attempt.is_correct)
        child = self.db.get_error(attempt.derived_error_id)
        self.assertEqual(child.origin, "drill")
        self.assertEqual(child.source_error_id, source)
        self.assertEqual(child.source_drill_attempt_id, attempt.id)

    def test_correct_drill_attempt_has_no_derived_error(self):
        source = self.db.create_error("原题")
        result = self.db.record_drill_attempt(
            source, {}, "题", "答", "作答", True, "正确"
        )
        attempt = self.db.list_drill_attempts()[0]
        self.assertEqual(attempt.id, result.attempt_id)
        self.assertIsNone(result.derived_error_id)
        self.assertIsNone(attempt.derived_error_id)
        self.assertEqual(
            self.db.drill_stats(),
            {"total": 1, "correct": 1, "incorrect": 0},
        )

    def test_drill_attempt_rejects_unstructured_spec(self):
        source = self.db.create_error("原题")

        with self.assertRaisesRegex(ValueError, "drill_spec 必须是 JSON 对象"):
            self.db.record_drill_attempt(
                source, "{}", "题", "答", "作答", True, "正确"
            )

        self.assertEqual(self.db.drill_stats()["total"], 0)

    def test_origin_counts_distinguish_user_and_drill_sources(self):
        record_source = self.db.create_error("手动题")
        self.db.create_error("OCR 题", origin="ocr")
        self.db.create_error("历史题", origin="unknown")
        self.db.record_drill_attempt(
            record_source,
            {},
            "衍生题",
            "答案",
            "错误作答",
            False,
            "反馈",
        )

        self.assertEqual(
            self.db.count_by_origin(),
            {"unknown": 1, "record": 1, "ocr": 1, "drill": 1},
        )

    def test_failed_derived_insert_rolls_back_attempt(self):
        source = self.db.create_error("原题")
        self.db.conn.execute(
            "CREATE TRIGGER reject_drill_error "
            "BEFORE INSERT ON error_records "
            "WHEN NEW.origin = 'drill' "
            "BEGIN SELECT RAISE(ABORT, '模拟衍生 Error 写入失败'); END"
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.record_drill_attempt(
                source,
                {},
                "衍生题",
                "答案",
                "错误作答",
                False,
                "反馈",
            )

        self.assertEqual(self.db.drill_stats()["total"], 0)
        self.assertEqual([record.id for record in self.db.list_all_errors()], [source])

    def test_delete_parent_clears_child_lineage_and_attempt(self):
        source = self.db.create_error("原题")
        result = self.db.record_drill_attempt(
            source,
            {},
            "衍生",
            "答",
            "错",
            False,
            "反馈",
        )
        child = self.db.get_error(result.derived_error_id)
        self.db.delete_error(source)
        detached_child = self.db.get_error(child.id)
        self.assertIsNone(detached_child.source_error_id)
        self.assertIsNone(detached_child.source_drill_attempt_id)
        self.assertEqual(self.db.drill_stats()["total"], 0)

    def test_delete_derived_error_removes_its_attempt(self):
        source = self.db.create_error("原题")
        result = self.db.record_drill_attempt(
            source,
            {},
            "衍生",
            "答",
            "错",
            False,
            "反馈",
        )

        self.db.delete_error(result.derived_error_id)

        self.assertIsNotNone(self.db.get_error(source))
        self.assertIsNone(self.db.get_error(result.derived_error_id))
        self.assertEqual(self.db.drill_stats()["total"], 0)


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
        self.source_error_id = self.db.create_error(f"{self.source_marker}：原题")
        self.db.update_grilling(
            self.source_error_id,
            "[]",
            "忽略适用条件就直接套用方法",
        )

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
            cfg={
                "drill_context_n": 10,
                "provider": "test-provider",
                "model": "test-model",
            },
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

    def test_correct_answer_records_attempt_without_creating_error(self):
        llm = _FakeLLM([
            self._valid_spec(),
            self._valid_draft(),
            {"is_correct": True, "feedback": "正确"},
        ])

        answer_popup, result_popup, _ = self._run(llm)

        self.assertEqual(len(llm.prompts), 3)
        self.assertEqual(len(self.db.list_all_errors()), 1)
        attempts = self.db.list_drill_attempts()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].source_error_id, self.source_error_id)
        self.assertTrue(attempts[0].is_correct)
        self.assertIsNone(attempts[0].derived_error_id)
        self.assertEqual(attempts[0].judge_provider, "test-provider")
        self.assertEqual(attempts[0].judge_model, "test-model")
        judge_template = PromptManager().load("judge.md")
        expected_prompt_digest = hashlib.sha256(
            judge_template.encode("utf-8")
        ).hexdigest()
        canonical_schema = json.dumps(
            JUDGE_SCHEMA,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        expected_schema_digest = hashlib.sha256(
            canonical_schema.encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            attempts[0].judge_prompt_sha256,
            expected_prompt_digest,
        )
        self.assertEqual(
            attempts[0].judge_schema_sha256,
            expected_schema_digest,
        )
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
        self.assertEqual(records[0].origin, "drill")
        self.assertEqual(records[0].source_error_id, self.source_error_id)
        attempts = self.db.list_drill_attempts()
        self.assertEqual(len(attempts), 1)
        self.assertFalse(attempts[0].is_correct)
        self.assertEqual(attempts[0].derived_error_id, records[0].id)
        self.assertEqual(records[0].source_drill_attempt_id, attempts[0].id)

    def test_cancelled_answer_does_not_record_attempt(self):
        llm = _FakeLLM([self._valid_spec(), self._valid_draft()])

        answer_popup, result_popup, _ = self._run(llm, answer=None)

        answer_popup.assert_called_once()
        result_popup.assert_not_called()
        self.assertEqual(len(llm.prompts), 2)
        self.assertEqual(self.db.drill_stats()["total"], 0)

    def test_spec_api_failure_stops_before_draft(self):
        llm = _FakeLLM([], fail_on_call=1)

        answer_popup, _, error_message = self._run(llm)

        self.assertEqual(len(llm.prompts), 1)
        answer_popup.assert_not_called()
        self.assertIn("提炼出题规格失败", error_message.call_args.args[0])
        self.assertEqual(self.db.drill_stats()["total"], 0)

    def test_draft_api_failure_stops_before_answering(self):
        llm = _FakeLLM([self._valid_spec()], fail_on_call=2)

        answer_popup, _, error_message = self._run(llm)

        self.assertEqual(len(llm.prompts), 2)
        answer_popup.assert_not_called()
        self.assertIn("模拟 API 错误", error_message.call_args.args[0])
        self.assertEqual(self.db.drill_stats()["total"], 0)

    def test_judge_api_failure_does_not_write_error(self):
        llm = _FakeLLM(
            [self._valid_spec(), self._valid_draft()],
            fail_on_call=3,
        )

        self._run(llm)

        self.assertEqual(len(llm.prompts), 3)
        self.assertEqual(len(self.db.list_all_errors()), 1)
        self.assertEqual(self.db.drill_stats()["total"], 0)

    def test_database_failure_does_not_claim_result_was_saved(self):
        llm = _FakeLLM([
            self._valid_spec(),
            self._valid_draft(),
            {"is_correct": True, "feedback": "正确"},
        ])

        with patch.object(
            self.db,
            "record_drill_attempt",
            side_effect=RuntimeError("磁盘写入失败"),
        ):
            _, result_popup, error_message = self._run(llm)

        result_popup.assert_not_called()
        error_message.assert_called_once_with("保存演练结果失败: 磁盘写入失败")
        self.assertEqual(self.db.drill_stats()["total"], 0)

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
        other_error_id = self.db.create_error(f"{other_marker}：另一道原题")
        self.db.update_grilling(other_error_id, "[]", "另一个摘要")
        llm = _FakeLLM([
            self._valid_spec(),
            self._valid_draft(),
            {"is_correct": True, "feedback": "正确"},
        ])

        self._run(llm)

        self.assertIn(other_marker, llm.prompts[0])
        self.assertNotIn(other_marker, llm.prompts[1])
        self.assertNotIn(self.source_marker, llm.prompts[1])
        self.assertEqual(
            self.db.list_drill_attempts()[0].source_error_id,
            other_error_id,
        )

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
        self.assertEqual(self.db.drill_stats()["total"], 0)
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
        self.assertEqual(self.db.drill_stats()["total"], 0)
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
        self.assertEqual(records[0].origin, "record")
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
        self.assertEqual(record.origin, "ocr")
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

    def test_state_builds_a_fresh_facade_after_provider_replacement(self):
        first, second = _ConversationLLM([]), _ConversationLLM([])
        state = AppState(db=self.db, llm=first, prompts=PromptManager())

        self.assertIs(state.application().llm, first)
        state.llm = second
        self.assertIs(state.application().llm, second)

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
            llm=_ConversationLLM(["发现的模式\n[GRILLING_END]"]),
            prompts=PromptManager(),
            cfg={"grill_max_turns": 1},
        )

        with (
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
            llm=_ConversationLLM(["首个问题", "继续追问"]),
            prompts=PromptManager(),
            cfg={"grill_max_turns": 1},
        )

        with (
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
            llm=_ConversationLLM(["先说说你的判断"]),
            prompts=PromptManager(),
            cfg={"grill_max_turns": 1},
        )

        with (
            patch("errgrind.cli.commands.multiline_input", side_effect=KeyboardInterrupt),
            patch("errgrind.cli.commands.console.print"),
            patch("errgrind.cli.commands.sysmsg"),
        ):
            _run_grilling(state, self.db.get_error(error_id))

        interrupted = self.db.get_error(error_id)
        self.assertEqual(interrupted.status, "pending-grill")
        self.assertIn("先说说你的判断", interrupted.grilling_conversation)

        state.accessed_error_ids.clear()
        state.llm = _ConversationLLM(["忽略了边界条件\n[GRILLING_END]"])
        with (
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

        with (patch.object(state.llm, "chat", side_effect=KeyboardInterrupt), patch("errgrind.cli.commands.sysmsg")):
            _run_grilling(state, self.db.get_error(error_id))

        interrupted = self.db.get_error(error_id)
        self.assertEqual(interrupted.status, "pending-grill")
        self.assertEqual(json.loads(interrupted.grilling_conversation)[-1]["content"], "开始吧")

        state.llm = _ConversationLLM(["忽略了定义域\n[GRILLING_END]"])
        with (
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
