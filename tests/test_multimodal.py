import json
import tempfile
import unittest
from pathlib import Path

from errgrind.application import ErrGrindApplication, WorkflowModelError
from errgrind.db.ops import Database
from errgrind.llm.messages import MultimodalMessage
from errgrind.llm.prompts import PromptManager


PNG = b"\x89PNG\r\n\x1a\nmultimodal-fixture"


def _reasoning_decision():
    return {
        "new_hypotheses": [
            {"id": "H1", "claim": "没有核对适用条件"},
            {"id": "H2", "claim": "把熟悉形式当成了充分条件"},
        ],
        "hypothesis_status_updates": [],
        "new_evidence": [
            {
                "source_ref": "initial_user_thoughts",
                "quote": "思路",
                "interpretation": "这是录题时对过去思路的回忆。",
                "supports": ["H1"],
                "contradicts": [],
                "probe_id": "",
            }
        ],
        "next_action": "reasoning_question",
        "probe": {
            "question": "当时为什么没有核对条件？",
            "target_hypothesis_ids": ["H1", "H2"],
            "discrimination_goal": "区分条件检查和形式熟悉度的作用",
            "predictions": [
                {"hypothesis_id": "H1", "expected_observation": "说不出适用条件"},
                {"hypothesis_id": "H2", "expected_observation": "知道条件但被形式带走"},
            ],
            "answer_key": "",
            "preserved_mechanism": "",
            "surface_change": "",
        },
        "best_hypothesis_id": "",
        "remaining_uncertainty": "还需要区分证据。",
        "what_would_change_judgment": "用户对条件的回忆",
        "summary": "",
    }


def _finish_decision(source_ref):
    return {
        "new_hypotheses": [],
        "hypothesis_status_updates": [
            {"id": "H1", "status": "supported"},
            {"id": "H2", "status": "weakened"},
        ],
        "new_evidence": [
            {
                "source_ref": source_ref,
                "quote": "",
                "interpretation": "用户通过图片提交了当时的作答 artifact；图片本身不是逐字引文。",
                "supports": ["H1"],
                "contradicts": [],
                "probe_id": "P1",
            }
        ],
        "next_action": "finish_supported",
        "probe": {
            "question": "",
            "target_hypothesis_ids": [],
            "discrimination_goal": "",
            "predictions": [],
            "answer_key": "",
            "preserved_mechanism": "",
            "surface_change": "",
        },
        "best_hypothesis_id": "H1",
        "remaining_uncertainty": "",
        "what_would_change_judgment": "新的独立 Error-time Evidence",
        "summary": "当前最受 Evidence 支持的解释是没有核对适用条件。",
    }


class _RecordLLM:
    def __init__(self):
        self.calls = []
        self.transcribe_calls = 0

    def chat_json(self, messages, **_kwargs):
        self.calls.append(messages)
        return {
            "question": "整理后的题目",
            "user_thoughts": "",
            "reference_answer": "",
        }

    def transcribe_image(self, *_args):
        self.transcribe_calls += 1
        raise AssertionError("Web/Core Record must not call transcription")


class _GrillLLM:
    def __init__(self, db, error_id):
        self.db = db
        self.error_id = error_id
        self.fail = False
        self.calls = []

    def chat_json(self, messages, **_kwargs):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("provider failure")
        multimodal = [item for item in messages if isinstance(item, MultimodalMessage)]
        if not multimodal:
            return _reasoning_decision()
        attachment = self.db.list_error_attachments(
            self.error_id, conversation_kind="grill"
        )[-1]
        return _finish_decision(f"message:3:attachment:{attachment.id}")


class _InitialImageGrillLLM:
    def __init__(self):
        self.calls = []

    def chat_json(self, messages, **_kwargs):
        self.calls.append(messages)
        initial_ref = None
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "system":
                continue
            import re
            match = re.search(r"initial_attachment:([1-9][0-9]*)", message.get("content", ""))
            if match:
                initial_ref = f"initial_attachment:{match.group(1)}"
                break
        decision = _reasoning_decision()
        if initial_ref:
            decision["new_evidence"] = [{
                "source_ref": initial_ref,
                "quote": "",
                "interpretation": "用户提供了原始图片 artifact。",
                "supports": ["H1"],
                "contradicts": [],
                "probe_id": "",
            }]
        return decision


class _TeachLLM:
    def __init__(self):
        self.fail = False
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("provider failure")
        return "请先说明这一步的理由。"


class _JudgeLLM:
    def __init__(self):
        self.calls = []

    def chat_json(self, messages, **_kwargs):
        self.calls.append(messages)
        return {"is_correct": False, "feedback": "请展示检查条件的思路。"}


class MultimodalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image = self.root / "user-upload"
        self.image.write_bytes(PNG)
        self.db = Database(str(self.root / "errgrind.db"))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_record_image_uses_one_draft_call_and_persists_bytes_only_after_confirmation(self):
        llm = _RecordLLM()
        app = ErrGrindApplication(self.db, llm, PromptManager())

        draft = app.prepare_record_draft("我记得题目在图片里", [str(self.image)])

        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.transcribe_calls, 0)
        message = llm.calls[0][0]
        self.assertIsInstance(message, MultimodalMessage)
        self.assertEqual(message.images[0].data, PNG)
        self.assertEqual(self.db.list_all_errors(), [])
        self.assertEqual(draft.user_thoughts, "")

        saved = app.record_error(
            draft.question,
            draft.user_thoughts,
            draft.reference_answer,
            image_paths=[str(self.image)],
        )
        attachments = self.db.list_error_attachments(
            saved.id, conversation_kind="initial"
        )
        self.assertEqual([item.data for item in attachments], [PNG])
        self.assertIsNone(self.db.get_error(saved.id).user_thoughts)
        self.db.delete_error(saved.id)
        self.assertEqual(self.db.list_error_attachments(saved.id), [])

    def test_image_only_error_can_claim_pending_record_image_and_reach_grill(self):
        llm = _InitialImageGrillLLM()
        app = ErrGrindApplication(self.db, llm, PromptManager())
        pending_key = "record-image-only"
        app.append_pending_image_attachments(pending_key, [str(self.image)])

        error = app.record_error("", None, pending_key=pending_key)

        self.assertEqual(error.question, "")
        self.assertEqual(self.db.get_error(error.id).display_title, "未命名错题")
        stored = self.db.list_error_attachments(error.id, conversation_kind="initial")
        self.assertEqual([item.data for item in stored], [PNG])
        self.assertEqual(self.db.pending_attachment_count(pending_key), 0)

        app.start_or_resume_grill(error.id)
        multimodal = [item for item in llm.calls[0] if isinstance(item, MultimodalMessage)]
        self.assertEqual(len(multimodal), 1)
        self.assertEqual(multimodal[0].images[0].data, PNG)

    def test_grill_image_turn_is_saved_before_failure_and_resume_reuses_exact_bytes(self):
        error_id = self.db.create_error("题目", "思路")
        llm = _GrillLLM(self.db, error_id)
        app = ErrGrindApplication(self.db, llm, PromptManager())
        app.start_or_resume_grill(error_id)

        llm.fail = True
        with self.assertRaises(WorkflowModelError):
            app.submit_grill_answer(error_id, "", image_paths=[str(self.image)])

        partial = self.db.get_error(error_id)
        messages = json.loads(partial.grilling_conversation)
        attachment_id = messages[-1]["attachments"][0]
        stored = self.db.get_attachments_for_ids([attachment_id], error_id=error_id)[0]
        self.assertEqual(stored.data, PNG)
        self.assertEqual(messages[-1]["content"], "")

        llm.fail = False
        result = app.start_or_resume_grill(error_id)

        self.assertEqual(result.state.value, "complete")
        multimodal = [
            item
            for batch in llm.calls
            for item in batch
            if isinstance(item, MultimodalMessage)
        ]
        self.assertTrue(multimodal)
        self.assertEqual(multimodal[-1].images[0].data, PNG)
        state = json.loads(self.db.get_error(error_id).grilling_diagnostic_state)
        self.assertEqual(state["evidence"][-1]["source_ref"], f"message:3:attachment:{attachment_id}")
        self.assertEqual(state["evidence"][-1]["quote"], "")

    def test_confirmed_record_image_reaches_grill_as_original_multimodal_input(self):
        llm = _InitialImageGrillLLM()
        app = ErrGrindApplication(self.db, llm, PromptManager())
        error = app.record_error(
            "题目",
            "思路",
            image_paths=[str(self.image)],
        )

        app.start_or_resume_grill(error.id)

        multimodal = [
            item
            for item in llm.calls[0]
            if isinstance(item, MultimodalMessage)
        ]
        self.assertEqual(len(multimodal), 1)
        self.assertEqual(multimodal[0].images[0].data, PNG)

    def test_teach_image_turn_is_saved_before_failure_and_resume_reuses_exact_bytes(self):
        error_id = self.db.create_error("题目", "思路")
        self.db.update_grilling(error_id, "[]", "已完成诊断")
        llm = _TeachLLM()
        app = ErrGrindApplication(self.db, llm, PromptManager())
        app.start_or_resume_teach(error_id)

        llm.fail = True
        with self.assertRaises(WorkflowModelError):
            app.submit_teach_answer(error_id, "", image_paths=[str(self.image)])

        messages = json.loads(self.db.get_error(error_id).teach_conversation)
        attachment_id = messages[-1]["attachments"][0]
        self.assertEqual(
            self.db.get_attachments_for_ids([attachment_id], error_id=error_id)[0].data,
            PNG,
        )

        llm.fail = False
        result = app.start_or_resume_teach(error_id)
        self.assertEqual(result.assistant_response, "请先说明这一步的理由。")
        multimodal = [
            item
            for batch in llm.calls
            for item in batch
            if isinstance(item, MultimodalMessage)
        ]
        self.assertTrue(multimodal)
        self.assertEqual(multimodal[-1].images[0].data, PNG)

    def test_drill_judge_image_provenance_reaches_attempt_and_wrong_derived_error(self):
        source_id = self.db.create_error("来源题", "来源思路")
        llm = _JudgeLLM()
        app = ErrGrindApplication(self.db, llm, PromptManager(), {"provider": "fake"})
        from errgrind.application.contracts import DrillPreparation

        preparation = DrillPreparation(
            source_id,
            {
                "target_pattern": {
                    "mechanism": "忽略条件",
                    "success_signal": "先核对条件",
                }
            },
            "新题",
            "参考答案",
        )
        judgment = app.judge_and_record_drill(
            preparation, "", image_paths=[str(self.image)], pending_key="drill-test"
        )

        attempt = self.db.get_drill_attempt(judgment.attempt.attempt_id)
        self.assertEqual(attempt.user_response, "")
        self.assertTrue(attempt.attachment_ids)
        self.assertTrue(
            any(
                isinstance(item, MultimodalMessage)
                and item.images[0].data == PNG
                for item in llm.calls[0]
            )
        )
        derived = self.db.get_error(attempt.derived_error_id)
        self.assertNotIn("[image]", derived.user_thoughts or "")
        self.assertEqual(
            [item.data for item in self.db.list_error_attachments(derived.id, conversation_kind="initial")],
            [PNG],
        )


if __name__ == "__main__":
    unittest.main()
