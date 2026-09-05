import json
import re
import traceback
import unittest
from unittest.mock import Mock

from errgrind.application import (
    ErrGrindApplication,
    GrillState,
    OutputContractError,
    WorkflowModelError,
)
from errgrind.application.grill_diagnosis import (
    validate_turn_decision,
)
from errgrind.db.ops import Database
from errgrind.llm.client import LLMClient
from errgrind.llm.codex import CodexClient
from errgrind.llm.gemini import GeminiClient
from errgrind.llm.prompts import PromptManager


def _decision(*, action="reasoning_question", evidence=None, probe=None):
    return {
        "new_hypotheses": [
            {"id": "H1", "claim": "忽略了题目中的限制条件"},
            {"id": "H2", "claim": "套用公式前没有核对适用条件"},
        ],
        "hypothesis_status_updates": [],
        "new_evidence": evidence if evidence is not None else [
            {
                "source_ref": "initial_user_thoughts",
                "quote": "我直接套了公式",
                "interpretation": "这是对录题时思路的回忆，仍可能有遗漏。",
                "supports": ["H1"],
                "contradicts": [],
                "probe_id": "",
            }
        ],
        "next_action": action,
        "probe": probe or {
            "question": "你当时为什么认为这一步可以直接使用？",
            "target_hypothesis_ids": ["H1", "H2"],
            "discrimination_goal": "区分限制条件遗漏与适用条件核对缺失。",
            "predictions": [
                {"hypothesis_id": "H1", "expected_observation": "说不出当时看到的限制条件。"},
                {"hypothesis_id": "H2", "expected_observation": "能说出条件但没有核对。"},
            ],
            "answer_key": "",
            "preserved_mechanism": "",
            "surface_change": "",
        },
        "best_hypothesis_id": "",
        "remaining_uncertainty": "还需要区分两个候选机制。",
        "what_would_change_judgment": "用户回忆出当时明确核对过限制条件。",
        "summary": "",
    }


def _undetermined(answer="不记得了"):
    return {
        **_decision(
            action="finish_undetermined",
            evidence=[
                {
                    "source_ref": "message:3",
                    "quote": answer,
                    "interpretation": "回答没有提供能区分主要候选机制的新信息。",
                    "supports": [],
                    "contradicts": [],
                    "probe_id": "P1",
                }
            ],
            probe={
                "question": "",
                "target_hypothesis_ids": [],
                "discrimination_goal": "",
                "predictions": [],
                "answer_key": "",
                "preserved_mechanism": "",
                "surface_change": "",
            },
        ),
        "new_hypotheses": [],
        "hypothesis_status_updates": [],
        "best_hypothesis_id": "",
        "remaining_uncertainty": "主要解释仍无法区分。",
        "summary": "当前 Evidence 还不能可靠区分主要解释。",
    }


def _real_openai_client():
    client = object.__new__(LLMClient)
    client.max_retries = 1
    return client


class GrillReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.prompts = PromptManager()

    def tearDown(self):
        self.db.close()

    def test_real_provider_json_failures_are_safe_and_recoverable(self):
        secret = 'answer_key=TOP_SECRET predictions=["HIDDEN"]'
        providers = []

        openai_client = _real_openai_client()
        openai_client.chat = Mock(side_effect=[secret, secret])
        providers.append(openai_client)

        gemini_client = GeminiClient(api_key="test", max_retries=1)
        gemini_client.chat = Mock(side_effect=[secret, secret])
        providers.append(gemini_client)

        codex_client = object.__new__(CodexClient)
        codex_client.max_retries = 1
        codex_client.chat = Mock(side_effect=[secret, secret])
        providers.append(codex_client)

        for provider in providers:
            with self.subTest(provider=type(provider).__name__):
                error_id = self.db.create_error("求 x", "我直接套了公式", "x=2")
                provider.chat = Mock(side_effect=[json.dumps(_decision()), secret, secret])
                callbacks = []
                app = ErrGrindApplication(self.db, provider, self.prompts)
                app.start_or_resume_grill(error_id)
                old_state = self.db.get_error(error_id).grilling_diagnostic_state
                try:
                    app.submit_grill_answer(error_id, "不记得了", on_token=callbacks.append)
                except OutputContractError as exc:
                    rendered = "".join(traceback.format_exception(exc))
                    for hidden in ("TOP_SECRET", "HIDDEN"):
                        self.assertNotIn(hidden, str(exc))
                        self.assertNotIn(hidden, rendered)
                else:
                    self.fail("两次 malformed output 应暂停 Grill")
                self.assertEqual(provider.chat.call_count, 3)
                self.assertEqual(callbacks, [])
                record = self.db.get_error(error_id)
                self.assertEqual(record.status, "pending-grill")
                self.assertIn("不记得了", record.grilling_conversation)
                self.assertNotIn("TOP_SECRET", record.grilling_conversation)
                self.assertEqual(record.grilling_diagnostic_state, old_state)
                # Resume processes the durable answer, not a repair instruction.
                provider.chat = Mock(return_value=json.dumps(_undetermined()))
                self.assertEqual(app.start_or_resume_grill(error_id).state, GrillState.COMPLETE)
                self.assertEqual(provider.chat.call_count, 1)

    def test_semantic_and_api_failures_redact_model_controlled_text(self):
        secret = "HIDDEN_SEMANTIC_KEY"
        error_id = self.db.create_error("求 x", "我直接套了公式", "x=2")
        invalid = {**_decision(), secret: "unexpected"}
        provider = _real_openai_client()
        provider.chat = Mock(
            side_effect=[json.dumps(invalid, ensure_ascii=False)] * 2
        )
        app = ErrGrindApplication(self.db, provider, self.prompts)
        with self.assertRaises(OutputContractError) as semantic:
            app.start_or_resume_grill(error_id)
        semantic_trace = "".join(traceback.format_exception(semantic.exception))
        self.assertNotIn(secret, str(semantic.exception))
        self.assertNotIn(secret, semantic_trace)

        api_secret = "PRIVATE_API_FAILURE_BODY"
        provider = _real_openai_client()
        provider.chat = Mock(side_effect=RuntimeError(api_secret))
        app = ErrGrindApplication(self.db, provider, self.prompts)
        with self.assertRaises(WorkflowModelError) as api_failure:
            app.start_or_resume_grill(error_id)
        api_trace = "".join(traceback.format_exception(api_failure.exception))
        self.assertNotIn(api_secret, str(api_failure.exception))
        self.assertNotIn(api_secret, api_trace)

    def test_successful_repair_persists_only_visible_assistant_text(self):
        hidden = "HIDDEN_REPAIR_CONTEXT"
        error_id = self.db.create_error("求 x", "我直接套了公式", "x=2")
        invalid = {**_decision(), "unexpected": hidden}
        provider = _real_openai_client()
        provider.chat = Mock(
            side_effect=[
                json.dumps(invalid, ensure_ascii=False),
                json.dumps(_decision(), ensure_ascii=False),
            ]
        )
        visible = []
        app = ErrGrindApplication(self.db, provider, self.prompts)
        result = app.start_or_resume_grill(error_id, on_token=visible.append)
        persisted = self.db.get_error(error_id).grilling_conversation
        self.assertEqual(visible, [result.assistant_response])
        self.assertIn(result.assistant_response, persisted)
        self.assertNotIn(hidden, persisted)
        self.assertNotIn("上次 Grill 输出违反本地契约", persisted)

    def test_teach_facade_hides_private_diagnostic_state(self):
        error_id = self.db.create_error("题目", "我直接套了公式")
        first = _decision(action="variant_problem")
        first["probe"].update(
            answer_key="HIDDEN_TEACH_KEY", preserved_mechanism="保留触发条件",
            surface_change="改变结构",
        )
        llm = Mock()
        llm.chat_json.side_effect = [first, _undetermined()]
        llm.chat.return_value = "新的讲解"
        app = ErrGrindApplication(self.db, llm, self.prompts)
        app.start_or_resume_grill(error_id)
        app.submit_grill_answer(error_id, "不记得了")
        private = self.db.get_error(error_id).grilling_diagnostic_state
        self.assertIn("HIDDEN_TEACH_KEY", private)

        results = [app.start_or_resume_teach(error_id)]
        results.append(app.start_or_resume_teach(error_id))
        results.append(app.submit_teach_answer(error_id, "还有疑问"))
        results.append(app.finish_teach(error_id))
        for result in results:
            self.assertIsNone(result.error.grilling_diagnostic_state)
            self.assertNotIn("HIDDEN_TEACH_KEY", repr(result))
        self.assertEqual(self.db.get_error(error_id).grilling_diagnostic_state, private)

    def test_formatted_prompt_example_is_json_and_first_turn_quotes_initial_thoughts(self):
        prompt = self.prompts.load("grilling.md").format(
            question="求 x", user_thoughts="...", reference_answer="x=2"
        )
        example = re.search(r"```text\n(\{.*?\})\n```", prompt, re.S)
        self.assertIsNotNone(example)
        raw = json.loads(example.group(1))
        validated = validate_turn_decision(
            raw,
            None,
            [{"role": "system", "content": prompt}, {"role": "user", "content": "开始吧"}],
            initial_user_thoughts="...",
        )
        self.assertEqual(validated.new_evidence[0]["source_ref"], "initial_user_thoughts")
        self.assertEqual(validated.new_evidence[0]["quote"], "...")

    def test_nondiscriminating_reply_can_finish_undetermined(self):
        error_id = self.db.create_error("求 x", "我直接套了公式", "x=2")
        llm = Mock()
        llm.chat_json = Mock(side_effect=[_decision(), _undetermined()])
        app = ErrGrindApplication(self.db, llm, self.prompts)
        app.start_or_resume_grill(error_id)
        result = app.submit_grill_answer(error_id, "不记得了")
        self.assertEqual(result.state, GrillState.COMPLETE)
        self.assertIn("当前 Evidence 还不能可靠区分主要解释", result.summary)
        state = json.loads(self.db.get_error(error_id).grilling_diagnostic_state)
        evidence = state["evidence"][-1]
        self.assertEqual(evidence["quote"], "不记得了")
        self.assertEqual(evidence["supports"], [])
        self.assertEqual(evidence["contradicts"], [])
        self.assertEqual(evidence["source_ref"], "message:3")
        self.assertEqual(evidence["probe_id"], "P1")
        self.assertEqual(self.db.get_drill_context(1), [])

    def test_first_turn_without_addressable_evidence_can_use_empty_ledger(self):
        error_id = self.db.create_error("题目")
        llm = Mock()
        llm.chat_json.return_value = _decision(evidence=[])
        app = ErrGrindApplication(self.db, llm, self.prompts)
        self.assertEqual(app.start_or_resume_grill(error_id).state, GrillState.ACTIVE)
        state = json.loads(self.db.get_error(error_id).grilling_diagnostic_state)
        self.assertEqual(state["evidence"], [])
        self.assertEqual(llm.chat_json.call_count, 1)

    def test_legacy_partial_history_is_preserved_but_request_uses_current_protocol(self):
        error_id = self.db.create_error("求 x", "我直接套了公式", "x=2")
        history = [
            {"role": "system", "content": "OLD ACTIVE SYSTEM INSTRUCTION"},
            {"role": "user", "content": "开始吧"},
            {"role": "assistant", "content": "旧问题"},
            {"role": "user", "content": "我还是直接套了公式"},
        ]
        self.db.save_grilling_conversation(error_id, json.dumps(history, ensure_ascii=False))
        provider = _real_openai_client()
        provider.chat = Mock(return_value=json.dumps(_decision(
            evidence=[{
                "source_ref": "message:3",
                "quote": "我还是直接套了公式",
                "interpretation": "保留为当前回答的原话，但仍需继续区分。",
                "supports": ["H1"],
                "contradicts": [],
                "probe_id": "",
            }]
        ), ensure_ascii=False))
        app = ErrGrindApplication(self.db, provider, self.prompts)

        result = app.start_or_resume_grill(error_id)
        self.assertEqual(result.state, GrillState.ACTIVE)
        saved = json.loads(self.db.get_error(error_id).grilling_conversation)
        self.assertEqual(saved[:4], history)
        sent = provider.chat.call_args.args[0]
        self.assertNotIn("OLD ACTIVE SYSTEM INSTRUCTION", json.dumps(sent, ensure_ascii=False))
        self.assertIn("求 x", sent[0]["content"])
        self.assertIn("我直接套了公式", sent[0]["content"])
        self.assertIn('"new_hypotheses"', sent[0]["content"])
        context = sent[-1]["content"]
        self.assertIn("Error 发生当时", sent[0]["content"])
        self.assertIn("message:3", context)
        self.assertEqual(
            json.loads(self.db.get_error(error_id).grilling_diagnostic_state)["evidence"][0]["source_ref"],
            "message:3",
        )


if __name__ == "__main__":
    unittest.main()
