import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from errgrind.llm.client import LLMClient
from errgrind.llm.gemini import GeminiClient
from errgrind.llm.ocr import OcrError, load_image, parse_ocr_result


class ImageValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detects_content_instead_of_trusting_suffix(self):
        path = self.root / "photo.txt"
        data = b"\x89PNG\r\n\x1a\nfixture"
        path.write_bytes(data)

        payload = load_image(str(path))

        self.assertEqual(payload.mime_type, "image/png")
        self.assertEqual(payload.data, data)
        self.assertTrue(payload.path.endswith("photo.txt"))

    def test_rejects_missing_directory_unsupported_and_oversized(self):
        unsupported = self.root / "notes.txt"
        unsupported.write_text("not an image", encoding="utf-8")
        oversized = self.root / "large.png"
        oversized.write_bytes(b"\x89PNG\r\n\x1a\nlarge")

        with self.assertRaisesRegex(OcrError, "不存在"):
            load_image(str(self.root / "missing.png"))
        with self.assertRaisesRegex(OcrError, "不是文件"):
            load_image(str(self.root))
        with self.assertRaisesRegex(OcrError, "仅支持"):
            load_image(str(unsupported))
        with self.assertRaisesRegex(OcrError, "图片过大"):
            load_image(str(oversized), max_bytes=4)

    def test_ocr_contract_requires_a_question_and_text_fields(self):
        self.assertEqual(
            parse_ocr_result(
                '{"question":"题目","user_thoughts":"","reference_answer":""}'
            )["question"],
            "题目",
        )
        with self.assertRaisesRegex(OcrError, "没有从图片中识别出题目"):
            parse_ocr_result({"question": "", "user_thoughts": "", "reference_answer": ""})
        with self.assertRaisesRegex(OcrError, "必须是文本"):
            parse_ocr_result({"question": "题目", "user_thoughts": [], "reference_answer": ""})


class ProviderOcrTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.image_path = Path(self.temp_dir.name) / "problem.png"
        self.image_data = b"\x89PNG\r\n\x1a\nfixture"
        self.image_path.write_bytes(self.image_data)
        self.result = {
            "question": "求 $x$",
            "user_thoughts": "直接除以二",
            "reference_answer": "$x=2$",
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_gemini_sends_inline_image_and_schema(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": json.dumps(self.result, ensure_ascii=False)}]}}
            ]
        }
        client = GeminiClient(api_key="test-key", model="test-model", max_retries=1)

        with patch("errgrind.llm.gemini.httpx.post", return_value=response) as post:
            actual = client.ocr_image(str(self.image_path), "OCR prompt")

        self.assertEqual(actual, self.result)
        body = post.call_args.kwargs["json"]
        image_part = body["contents"][0]["parts"][1]["inline_data"]
        self.assertEqual(image_part["mime_type"], "image/png")
        self.assertEqual(base64.b64decode(image_part["data"]), self.image_data)
        self.assertEqual(body["system_instruction"]["parts"][0]["text"], "OCR prompt")
        self.assertIn("response_schema", body["generationConfig"])

    def test_openai_compatible_sends_data_url(self):
        client = LLMClient(api_key="test-key")
        with patch.object(client, "chat_json", return_value=self.result) as chat_json:
            actual = client.ocr_image(str(self.image_path), "OCR prompt")

        self.assertEqual(actual, self.result)
        messages = chat_json.call_args.args[0]
        self.assertEqual(messages[0], {"role": "system", "content": "OCR prompt"})
        image_url = messages[1]["content"][1]["image_url"]["url"]
        self.assertTrue(image_url.startswith("data:image/png;base64,"))
