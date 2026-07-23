import unittest

from errgrind.cli.commands import _grilling_summary, _is_grilling_complete
from errgrind.llm.client import LLMClient, LLMError
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
            "drill.md": {"summary_list": "s"},
            "judge.md": {"question": "q", "reference_answer": "a", "user_response": "r"},
        }

        for name, substitutions in values.items():
            with self.subTest(name=name):
                prompts.load(name).format(**substitutions)


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


if __name__ == "__main__":
    unittest.main()
