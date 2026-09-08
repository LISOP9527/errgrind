import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from errgrind.application.grill import grilling_summary as _grilling_summary
from errgrind.application.grill import is_grilling_complete as _is_grilling_complete
from errgrind.application.grill_diagnosis import GRILL_TURN_SCHEMA
from errgrind.config import prepare_database_path
from errgrind.cli.ui import (
    _format_math_for_terminal,
    render_error_detail,
    render_markdown_to_formatted_text,
    render_markdown_to_plain_text,
    render_terminal_markdown,
)
from errgrind.llm.client import LLMClient, LLMError
from errgrind.llm.gemini import GeminiClient, GeminiError
from errgrind.llm.prompts import PromptManager


class _Delta:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _Completions:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return next(self.responses)


class PromptFormattingTests(unittest.TestCase):
    def test_all_prompt_templates_format(self):
        prompts = PromptManager()
        values = {
            "grilling.md": {"question": "q", "user_thoughts": "t", "reference_answer": "a"},
            "teach.md": {
                "question": "q",
                "user_thoughts": "t",
                "reference_answer": "a",
                "grilling_history": "h",
            },
            "drill_spec.md": {"error_context": "s"},
            "drill.md": {"drill_spec": "s"},
            "judge.md": {
                "question": "q",
                "reference_answer": "a",
                "user_response": "r",
                "target_pattern": "p",
                "success_signal": "s",
            },
            "ocr.md": {},
        }

        for name, substitutions in values.items():
            with self.subTest(name=name):
                template = prompts.load(name)
                if substitutions:
                    template.format(**substitutions)
                else:
                    self.assertTrue(template.strip())

    def test_drill_prompts_preserve_contract_and_diagnostic_semantics(self):
        prompts = PromptManager()
        grill = prompts.load("grilling.md")
        self.assertIn('"new_hypotheses"', grill)
        self.assertIn("`variant_problem`", grill)
        self.assertNotIn("[GRILLING_END]", grill)
        self.assertIn("Error 发生当时的 failure mechanism", grill)
        self.assertIn("retrospective reconstruction", grill)
        self.assertIn("首要目标是诊断，不是 Teach", grill)
        self.assertIn("non-discriminating Evidence", grill)
        self.assertIn('"source_ref": "initial_user_thoughts"', grill)
        self.assertNotIn('"source_ref": "message:3"', grill)
        self.assertIn('"new_evidence": []', grill)
        teach = prompts.load("teach.md")
        self.assertIn("episode-level diagnosis", teach)
        self.assertNotIn("把知识点和 Error Pattern 联系起来", teach)
        draft = prompts.load("drill.md")
        spec = prompts.load("drill_spec.md")

        self.assertIn("{drill_spec}", draft)
        self.assertIn('"question"', draft)
        self.assertIn('"reference_answer"', draft)
        self.assertIn("严格 JSON", draft)
        self.assertIn("不得人为排斥其他数学上正确的解法", draft)
        self.assertIn("中性方式要求学生展示", draft)
        self.assertIn("静默自检", draft)
        for field in ("level", "reasoning_depth", "calculation_load"):
            self.assertIn(f"`{field}`", draft)

        self.assertIn("{error_context}", spec)
        self.assertIn("只选择一个", spec)
        self.assertIn("改变表面情境", spec)
        self.assertIn("功能等价", spec)
        self.assertIn("不绑定固定措辞或唯一解法", spec)
        self.assertIn("`reasoning_depth`", spec)
        self.assertIn("`calculation_load`", spec)

        judge = prompts.load("judge.md")
        self.assertIn("功能等价的思考行为", judge)
        self.assertIn("不能仅因方法不同而判错", judge)
        self.assertIn("思路证据不足", judge)

    def test_grill_cli_uses_neutral_diagnostic_wording(self):
        commands = (Path(__file__).parents[1] / "errgrind" / "cli" / "commands.py").read_text()

        self.assertNotIn("思维审讯", commands)
        self.assertIn("Grill 诊断完成", commands)
        self.assertIn("Grill 诊断对话已保存", commands)


