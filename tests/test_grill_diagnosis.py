"""Offline contracts and workflow invariants for structured episode Grill."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from errgrind.application import (
    ErrGrindApplication,
    GrillState,
    OutputContractError,
    WorkflowModelError,
    WorkflowPersistenceError,
)
from errgrind.application.grill_diagnosis import (
    GRILL_TURN_SCHEMA,
    apply_turn_decision,
    empty_diagnostic_state,
    load_diagnostic_state,
    validate_turn_decision,
)
from errgrind.db.ops import Database
from errgrind.cli.commands import _run_grilling
from errgrind.cli.state import AppState
from errgrind.llm.prompts import PromptManager


def _empty_probe():
    return {
        "question": "",
        "target_hypothesis_ids": [],
        "discrimination_goal": "",
        "predictions": [],
        "answer_key": "",
        "preserved_mechanism": "",
        "surface_change": "",
    }


def _probe(question="请说明你当时的判断", *, variant=False):
    return {
        "question": question,
        "target_hypothesis_ids": ["H1", "H2"],
        "discrimination_goal": "区分两个候选机制",
        "predictions": [
            {"hypothesis_id": "H1", "expected_observation": "会描述第一种行为"},
            {"hypothesis_id": "H2", "expected_observation": "会描述第二种行为"},
        ],
        "answer_key": "关键判断" if variant else "",
        "preserved_mechanism": "保留核心 trigger" if variant else "",
        "surface_change": "改变表面结构" if variant else "",
    }


def _first_decision(*, action="reasoning_question"):
    return {
        "new_hypotheses": [
            {"id": "H1", "claim": "候选机制一"},
            {"id": "H2", "claim": "候选机制二"},
        ],
        "hypothesis_status_updates": [],
        "new_evidence": [
            {
                "source_ref": "initial_user_thoughts",
                "quote": "我直接套了公式",
                "interpretation": "原始思路对候选机制提供了可审查线索",
                "supports": ["H1"],
                "contradicts": [],
                "probe_id": "",
            }
        ],
        "next_action": action,
        "probe": _probe(variant=action == "variant_problem")
        if action in {"reasoning_question", "variant_problem"}
        else _empty_probe(),
        "best_hypothesis_id": "",
        "remaining_uncertainty": "还需区分两个机制",
        "what_would_change_judgment": "用户在新问题中的实际行为",
        "summary": "",
    }


def _answer_decision(*, action="finish_supported", source_ref="message:3"):
    return {
        "new_hypotheses": [],
        "hypothesis_status_updates": [
            {"id": "H1", "status": "supported"},
            {"id": "H2", "status": "weakened"},
        ],
        "new_evidence": [
            {
                "source_ref": source_ref,
                "quote": "我还是直接套了公式",
                "interpretation": "这条真实回答区分并支持 H1",
                "supports": ["H1"],
                "contradicts": ["H2"],
                "probe_id": "P1",
            }
        ],
        "next_action": action,
        "probe": _empty_probe(),
        "best_hypothesis_id": "H1" if action == "finish_supported" else "",
        "remaining_uncertainty": "仍可被独立反例改变" if action == "finish_supported" else "回答仍不足以区分主要解释",
        "what_would_change_judgment": "相反行为证据",
        "summary": "当前最受 Evidence 支持的解释是 H1。"
        if action == "finish_supported"
        else "当前 Evidence 还不能可靠区分主要解释。",
    }


class JsonLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def chat_json(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


class GrillDiagnosisContractTests(unittest.TestCase):
    def setUp(self):
        self.messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "开始吧"},
            {"role": "assistant", "content": "问题"},
            {"role": "user", "content": "我还是直接套了公式"},
        ]
        self.state = apply_turn_decision(None, _validated_first())

    def test_schema_is_strict_at_every_object(self):
        def visit(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertFalse(node.get("additionalProperties", True))
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)

        visit(GRILL_TURN_SCHEMA)
        self.assertNotIn("null", json.dumps(GRILL_TURN_SCHEMA))

    def test_grounded_message_quote_is_accepted(self):
        decision = _answer_decision()
        validated = validate_turn_decision(
            decision,
            self.state,
            self.messages,
            initial_user_thoughts="我直接套了公式",
            require_latest_user_evidence=True,
        )
        self.assertEqual(validated.new_evidence[0]["source_ref"], "message:3")

    def test_later_identical_user_answer_is_addressable(self):
        messages = [
            *self.messages,
            {"role": "assistant", "content": "再问一次"},
            {"role": "user", "content": "开始吧"},
        ]
        decision = _answer_decision()
        decision["new_evidence"][0].update(
            {"source_ref": "message:5", "quote": "开始吧"}
        )
        validated = validate_turn_decision(
            decision,
            self.state,
            messages,
            initial_user_thoughts="我直接套了公式",
            require_latest_user_evidence=True,
        )
        self.assertEqual(validated.new_evidence[0]["source_ref"], "message:5")

    def test_fake_sources_and_non_substrings_are_rejected(self):
        cases = [
            ("message:99", "不存在的消息"),
            ("message:2", "问题"),
            ("message:1", "开始吧"),
            ("message:3", "不是原话"),
        ]
        for source_ref, quote in cases:
            with self.subTest(source_ref=source_ref):
                decision = _answer_decision(source_ref=source_ref)
                decision["new_evidence"][0]["quote"] = quote
                with self.assertRaises(OutputContractError):
                    validate_turn_decision(
                        decision,
                        self.state,
                        self.messages,
                        initial_user_thoughts="我直接套了公式",
                        require_latest_user_evidence=True,
                    )

    def test_hypothesis_refs_and_overlap_are_rejected(self):
        for field in ("supports", "contradicts"):
            decision = _answer_decision()
            decision["new_evidence"][0][field] = ["H9"]
            with self.subTest(field=field):
                with self.assertRaises(OutputContractError):
                    validate_turn_decision(decision, self.state, self.messages)

        decision = _answer_decision()
        decision["new_evidence"][0]["supports"] = ["H1"]
        decision["new_evidence"][0]["contradicts"] = ["H1"]
        with self.assertRaises(OutputContractError):
            validate_turn_decision(decision, self.state, self.messages)

    def test_deterministic_merge_preserves_claims_and_rejected_history(self):
        state = apply_turn_decision(None, _validated_first())
        update = _answer_decision(action="finish_undetermined")
        update["hypothesis_status_updates"] = [{"id": "H1", "status": "rejected"}]
        update["new_evidence"] = [
            {
                "source_ref": "message:3",
                "quote": "我还是直接套了公式",
                "interpretation": "这次回答反驳 H1",
                "supports": [],
                "contradicts": ["H1"],
                "probe_id": "P1",
            }
        ]
        update["best_hypothesis_id"] = ""
        validated = validate_turn_decision(update, state, self.messages)
        merged = apply_turn_decision(state, validated)
        self.assertEqual(
            [(item["id"], item["claim"]) for item in merged["hypotheses"]],
            [("H1", "候选机制一"), ("H2", "候选机制二")],
        )
        self.assertEqual(merged["hypotheses"][0]["status"], "rejected")
        self.assertEqual([item["id"] for item in merged["evidence"]], ["E1", "E2"])

        new_h = _answer_decision(action="finish_undetermined")
        new_h["new_hypotheses"] = [{"id": "H3", "claim": "新的候选机制"}]
        new_h["hypothesis_status_updates"] = []
        new_h["new_evidence"] = []
        validated = validate_turn_decision(new_h, state, self.messages)
        merged = apply_turn_decision(state, validated)
        self.assertEqual(merged["hypotheses"][-1]["id"], "H3")

    def test_variant_contract_requires_targets_predictions_and_hidden_fields(self):
        valid = _first_decision(action="variant_problem")
        decision = validate_turn_decision(
            valid,
            None,
            [{"role": "system", "content": "s"}, {"role": "user", "content": "开始吧"}],
            initial_user_thoughts="我直接套了公式",
        )
        self.assertEqual(decision.probe["answer_key"], "关键判断")

        one_target = _first_decision(action="variant_problem")
        one_target["probe"]["target_hypothesis_ids"] = ["H1"]
        one_target["probe"]["predictions"] = [
            {"hypothesis_id": "H1", "expected_observation": "x"}
        ]
        with self.assertRaises(OutputContractError):
            validate_turn_decision(
                one_target,
                None,
                [{"role": "system", "content": "s"}, {"role": "user", "content": "开始吧"}],
                initial_user_thoughts="我直接套了公式",
            )

        missing_prediction = _first_decision(action="variant_problem")
        missing_prediction["probe"]["predictions"].pop()
        with self.assertRaises(OutputContractError):
            validate_turn_decision(
                missing_prediction,
                None,
                [{"role": "system", "content": "s"}, {"role": "user", "content": "开始吧"}],
                initial_user_thoughts="我直接套了公式",
            )

        no_key = _first_decision(action="variant_problem")
        no_key["probe"]["answer_key"] = ""
        with self.assertRaises(OutputContractError):
            validate_turn_decision(
                no_key,
                None,
                [{"role": "system", "content": "s"}, {"role": "user", "content": "开始吧"}],
                initial_user_thoughts="我直接套了公式",
            )

    def test_variant_answer_must_link_the_current_variant_probe(self):
        variant_state = apply_turn_decision(
            None,
            validate_turn_decision(
                _first_decision(action="variant_problem"),
                None,
                [{"role": "system", "content": "s"}, {"role": "user", "content": "开始吧"}],
                initial_user_thoughts="我直接套了公式",
            ),
        )
        decision = _answer_decision()
        decision["new_evidence"][0]["probe_id"] = ""
        with self.assertRaises(OutputContractError):
            validate_turn_decision(
                decision,
                variant_state,
                self.messages,
                initial_user_thoughts="我直接套了公式",
                require_latest_user_evidence=True,
            )

    def test_undetermined_contract_has_no_best_hypothesis(self):
        decision = _answer_decision(action="finish_undetermined")
        decision["new_evidence"] = []
        decision["hypothesis_status_updates"] = []
        validated = validate_turn_decision(decision, self.state, self.messages)
        merged = apply_turn_decision(self.state, validated)
        self.assertEqual(merged["diagnosis_status"], "undetermined")
        self.assertEqual(merged["best_hypothesis_id"], "")
        self.assertEqual(merged["current_probe_id"], "")

    def test_finish_supported_requires_existing_supported_best(self):
        for best, updates in (
            ("", [{"id": "H1", "status": "supported"}]),
            ("H9", [{"id": "H1", "status": "supported"}]),
            ("H1", []),
        ):
            decision = _answer_decision()
            decision["best_hypothesis_id"] = best
            decision["hypothesis_status_updates"] = updates
            with self.subTest(best=best, updates=updates):
                with self.assertRaises(OutputContractError):
                    validate_turn_decision(decision, self.state, self.messages)

    def test_state_null_is_distinct_from_an_empty_persisted_state(self):
        self.assertIsNone(load_diagnostic_state(None))
        empty = empty_diagnostic_state()
        self.assertEqual(load_diagnostic_state(empty), empty)


def _validated_first():
    return validate_turn_decision(
        _first_decision(),
        None,
        [{"role": "system", "content": "s"}, {"role": "user", "content": "开始吧"}],
        initial_user_thoughts="我直接套了公式",
    )


class StructuredGrillWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "errgrind.db"))
        self.prompts = PromptManager()

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def _app(self, responses):
        llm = JsonLLM(responses)
        return llm, ErrGrindApplication(self.db, llm, self.prompts)

    def test_first_turn_persists_state_and_only_shows_question(self):
        error_id = self.db.create_error("题目", "我直接套了公式")
        llm, app = self._app([_first_decision()])
        tokens = []
        result = app.start_or_resume_grill(error_id, on_token=tokens.append)

        self.assertEqual(result.state, GrillState.ACTIVE)
        self.assertEqual(result.assistant_response, "请说明你当时的判断")
        self.assertEqual(tokens, ["请说明你当时的判断"])
        record = self.db.get_error(error_id)
        self.assertIsNone(result.error.grilling_diagnostic_state)
        state = json.loads(record.grilling_diagnostic_state)
        self.assertEqual(state["version"], 1)
        self.assertEqual(state["probes"][0]["id"], "P1")
        self.assertNotIn("关键判断", record.grilling_conversation)
        self.assertNotIn("[Current Diagnostic State]", record.grilling_conversation)
        self.assertEqual(llm.calls[0][1]["output_schema"], GRILL_TURN_SCHEMA)
        self.assertEqual(llm.calls[0][1]["max_json_attempts"], 1)

    def test_user_message_is_saved_before_model_call_and_failure_keeps_state(self):
        error_id = self.db.create_error("题目", "我直接套了公式")
        first_llm, app = self._app([_first_decision()])
        app.start_or_resume_grill(error_id)
        old_state = self.db.get_error(error_id).grilling_diagnostic_state

        class FailingLLM(JsonLLM):
            def chat_json(inner, messages, **kwargs):
                record = self_outer.db.get_error(error_id)
                self_outer.assertEqual(
                    json.loads(record.grilling_conversation)[-1],
                    {"role": "user", "content": "我还是直接套了公式"},
                )
                self_outer.assertEqual(record.grilling_diagnostic_state, old_state)
                raise RuntimeError("网络失败")

        self_outer = self
        failing = FailingLLM([])
        app = ErrGrindApplication(self.db, failing, self.prompts)
        with self.assertRaises(WorkflowModelError):
            app.submit_grill_answer(error_id, "我还是直接套了公式")
        record = self.db.get_error(error_id)
        self.assertEqual(record.grilling_diagnostic_state, old_state)
        self.assertEqual(record.status, "pending-grill")

    def test_variant_answer_is_grill_evidence_not_drill_or_derived_error(self):
        error_id = self.db.create_error("题目", "我直接套了公式")
        llm, app = self._app([
            _first_decision(action="variant_problem"),
            _answer_decision(),
        ])
        first = app.start_or_resume_grill(error_id)
        self.assertEqual(first.assistant_response, "请说明你当时的判断")
        before_errors = len(self.db.list_all_errors())
        before_drill = self.db.drill_stats()["total"]
        completed = app.submit_grill_answer(error_id, "我还是直接套了公式")

        self.assertEqual(completed.state, GrillState.COMPLETE)
        self.assertEqual(self.db.drill_stats()["total"], before_drill)
        self.assertEqual(len(self.db.list_all_errors()), before_errors)
        state = json.loads(self.db.get_error(error_id).grilling_diagnostic_state)
        self.assertEqual(state["diagnosis_status"], "supported")
        self.assertEqual(state["best_hypothesis_id"], "H1")
        self.assertEqual(self.db.get_error(error_id).status, "pending-teach")
        self.assertEqual(state["evidence"][-1]["probe_id"], "P1")
        self.assertEqual(state["evidence"][-1]["source_ref"], "message:3")
        self.assertNotIn("关键判断", completed.assistant_response)
        self.assertNotIn("expected_observation", completed.assistant_response)
        self.assertEqual(len(llm.calls), 2)

        read_only = app.start_or_resume_grill(error_id)
        self.assertEqual(read_only.state, GrillState.READ_ONLY)
        self.assertEqual(read_only.summary, completed.assistant_response)
        self.assertEqual(len(llm.calls), 2)

    def test_finish_undetermined_completes_without_best_hypothesis(self):
        error_id = self.db.create_error("题目", "我直接套了公式")
        llm, app = self._app([
            _first_decision(),
            _answer_decision(action="finish_undetermined"),
        ])
        app.start_or_resume_grill(error_id)
        result = app.submit_grill_answer(error_id, "我还是直接套了公式")

        self.assertEqual(result.state, GrillState.COMPLETE)
        self.assertIn("当前 Evidence 还不能可靠区分主要解释", result.summary)
        record = self.db.get_error(error_id)
        state = json.loads(record.grilling_diagnostic_state)
        self.assertEqual(record.status, "pending-teach")
        self.assertEqual(state["diagnosis_status"], "undetermined")
        self.assertEqual(state["best_hypothesis_id"], "")
        self.assertTrue(state["remaining_uncertainty"])

    def test_invalid_output_repairs_once_and_only_repaired_result_is_saved(self):
        error_id = self.db.create_error("题目", "我直接套了公式")
        invalid = _first_decision()
        invalid["new_evidence"][0]["source_ref"] = "message:99"
        llm, app = self._app([invalid, _first_decision(action="variant_problem")])
        tokens = []
        result = app.start_or_resume_grill(error_id, on_token=tokens.append)

        self.assertEqual(result.assistant_response, "请说明你当时的判断")
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(tokens, ["请说明你当时的判断"])
        self.assertNotIn("message:99", self.db.get_error(error_id).grilling_diagnostic_state)

    def test_malformed_json_gets_one_protocol_repair(self):
        error_id = self.db.create_error("题目", "我直接套了公式")
        llm, app = self._app([ValueError("JSON 解析失败"), _first_decision()])
        result = app.start_or_resume_grill(error_id)

        self.assertEqual(result.assistant_response, "请说明你当时的判断")
        self.assertEqual(len(llm.calls), 2)

    def test_two_invalid_outputs_preserve_previous_state_and_user_answer(self):
        error_id = self.db.create_error("题目", "我直接套了公式")
        llm, app = self._app([_first_decision(action="variant_problem")])
        app.start_or_resume_grill(error_id)
        old_state = self.db.get_error(error_id).grilling_diagnostic_state
        invalid = _answer_decision()
        invalid["new_evidence"][0]["source_ref"] = "message:99"
        llm, app = self._app([invalid, invalid])
        with self.assertRaises(OutputContractError):
            app.submit_grill_answer(error_id, "我还是直接套了公式")
        record = self.db.get_error(error_id)
        self.assertEqual(record.grilling_diagnostic_state, old_state)
        self.assertEqual(json.loads(record.grilling_conversation)[-1]["content"], "我还是直接套了公式")
        self.assertEqual(len(llm.calls), 2)

    def test_persistence_failure_never_returns_unsaved_question(self):
        error_id = self.db.create_error("题目", "我直接套了公式")
        llm, app = self._app([_first_decision(action="variant_problem")])
        original = self.db.save_grilling_progress
        calls = {"count": 0}

        def fail_on_result(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("磁盘写入失败")
            return original(*args, **kwargs)

        with patch.object(self.db, "save_grilling_progress", side_effect=fail_on_result):
            with self.assertRaises(WorkflowPersistenceError):
                app.start_or_resume_grill(error_id)
        record = self.db.get_error(error_id)
        self.assertEqual(record.status, "pending-grill")
        self.assertIsNone(record.grilling_diagnostic_state)
        self.assertNotIn("请说明你当时的判断", record.grilling_conversation)

    def test_old_partial_conversation_builds_state_without_losing_history(self):
        error_id = self.db.create_error("题目", "我直接套了公式")
        history = [
            {"role": "system", "content": "旧 prompt"},
            {"role": "user", "content": "开始吧"},
            {"role": "assistant", "content": "旧问题"},
            {"role": "user", "content": "我还是直接套了公式"},
        ]
        self.db.save_grilling_conversation(error_id, json.dumps(history, ensure_ascii=False))
        first = _first_decision()
        first["new_evidence"][0].update(
            {
                "source_ref": "message:3",
                "quote": "我还是直接套了公式",
            }
        )
        llm, app = self._app([first])
        result = app.start_or_resume_grill(error_id)
        self.assertEqual(result.state, GrillState.ACTIVE)
        messages = json.loads(self.db.get_error(error_id).grilling_conversation)
        self.assertEqual(messages[:4], history)
        self.assertIsNotNone(self.db.get_error(error_id).grilling_diagnostic_state)

    def test_old_completed_grill_stays_read_only_without_backfill(self):
        error_id = self.db.create_error("题目", "思路")
        self.db.update_grilling(
            error_id,
            json.dumps([{"role": "assistant", "content": "旧摘要"}], ensure_ascii=False),
            "旧摘要",
        )
        llm, app = self._app([])
        result = app.start_or_resume_grill(error_id)
        self.assertEqual(result.state, GrillState.READ_ONLY)
        self.assertEqual(result.summary, "旧摘要")
        self.assertIsNone(result.error.grilling_diagnostic_state)
        self.assertEqual(llm.calls, [])

    def test_keyboard_interrupt_keeps_submitted_message_and_old_state(self):
        error_id = self.db.create_error("题目", "我直接套了公式")
        llm, app = self._app([_first_decision()])
        app.start_or_resume_grill(error_id)
        old_state = self.db.get_error(error_id).grilling_diagnostic_state
        llm, app = self._app([KeyboardInterrupt()])
        with self.assertRaises(KeyboardInterrupt):
            app.submit_grill_answer(error_id, "我还是直接套了公式")
        record = self.db.get_error(error_id)
        self.assertEqual(record.status, "pending-grill")
        self.assertEqual(record.grilling_diagnostic_state, old_state)
        self.assertEqual(json.loads(record.grilling_conversation)[-1]["role"], "user")

    def test_db_progress_and_completion_helpers_write_their_groups_atomically(self):
        error_id = self.db.create_error("题目", "思路")
        conversation = json.dumps([{"role": "user", "content": "开始吧"}])
        state = json.dumps(empty_diagnostic_state(), ensure_ascii=False)
        self.db.save_grilling_progress(error_id, conversation, state)
        record = self.db.get_error(error_id)
        self.assertEqual(record.status, "pending-grill")
        self.assertEqual(record.grilling_diagnostic_state, state)

        self.db.complete_grilling(
            error_id,
            conversation,
            "当前最受 Evidence 支持的解释是 H1。",
            state,
        )
        record = self.db.get_error(error_id)
        self.assertEqual(record.status, "pending-teach")
        self.assertEqual(record.grilling_summary, "当前最受 Evidence 支持的解释是 H1。")
        self.assertEqual(record.grilling_diagnostic_state, state)

    def test_v2_database_migrates_nullable_diagnostic_column_without_backfill(self):
        path = Path(self.temp_dir.name) / "v2.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE error_records ("
            "id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending-grill', "
            "origin TEXT NOT NULL DEFAULT 'unknown', source_error_id INTEGER, "
            "source_drill_attempt_id INTEGER, question TEXT NOT NULL, "
            "user_thoughts TEXT, reference_answer TEXT, grilling_conversation TEXT, "
            "grilling_summary TEXT, teach_conversation TEXT, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "updated_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        conn.execute("INSERT INTO error_records (question, grilling_summary, status) VALUES ('旧题', '旧摘要', 'pending-teach')")
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        conn.close()

        migrated = Database(str(path))
        self.addCleanup(migrated.close)
        columns = {
            row[1] for row in migrated.conn.execute("PRAGMA table_info(error_records)")
        }
        self.assertIn("grilling_diagnostic_state", columns)
        self.assertEqual(migrated.get_error(1).grilling_summary, "旧摘要")
        self.assertIsNone(migrated.get_error(1).grilling_diagnostic_state)
        self.assertEqual(
            migrated.conn.execute("PRAGMA user_version").fetchone()[0], 3
        )
    def test_cli_pauses_instead_of_crashing_on_output_contract_error(self):
        error_id = self.db.create_error("题目", "我直接套了公式")
        invalid = _first_decision()
        invalid["new_evidence"][0]["source_ref"] = "message:99"
        state = AppState(
            db=self.db,
            llm=JsonLLM([invalid, invalid]),
            prompts=self.prompts,
            cfg={"grill_max_turns": 1},
        )
        with (
            patch("errgrind.cli.commands.errmsg") as error_message,
            patch("errgrind.cli.commands.console.print"),
        ):
            _run_grilling(state, self.db.get_error(error_id))
        self.assertTrue(error_message.called)
        self.assertEqual(self.db.get_error(error_id).status, "pending-grill")


if __name__ == "__main__":
    unittest.main()
