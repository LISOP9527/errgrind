import unittest
import importlib
import os
import subprocess
import sys
from unittest.mock import Mock, patch

from errgrind.cli import app


class CliStartupTests(unittest.TestCase):
    def test_web_subcommand_dispatches_to_web_entrypoint(self):
        entrypoint = importlib.import_module("errgrind.main")
        with patch.object(sys, "argv", ["errgrind", "web", "--port", "9876"]), \
             patch("errgrind.web.__main__.main") as web_main:
            entrypoint.main()

        web_main.assert_called_once_with(["--port", "9876"])

    def test_without_web_subcommand_still_starts_cli(self):
        entrypoint = importlib.import_module("errgrind.main")
        with patch.object(sys, "argv", ["errgrind"]), \
             patch("errgrind.cli.app.run_session") as run_session:
            entrypoint.main()

        run_session.assert_called_once_with()

    def test_cli_import_does_not_load_provider_sdks(self):
        code = (
            "import sys; import errgrind.cli.app; "
            "print('openai' in sys.modules, 'openai_codex' in sys.modules)"
        )
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.stdout.strip(), "False False")

    def test_saved_codex_config_does_not_login_or_refresh_at_startup(self):
        cfg = {"provider": "codex", "model": "gpt-5.6-sol"}
        with patch.object(app, "load_config", return_value=cfg), \
             patch.object(app, "_configure_codex_login") as configure:
            result = app._ensure_config()

        self.assertIs(result, cfg)
        configure.assert_not_called()

    def test_codex_client_is_constructed_lazily_without_account_check(self):
        client = Mock()
        codex_client = Mock(return_value=client)
        codex_error = app.PROVIDERS["codex"][2]
        with patch.dict(
            app.PROVIDERS,
            {"codex": ("Codex", codex_client, codex_error)},
        ):
            result = app._make_llm(
                {"provider": "codex", "model": "gpt-5.6-sol", "reasoning_effort": "high"}
            )

        self.assertIs(result, client)
        codex_client.assert_called_once_with(
            model="gpt-5.6-sol", reasoning_effort="high"
        )
        client.account.assert_not_called()

    def test_selecting_codex_explicitly_still_runs_login_flow(self):
        cfg = {"provider": "gemini", "api_key": "old-key", "model": "old-model"}
        with patch.object(app, "select_from_list", return_value=3), \
             patch.object(app, "_configure_codex_login") as configure, \
             patch.object(app, "_select_model") as select_model:
            app._change_provider(cfg)

        configure.assert_called_once_with()
        select_model.assert_called_once()
        selected_cfg = select_model.call_args.args[0]
        self.assertEqual(selected_cfg["provider"], "codex")
        self.assertEqual(selected_cfg["api_key"], "")
        self.assertEqual(cfg["provider"], "codex")


if __name__ == "__main__":
    unittest.main()