class JsonRetryTests(unittest.TestCase):
    def test_gemini_sends_strict_contract_as_json_schema_on_wire(self):
        client = GeminiClient(api_key="test-key", model="test-model", max_retries=1)
        response = Mock()
        response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]
        }
        # Existing generation settings are preserved without sending both schema dialects.
        settings = {"response_schema": {"type": "OBJECT"}, "temperature": 0.1}
        with patch("errgrind.llm.gemini.httpx.post", return_value=response) as post:
            result = client.chat_json(
                [{"role": "user", "content": "JSON"}],
                output_schema=GRILL_TURN_SCHEMA,
                max_json_attempts=1,
                generation_config=settings,
            )
        self.assertEqual(result, {"ok": True})
        post.assert_called_once()
        body = post.call_args.kwargs["json"]
        config = body["generationConfig"]
        self.assertEqual(config["responseJsonSchema"], GRILL_TURN_SCHEMA)
        self.assertEqual(config["response_mime_type"], "application/json")
        self.assertEqual(config["temperature"], 0.1)
        self.assertNotIn("response_schema", config)
        self.assertNotIn("responseSchema", config)
        self.assertNotIn("max_json_attempts", body)
        self.assertEqual(settings["response_schema"], {"type": "OBJECT"})

    def test_openai_compatible_client_retries_invalid_json(self):
        client = object.__new__(LLMClient)
        client.max_retries = 2
        client.chat = Mock(side_effect=["not json", '{"ok": true}'])

        result = client.chat_json([{"role": "user", "content": "prompt"}])

        self.assertEqual(result, {"ok": True})
        self.assertEqual(client.chat.call_count, 2)
        self.assertEqual(client.chat.call_args.kwargs["temperature"], 0.2)
        retry_messages = client.chat.call_args.args[0]
        self.assertIn("上次响应不是合法 JSON 对象", retry_messages[-1]["content"])

    def test_openai_compatible_client_retries_non_text_response(self):
        client = object.__new__(LLMClient)
        client.max_retries = 2
        client.chat = Mock(side_effect=[None, '{"ok": true}'])

        result = client.chat_json([{"role": "user", "content": "prompt"}])

        self.assertEqual(result, {"ok": True})
        self.assertEqual(client.chat.call_count, 2)
        self.assertEqual(client.chat.call_args.kwargs["temperature"], 0.2)

    def test_gemini_client_retries_invalid_json(self):
        client = GeminiClient(api_key="test-key", model="test-model", max_retries=2)
        client.chat = Mock(side_effect=["[]", '{"ok": true}'])

        result = client.chat_json([{"role": "user", "content": "prompt"}])

        self.assertEqual(result, {"ok": True})
        self.assertEqual(client.chat.call_count, 2)
        self.assertEqual(
            client.chat.call_args.kwargs["generationConfig"]["temperature"],
            0.2,
        )

    def test_gemini_client_retries_non_text_response(self):
        client = GeminiClient(api_key="test-key", model="test-model", max_retries=2)
        client.chat = Mock(side_effect=[None, '{"ok": true}'])

        result = client.chat_json([{"role": "user", "content": "prompt"}])

        self.assertEqual(result, {"ok": True})
        self.assertEqual(client.chat.call_count, 2)
        self.assertEqual(
            client.chat.call_args.kwargs["generationConfig"]["temperature"],
            0.2,
        )


class MarkdownRenderingTests(unittest.TestCase):
    def test_markdown_is_converted_to_prompt_toolkit_fragments(self):
        fragments = render_markdown_to_formatted_text("# 题目\n\n这是 **重点**。")

        self.assertIn(("bold underline", "题"), fragments)
        self.assertIn(("bold", "重"), fragments)
        self.assertNotIn(("", "#"), fragments)

    def test_latex_math_is_made_terminal_readable(self):
        rendered = _format_math_for_terminal(
            r"已知 $\frac{1 - \cos 40°}{\sin 40°} = \tan \theta$，求 $\theta$。"
        )

        self.assertEqual(
            rendered,
            "已知 (1 - cos 40°) / (sin 40°) = tan θ，求 θ。",
        )

    def test_known_teach_formula_renders_without_latex_source(self):
        source = (
            r"**观察目标与已知：** 我们要凑出 $\tan 55^\circ$。"
            r"而 $55^\circ$ 和 $20^\circ$ 相差 $35^\circ$。"
        )

        rendered = render_terminal_markdown(source)
        plain = render_markdown_to_plain_text(source)

        self.assertEqual(
            rendered.markup,
            "**观察目标与已知：** 我们要凑出 tan 55°。而 55° 和 20° 相差 35°。",
        )
        self.assertEqual(
            plain,
            "观察目标与已知： 我们要凑出 tan 55°。而 55° 和 20° 相差 35°。",
        )

    def test_display_math_and_common_symbols_are_readable(self):
        rendered = _format_math_for_terminal(
            r"\[x_{1,2}=\frac{-b\pm\sqrt{b^2-4ac}}{2a}\]"
        )

        self.assertEqual(rendered, "x₁,₂=(-b±√(b²-4ac)) / (2a)")

    def test_markdown_code_is_not_treated_as_math(self):
        source = "`$HOME`\n\n```text\n$5 + $6\n```"
        self.assertEqual(_format_math_for_terminal(source), source)

    def test_error_workspace_detail_uses_the_shared_renderer(self):
        now = datetime(2026, 7, 27, 12, 0)
        error = SimpleNamespace(
            status="pending-teach",
            question="# 题目\n求 " + r"$\tan 55^\circ$",
            user_thoughts="我忽略了 **余角**。",
            reference_answer=r"令 $x_1=35^\circ$。",
            grilling_summary=r"需要先检查 $a \leq b$。",
            grilling_conversation="[]",
            created_at=now,
        )

        fragments = render_error_detail(error, 1)
        text = "".join(fragment[1] for fragment in fragments)

        self.assertIn("tan 55°", text)
        self.assertIn("x₁=35°", text)
        self.assertIn("a ≤ b", text)
        self.assertNotIn("\\tan", text)
        self.assertNotIn("$", text)
        self.assertNotIn("**", text)


