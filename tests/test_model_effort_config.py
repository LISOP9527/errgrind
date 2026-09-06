import unittest
from enum import Enum
from types import SimpleNamespace
from unittest.mock import patch

from errgrind.cli import app
from errgrind.cli import commands


class Effort(Enum):
    LOW = "low"
    HIGH = "high"


class Root:
    def __init__(self, value):
        self.root = value


class NewClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class ModelEffortConfigTests(unittest.TestCase):
    def setUp(self):
        self.metadata_patch = patch.object(app, "_CODEX_MODEL_METADATA", {})
        self.metadata_patch.start()
        self.addCleanup(self.metadata_patch.stop)

    def test_sdk_effort_option_metadata_uses_enum_value_and_root(self):
        entry = SimpleNamespace(
            model="codex-astra",
            is_default=True,
            supported_reasoning_efforts=Root([
                SimpleNamespace(reasoning_effort=Effort.LOW),
                SimpleNamespace(reasoning_effort=Effort.HIGH),
            ]),
            default_reasoning_effort=Root(Effort.LOW),
        )
        client = SimpleNamespace(models=lambda: [entry], close=lambda: None)
        with patch.object(app, "CodexClient", return_value=client):
            self.assertEqual(app._fetch_codex_models(), (["codex-astra"], "codex-astra"))
        self.assertEqual(app._fetch_codex_model_metadata("codex-astra"), {
            "supported_reasoning_efforts": ["low", "high"],
            "default_reasoning_effort": "low",
        })

    def test_model_directory_is_dynamic_and_manual_id_is_available(self):
        cfg = {"provider": "codex", "model": "old"}
        with patch.object(app, "_fetch_codex_models", return_value=(["codex-astra"], "codex-astra")), \
             patch.object(app, "select_from_list", return_value=1), \
             patch.object(app, "popup_input", return_value="codex-manual"), \
             patch.object(app, "_select_codex_effort"):
            app._select_model(cfg)
        self.assertEqual(cfg["model"], "codex-manual")

    def test_model_cancel_keeps_state_and_client(self):
        old = NewClient()
        state = SimpleNamespace(cfg={"provider": "codex", "model": "old"}, llm=old)
        with patch("errgrind.cli.app._select_model", side_effect=KeyboardInterrupt), \
             patch("errgrind.config.save") as save:
            commands._cmd_model(state, "")
        self.assertEqual(state.cfg["model"], "old")
        self.assertIs(state.llm, old)
        save.assert_not_called()

    def test_save_failure_closes_new_client_and_preserves_state(self):
        old = NewClient()
        new = NewClient()
        state = SimpleNamespace(cfg={"provider": "codex", "model": "old"}, llm=old)
        with self.assertRaises(OSError):
            commands._commit_llm_change(
                state, {"provider": "codex", "model": "new"}, new,
                lambda cfg: (_ for _ in ()).throw(OSError("disk")),
            )
        self.assertTrue(new.closed)
        self.assertIs(state.llm, old)
        self.assertEqual(state.cfg["model"], "old")

    def test_effort_default_and_selected_value(self):
        cfg = {"provider": "codex", "model": "m", "reasoning_effort": "high"}
        with patch.object(app, "select_from_list", return_value=0):
            app._select_codex_effort(cfg, {
                "supported_reasoning_efforts": ["low", "high"],
                "default_reasoning_effort": "low",
            })
        self.assertIsNone(cfg["reasoning_effort"])
        with patch.object(app, "select_from_list", return_value=2):
            app._select_codex_effort(cfg, {
                "supported_reasoning_efforts": ["low", "high"],
                "default_reasoning_effort": "low",
            })
        self.assertEqual(cfg["reasoning_effort"], "high")

    def test_effort_command_fetches_catalog_and_replaces_client(self):
        old = NewClient()
        new = NewClient()
        state = SimpleNamespace(cfg={"provider": "codex", "model": "m"}, llm=old)
        with patch.object(commands, "_commit_llm_change") as commit, \
             patch("errgrind.cli.app._fetch_codex_models") as fetch, \
             patch("errgrind.cli.app._select_codex_effort") as select, \
             patch("errgrind.cli.app._make_llm", return_value=new):
            commands._cmd_effort(state, "")
        fetch.assert_called_once_with()
        select.assert_called_once()
        commit.assert_called_once()

    def test_config_has_effort_entry(self):
        self.assertIn("effort", commands.COMMANDS)
        self.assertIn("effort", commands.COMMAND_DESCRIPTIONS)
        old, new = NewClient(), NewClient()
        state = SimpleNamespace(cfg={"provider": "codex", "model": "m"}, llm=old)
        with patch.object(commands, "select_from_list", side_effect=[3, None]), \
             patch.object(app, "_fetch_codex_models"), \
             patch.object(app, "select_from_list", return_value=1), \
             patch.object(app, "popup_input", return_value="ultra"), \
             patch.object(app, "_make_llm", return_value=new) as make, \
             patch("errgrind.config.save") as save:
            commands._cmd_config(state, "")
        self.assertEqual(state.cfg["reasoning_effort"], "ultra")
        self.assertEqual(make.call_args.args[0]["reasoning_effort"], "ultra")
        save.assert_called_once_with(state.cfg)
        self.assertIs(state.llm, new)
        self.assertTrue(old.closed)

    def test_cancel_effort_after_selecting_model_keeps_original_configuration(self):
        old = NewClient()
        cfg = {"provider": "codex", "model": "old", "reasoning_effort": "high"}
        state = SimpleNamespace(cfg=dict(cfg), llm=old)
        with patch.object(app, "_fetch_codex_models", return_value=(["new"], "new")), \
             patch.object(app, "select_from_list", side_effect=[0, None]), \
             patch("errgrind.config.save") as save, \
             patch.object(app, "_make_llm") as make:
            commands._cmd_model(state, "")
        self.assertEqual(state.cfg, cfg)
        self.assertIs(state.llm, old)
        self.assertFalse(old.closed)
        save.assert_not_called()
        make.assert_not_called()


if __name__ == "__main__":
    unittest.main()
