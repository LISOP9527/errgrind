import json
import os
import shutil


CONFIG_DIR = os.path.expanduser("~/.config/errgrind")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"


def get_database_path(data_dir: str | None = None) -> str:
    if data_dir is None:
        data_dir = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        data_dir = os.path.join(data_dir, "errgrind")
    return os.path.join(data_dir, "errgrind.db")


def prepare_database_path(
    data_dir: str | None = None,
    legacy_path: str | None = None,
) -> str:
    db_path = get_database_path(data_dir)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    if legacy_path is None:
        legacy_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "data", "errgrind.db")
        )
    if not os.path.exists(db_path) and os.path.exists(legacy_path):
        shutil.copy2(legacy_path, db_path)
    return db_path


DEFAULT_CONFIG = {
    "provider": "gemini",
    "model": DEFAULT_GEMINI_MODEL,
    "api_key": "",
    "drill_context_n": 10,
    "grill_max_turns": 30,
}


def load() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def save(cfg: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