class GrillingCompletionTests(unittest.TestCase):
    def test_only_a_final_marker_completes_grilling(self):
        self.assertTrue(_is_grilling_complete("总结\n[GRILLING_END]\n"))
        self.assertFalse(_is_grilling_complete("提及 [GRILLING_END] 但仍在追问"))
        self.assertEqual(_grilling_summary("总结\n[GRILLING_END]"), "总结")


class StreamingTests(unittest.TestCase):
    def _client(self, responses):
        client = object.__new__(LLMClient)
        completions = _Completions(responses)
        client.client = type("OpenAI", (), {
            "chat": type("Chat", (), {"completions": completions})()
        })()
        client.model = "test-model"
        client.provider = "openai_compatible"
        client.max_retries = 2
        return client, completions

    def test_streaming_forwards_tokens(self):
        client, _ = self._client([[_Chunk("你"), _Chunk("好")]])
        received = []

        response = client.stream_chat([], received.append)

        self.assertEqual(response, "你好")
        self.assertEqual(received, ["你", "好"])

    def test_streaming_does_not_retry_after_partial_output(self):
        def interrupted_response():
            yield _Chunk("部分回答")
            raise RuntimeError("connection lost")

        client, completions = self._client([interrupted_response(), [_Chunk("不应重试")]])
        received = []

        with self.assertRaises(LLMError):
            client.stream_chat([], received.append)

        self.assertEqual(received, ["部分回答"])
        self.assertEqual(completions.calls, 1)


class _GeminiStreamResponse:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    def iter_lines(self):
        yield from self.lines


class GeminiStreamingTests(unittest.TestCase):
    def _client(self):
        return GeminiClient(api_key="test-key", model="test-model", max_retries=2)

    def test_streaming_forwards_sse_text_chunks(self):
        first = json.dumps({"candidates": [{"content": {"parts": [{"text": "你"}]}}]})
        second = json.dumps({"candidates": [{"content": {"parts": [{"text": "好"}]}}]})
        metadata = json.dumps({"usageMetadata": {"totalTokenCount": 2}})
        response = _GeminiStreamResponse([
            f"data: {first}", "", f"data: {second}", "", f"data: {metadata}", "",
        ])
        received = []

        with patch("errgrind.llm.gemini.httpx.stream", return_value=response) as stream:
            text = self._client().stream_chat(
                [{"role": "system", "content": "prompt"}, {"role": "user", "content": "hi"}],
                received.append,
            )

        self.assertEqual(text, "你好")
        self.assertEqual(received, ["你", "好"])
        self.assertEqual(stream.call_args.kwargs["params"]["alt"], "sse")
        self.assertEqual(stream.call_args.kwargs["json"]["system_instruction"]["parts"][0]["text"], "prompt")

    def test_streaming_does_not_retry_after_partial_output(self):
        chunk = json.dumps({"candidates": [{"content": {"parts": [{"text": "部分"}]}}]})

        class InterruptedResponse(_GeminiStreamResponse):
            def iter_lines(self):
                yield f"data: {chunk}"
                yield ""
                raise RuntimeError("connection lost")

        received = []
        with patch(
            "errgrind.llm.gemini.httpx.stream",
            return_value=InterruptedResponse([]),
        ) as stream:
            with self.assertRaises(GeminiError):
                self._client().stream_chat([], received.append)

        self.assertEqual(received, ["部分"])
        self.assertEqual(stream.call_count, 1)


class DatabasePathTests(unittest.TestCase):
    def test_legacy_database_is_copied_once_and_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy = root / "legacy.db"
            data_dir = root / "new-data"
            legacy.write_bytes(b"old database")

            target = Path(prepare_database_path(str(data_dir), str(legacy)))

            self.assertEqual(target.read_bytes(), b"old database")
            self.assertTrue(legacy.exists())

            target.write_bytes(b"new database")
            prepare_database_path(str(data_dir), str(legacy))
            self.assertEqual(target.read_bytes(), b"new database")


if __name__ == "__main__":
    unittest.main()
