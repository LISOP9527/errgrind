"""Headless workflow tests: these must never need a CLI or terminal mock."""

import ast
import json
import tempfile
import unittest
from pathlib import Path

from errgrind.application import (
    DrillStage,
    ErrGrindApplication,
    GrillState,
    InvalidWorkflowState,
    OutputContractError,
    WorkflowModelError,
)
from errgrind.db.ops import Database
from errgrind.application.grill_diagnosis import empty_diagnostic_state


class _Prompts:
    """Keep the application boundary test independent from provider extras."""

    def load(self, name):
        return (Path(__file__).parents[1] / "prompts" / name).read_text(encoding="utf-8")


class _LLM:
    def __init__(self, chat_responses=(), json_responses=()):
        self.chat_responses = iter(chat_responses)
        self.json_responses = iter(json_responses)
        self.prompts = []

    def chat(self, messages):
        self.prompts.append("\n".join(item["content"] for item in messages))
        return next(self.chat_responses)

    def stream_chat(self, messages, on_token):
        response = self.chat(messages)
        on_token(response)
        return response

    def chat_json(self, messages, **_kwargs):
        self.prompts.append("\n".join(item["content"] for item in messages))
        return next(self.json_responses)


class ApplicationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "errgrind.db"))
        self.prompts = _Prompts()

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_grill_bootstrap_and_user_evidence_survive_model_failure(self):
        error_id = self.db.create_error("题目", "错误思路")
        app = ErrGrindApplication(
            self.db, _LLM(["先说说你的判断"]), self.prompts
        )

        started = app.start_or_resume_grill(error_id)
        self.assertEqual(started.state, GrillState.ACTIVE)

        app = ErrGrindApplication(self.db, _LLM(), self.prompts)
        with self.assertRaises(WorkflowModelError):
            app.submit_grill_answer(error_id, "我直接套了公式")

        partial = self.db.get_error(error_id)
        self.assertEqual(partial.status, "pending-grill")
        self.assertEqual(
            json.loads(partial.grilling_conversation)[-1],
            {"role": "user", "content": "我直接套了公式"},
        )

        tokens = []
        app = ErrGrindApplication(
            self.db, _LLM(["忽略条件\n[GRILLING_END]"]), self.prompts
        )
        result = app.start_or_resume_grill(error_id, on_token=tokens.append)

        self.assertEqual(result.state, GrillState.COMPLETE)
        self.assertEqual(result.summary, "忽略条件")
        self.assertEqual(tokens, ["忽略条件\n[GRILLING_END]"])
        self.assertEqual(self.db.get_error(error_id).status, "pending-teach")

    def test_blank_grill_answers_leave_persisted_state_untouched(self):
        error_id = self.db.create_error("求 x", "我直接套了公式")
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "开始吧"},
            {"role": "assistant", "content": "当时为什么选这个公式？"},
        ]
        self.db.save_grilling_progress(
            error_id,
            json.dumps(messages, ensure_ascii=False),
            json.dumps(empty_diagnostic_state(), ensure_ascii=False),
        )
        before = self.db.get_error(error_id)
        llm = _LLM()
        app = ErrGrindApplication(self.db, llm, self.prompts)
        for answer in ("", "   ", "\t\n", "\u3000"):
            with self.subTest(answer=repr(answer)):
                with self.assertRaisesRegex(
                    InvalidWorkflowState,
                    "Grill 回答不能为空；如果不记得，可以直接输入“不记得”。",
                ):
                    app.submit_grill_answer(error_id, answer)
                self.assertEqual(self.db.get_error(error_id), before)
                self.assertEqual(llm.prompts, [])

    def test_teach_is_guarded_then_can_be_finished_and_resumed(self):
        error_id = self.db.create_error("题目", "思路")
        app = ErrGrindApplication(self.db, _LLM(), self.prompts)
        with self.assertRaises(InvalidWorkflowState):
            app.start_or_resume_teach(error_id)

        self.db.update_grilling(error_id, "[]", "摘要")
        with self.assertRaisesRegex(InvalidWorkflowState, "Teach 尚未开始"):
            app.finish_teach(error_id)

        app = ErrGrindApplication(self.db, _LLM(["首次讲解"]), self.prompts)
        started = app.start_or_resume_teach(error_id)
        self.assertEqual(started.assistant_response, "首次讲解")

        failing_app = ErrGrindApplication(self.db, _LLM(), self.prompts)
        with self.assertRaises(WorkflowModelError):
            failing_app.submit_teach_answer(error_id, "这个问题还没明白")
        partial = self.db.get_error(error_id)
        self.assertEqual(partial.status, "pending-teach")
        self.assertEqual(
            json.loads(partial.teach_conversation)[-1],
            {"role": "user", "content": "这个问题还没明白"},
        )

        app = ErrGrindApplication(
            self.db, _LLM(["恢复后的回答", "继续回答"]), self.prompts
        )
        resumed = app.start_or_resume_teach(error_id)
        self.assertEqual(resumed.assistant_response, "恢复后的回答")
        app.finish_teach(error_id)
        self.assertEqual(self.db.get_error(error_id).status, "done")

        continued = app.submit_teach_answer(error_id, "还有问题")
        self.assertEqual(continued.assistant_response, "继续回答")
        self.assertEqual(self.db.get_error(error_id).status, "done")

    def test_teach_progress_interrupt_keeps_the_submitted_message(self):
        error_id = self.db.create_error("题目", "思路")
        self.db.update_grilling(error_id, "[]", "摘要")
        app = ErrGrindApplication(self.db, _LLM(["首次讲解"]), self.prompts)
        app.start_or_resume_teach(error_id)

        def interrupt_before_model_call():
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            app.submit_teach_answer(
                error_id,
                "刚提交的问题",
                before_model_call=interrupt_before_model_call,
            )

        partial = self.db.get_error(error_id)
        self.assertEqual(partial.status, "pending-teach")
        self.assertEqual(
            json.loads(partial.teach_conversation)[-1],
            {"role": "user", "content": "刚提交的问题"},
        )
        app.finish_teach(error_id)
        self.assertEqual(self.db.get_error(error_id).status, "done")

    def test_drill_preparation_keeps_source_text_out_of_draft(self):
        marker = "SOURCE_ONLY_7F31"
        source = self.db.create_error(marker)
        self.db.update_grilling(source, "[]", "先检查适用条件")
        spec = {
            "source_error_number": 1,
            "target_pattern": {"mechanism": "先检查适用条件", "trigger": "熟悉形式", "failure_behavior": "直接套用", "desired_behavior": "核对条件", "success_signal": "列出条件"},
            "new_problem": {"domain": "概率", "task_type": "calculate", "setting": "受限样本", "task_goal": "计算概率", "essential_trigger": "条件未自动成立", "solution_strategy": "先确定样本空间", "avoid": ["复杂计算"]},
            "difficulty": {"level": 3, "reasoning_depth": 3, "calculation_load": 2},
        }
        llm = _LLM(json_responses=[spec, {"question": "新题", "reference_answer": "新答"}, {"is_correct": False, "feedback": "反馈"}])
        app = ErrGrindApplication(self.db, llm, self.prompts, {"provider": "test", "model": "model"})

        stages = []
        preparation = app.prepare_drill(on_stage=stages.append)
        self.assertEqual(stages, [DrillStage.SPEC, DrillStage.DRAFT])
        self.assertIn(marker, llm.prompts[0])
        self.assertNotIn(marker, llm.prompts[1])
        judgment = app.judge_and_record_drill(preparation, "错误作答")
        self.assertFalse(judgment.is_correct)
        self.assertIsNotNone(judgment.attempt.derived_error_id)

    def test_invalid_judge_contract_does_not_write_attempt_or_derived_error(self):
        source = self.db.create_error("原题")
        self.db.update_grilling(source, "[]", "摘要")
        spec = {
            "source_error_number": 1,
            "target_pattern": {"mechanism": "检查条件", "trigger": "熟悉形式", "failure_behavior": "直接套用", "desired_behavior": "核对条件", "success_signal": "列出条件"},
            "new_problem": {"domain": "概率", "task_type": "calculate", "setting": "受限样本", "task_goal": "计算概率", "essential_trigger": "条件未自动成立", "solution_strategy": "先确定样本空间", "avoid": ["复杂计算"]},
            "difficulty": {"level": 3, "reasoning_depth": 3, "calculation_load": 2},
        }
        app = ErrGrindApplication(
            self.db,
            _LLM(json_responses=[spec, {"question": "新题", "reference_answer": "新答"}, {"is_correct": "false", "feedback": "错误"}]),
            self.prompts,
        )
        preparation = app.prepare_drill()
        with self.assertRaisesRegex(OutputContractError, "JSON 布尔值"):
            app.judge_and_record_drill(preparation, "作答")
        self.assertEqual(self.db.drill_stats()["total"], 0)
        self.assertEqual(len(self.db.list_all_errors()), 1)

    def test_application_modules_do_not_depend_on_cli_or_terminal_packages(self):
        root = Path(__file__).parents[1] / "errgrind" / "application"
        forbidden = {"rich", "prompt_toolkit", "errgrind.cli"}
        for path in root.glob("*.py"):
            with self.subTest(path=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    module = ""
                    if isinstance(node, ast.Import):
                        names = [item.name for item in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    else:
                        continue
                    for module in names:
                        self.assertFalse(any(module == item or module.startswith(item + ".") for item in forbidden), module)

    def test_public_error_reads_hide_persisted_diagnostic_state(self):
        error_id = self.db.create_error("题目", "思路")
        diagnostic_state = empty_diagnostic_state()
        diagnostic_state.update(
            {
                "diagnosis_status": "supported",
                "hypotheses": [
                    {"id": "H1", "claim": "机制一", "status": "supported"},
                    {"id": "H2", "claim": "机制二", "status": "weakened"},
                ],
                "best_hypothesis_id": "H1",
                "what_would_change_judgment": "相反的用户回忆",
                "evidence": [
                    {
                        "id": "E1",
                        "source_ref": "initial_user_thoughts",
                        "quote": "思路",
                        "interpretation": "原始思路提供支持",
                        "supports": ["H1"],
                        "contradicts": [],
                        "probe_id": "",
                    }
                ],
                "probes": [
                    {
                        "id": "P1",
                        "type": "variant_problem",
                        "question": "变式题",
                        "target_hypothesis_ids": ["H1", "H2"],
                        "discrimination_goal": "区分机制",
                        "predictions": [
                            {
                                "hypothesis_id": "H1",
                                "expected_observation": "隐藏预测",
                            },
                            {
                                "hypothesis_id": "H2",
                                "expected_observation": "另一个隐藏预测",
                            },
                        ],
                        "answer_key": "隐藏答案",
                        "preserved_mechanism": "触发机制",
                        "surface_change": "表面变化",
                    }
                ],
            }
        )
        raw_state = json.dumps(diagnostic_state, ensure_ascii=False)
        self.db.save_grilling_progress(error_id, "[]", raw_state)

        db_record = self.db.get_error(error_id)
        self.assertEqual(db_record.grilling_diagnostic_state, raw_state)
        self.assertIn("隐藏答案", db_record.grilling_diagnostic_state)

        app = ErrGrindApplication(self.db, _LLM(), self.prompts)
        public_record = app.get_error(error_id)
        self.assertIsNone(public_record.grilling_diagnostic_state)
        public_records = app.list_errors()
        self.assertEqual(len(public_records), 1)
        self.assertIsNone(public_records[0].grilling_diagnostic_state)
