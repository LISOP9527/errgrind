"""Provider model discovery and the Settings catalog endpoint."""

import json
import unittest
from unittest.mock import Mock, patch

from errgrind.llm.catalog import ModelCatalogError, discover_models
from errgrind.web.app import create_app


class CatalogParsingTests(unittest.TestCase):
    def test_gemini_filters_generation_models_and_keeps_key_out_of_url(self):
        response = Mock()
        response.json.return_value = {
            "models": [
                {
                    "name": "models/gemini-a",
                    "displayName": "Gemini A",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/embed",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        }
        with patch("errgrind.llm.catalog.httpx.get", return_value=response) as get:
            models = discover_models("gemini", api_key="secret-key")

        self.assertEqual([item["id"] for item in models], ["gemini-a"])
        self.assertEqual(models[0]["display_name"], "Gemini A")
        self.assertEqual(get.call_args.kwargs["headers"], {"x-goog-api-key": "secret-key"})
        self.assertEqual(get.call_args.kwargs["params"], {"pageSize": 1000})
        self.assertNotIn("secret-key", get.call_args.args[0])
        self.assertNotIn("secret-key", json.dumps(models))

    def test_openai_compatible_catalog_is_deduplicated(self):
        response = Mock()
        response.json.return_value = {
            "data": [
                {"id": "deepseek-chat", "owned_by": "deepseek"},
                {"id": "deepseek-chat"},
                {"id": "deepseek-reasoner", "display_name": "Reasoner"},
            ]
        }
        with patch("errgrind.llm.catalog.httpx.get", return_value=response) as get:
            models = discover_models(
                "deepseek", api_key="deep-secret",
                base_url="https://attacker.invalid/v1",
            )

        self.assertEqual([item["id"] for item in models], ["deepseek-chat", "deepseek-reasoner"])
        self.assertTrue(models[0]["is_default"])
        self.assertEqual(get.call_args.args[0], "https://api.deepseek.com/models")
        self.assertEqual(
            get.call_args.kwargs["headers"],
            {"Authorization": "Bearer deep-secret"},
        )

        malformed = Mock()
        malformed.json.return_value = {"data": None}
        with patch("errgrind.llm.catalog.httpx.get", return_value=malformed):
            with self.assertRaisesRegex(ModelCatalogError, "返回格式无效"):
                discover_models("deepseek", api_key="deep-secret")

    def test_default_opencode_catalog_only_exposes_supported_protocol(self):
        response = Mock()
        response.json.return_value = {"data": [
            {"id": "deepseek-v4-flash"},
            {"id": "gpt-5.6-luna"},
            {"id": "minimax-m3"},
        ]}
        with patch("errgrind.llm.catalog.httpx.get", return_value=response):
            go_models = discover_models("opencode", api_key="go-secret")
            custom_models = discover_models(
                "opencode", api_key="custom-secret",
                base_url="https://compatible.example/v1",
            )

        self.assertEqual([item["id"] for item in go_models], ["deepseek-v4-flash"])
        self.assertEqual(
            [item["id"] for item in custom_models],
            ["deepseek-v4-flash", "gpt-5.6-luna", "minimax-m3"],
        )

    def test_codex_catalog_preserves_only_public_metadata_and_closes_client(self):
        client = Mock()
        client.models.return_value = [
            {
                "model": "gpt-current",
                "display_name": "Current GPT",
                "is_default": True,
                "supported_reasoning_efforts": ["low", "high"],
                "default_reasoning_effort": "high",
                "base_instructions": "private",
                "model_messages": {"private": True},
            }
        ]
        with patch("errgrind.llm.codex.CodexClient", return_value=client):
            models = discover_models("codex", api_key="ignored")

        self.assertEqual(models, [{
            "id": "gpt-current",
            "display_name": "Current GPT",
            "is_default": True,
            "supported_reasoning_efforts": ["low", "high"],
            "default_reasoning_effort": "high",
        }])
        client.close.assert_called_once_with()

    def test_bad_or_malformed_response_is_safe(self):
        secret = "never-show-this-key"
        response = Mock()
        response.raise_for_status.side_effect = RuntimeError(f"body contains {secret}")
        with patch("errgrind.llm.catalog.httpx.get", return_value=response):
            with self.assertRaisesRegex(ModelCatalogError, "模型目录获取失败") as caught:
                discover_models("gemini", api_key=secret)
        self.assertNotIn(secret, str(caught.exception))

        for payload in ({"models": None}, {"models": "not-a-list"}, {}):
            malformed = Mock()
            malformed.json.return_value = payload
            with self.subTest(payload=payload), \
                    patch("errgrind.llm.catalog.httpx.get", return_value=malformed):
                with self.assertRaisesRegex(ModelCatalogError, "返回格式无效"):
                    discover_models("gemini", api_key="secret")


class CatalogEndpointTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            cfg={
                "provider": "gemini",
                "api_key": "saved-secret",
                "model": "saved-model",
                "base_url": "https://saved.example/v1",
            },
            application=Mock(),
            secret_key="catalog-test",
        )
        self.app.testing = True
        self.client = self.app.test_client()
        self.page = self.client.get("/config")
        with self.client.session_transaction() as session:
            self.csrf = session["csrf"]

    def post(self, data, **headers):
        return self.client.post(
            "/api/assistant/models",
            data=data,
            headers={"X-CSRFToken": self.csrf, **headers},
        )

    def test_legacy_fallback_renders_model_as_select(self):
        self.assertIn(b'<select id="config-model"', self.page.data)
        self.assertNotIn(b'saved-secret', self.page.data)

    def test_reuses_saved_key_without_returning_or_persisting_it(self):
        catalog = [{
            "id": "m", "display_name": "M", "is_default": True,
            "supported_reasoning_efforts": [], "default_reasoning_effort": None,
        }]
        with patch("errgrind.web.app.discover_models", return_value=catalog) as discover:
            first = self.post({"provider": "gemini", "api_key": "", "base_url": ""})
            second = self.post({"provider": "gemini", "api_key": "", "base_url": ""})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(discover.call_args.kwargs["api_key"], "saved-secret")
        self.assertNotIn(b'saved-secret', first.data)

    def test_transient_key_for_new_provider_is_not_retained(self):
        catalog = [{
            "id": "deepseek-chat", "display_name": "deepseek-chat",
            "is_default": True, "supported_reasoning_efforts": [],
            "default_reasoning_effort": None,
        }]
        with patch("errgrind.web.app.discover_models", return_value=catalog) as discover:
            response = self.post({
                "provider": "deepseek", "api_key": "transient-secret", "base_url": "",
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(discover.call_args.kwargs["api_key"], "transient-secret")
        self.assertNotIn(b'transient-secret', response.data)
        retry_without_key = self.post({"provider": "deepseek", "api_key": ""})
        self.assertEqual(retry_without_key.status_code, 400)

    def test_codex_ignores_submitted_credentials_and_returns_effort_metadata(self):
        catalog = [{
            "id": "gpt-current", "display_name": "Current GPT",
            "is_default": True, "supported_reasoning_efforts": ["low", "high"],
            "default_reasoning_effort": "high",
        }]
        with patch("errgrind.web.app.discover_models", return_value=catalog) as discover:
            response = self.post({
                "provider": "codex",
                "api_key": "must-be-ignored",
                "base_url": "https://must-be-ignored.invalid",
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["models"][0]["supported_reasoning_efforts"],
            ["low", "high"],
        )
        self.assertEqual(discover.call_args.kwargs, {"api_key": "", "base_url": ""})
        self.assertNotIn(b'must-be-ignored', response.data)

    def test_csrf_cross_origin_missing_key_and_empty_catalog_are_safe(self):
        missing_csrf = self.client.post(
            "/api/assistant/models", data={"provider": "codex"},
        )
        self.assertEqual(missing_csrf.status_code, 403)

        cross_origin = self.post(
            {"provider": "codex"}, Origin="https://evil.invalid",
        )
        self.assertEqual(cross_origin.status_code, 403)

        missing_key = self.post({"provider": "deepseek"})
        self.assertEqual(missing_key.status_code, 400)

        with patch("errgrind.web.app.discover_models", return_value=[]):
            empty = self.post({"provider": "codex"})
        self.assertEqual(empty.status_code, 502)

    def test_provider_failure_never_echoes_submitted_key(self):
        secret = "submitted-private-key"
        with patch(
            "errgrind.web.app.discover_models",
            side_effect=ModelCatalogError("模型目录获取失败，请检查 API Key 和服务地址。"),
        ):
            response = self.post({"provider": "deepseek", "api_key": secret})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(secret.encode(), response.data)

    def test_saved_opencode_key_is_never_forwarded_to_a_changed_address(self):
        app = create_app(
            cfg={
                "provider": "opencode",
                "api_key": "saved-opencode-secret",
                "model": "m",
                "base_url": "https://saved.example/v1",
            },
            application=Mock(),
            secret_key="opencode-catalog-test",
        )
        app.testing = True
        client = app.test_client()
        client.get("/config")
        with client.session_transaction() as session:
            csrf = session["csrf"]
        with patch("errgrind.web.app.discover_models") as discover:
            rejected = client.post(
                "/api/assistant/models",
                data={
                    "provider": "opencode", "api_key": "",
                    "base_url": "https://changed.example/v1",
                },
                headers={"X-CSRFToken": csrf},
            )
        self.assertEqual(rejected.status_code, 400)
        self.assertNotIn(b'saved-opencode-secret', rejected.data)
        discover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
