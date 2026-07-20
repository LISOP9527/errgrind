import sys

import httpx
from rich.panel import Panel

from ..config import load as load_config, save as save_config
from ..db.ops import Database
from ..llm.client import DEEPSEEK_BASE_URL, GO_BASE_URL, LLMClient, LLMError
from ..llm.gemini import GeminiClient, GeminiError
from ..llm.prompts import PromptManager
from .commands import COMMANDS, COMMAND_DESCRIPTIONS
from .state import AppState
from .ui import console, user_input, prompt_line, init_completer, sysmsg, errmsg


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

PROVIDERS = {
    "gemini": ("Gemini（谷歌）", GeminiClient, GeminiError),
    "deepseek": ("DeepSeek", LLMClient, LLMError),
    "opencode": ("OpenCode Go（订阅）", LLMClient, LLMError),
}

OLD_ZEN_URL = "https://opencode.ai/zen/v1"


def _fetch_gemini_models(api_key):
    try:
        resp = httpx.get(f"{GEMINI_BASE}/models?key={api_key}", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return [m["name"].replace("models/", "") for m in data.get("models", [])]
    except Exception:
        return []


def _fetch_llm_models(base_url, api_key):
    try:
        resp = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return [m["id"] for m in data.get("data", [])]
    except Exception:
        return []


def _select_model(cfg):
    console.print("[dim]正在获取可用模型...[/dim]")
    provider = cfg["provider"]
    models = []
    default_model = ""
    if provider == "gemini":
        models = _fetch_gemini_models(cfg["api_key"])
        default_model = "gemini-3.5-flash"
    elif provider == "deepseek":
        models = _fetch_llm_models(DEEPSEEK_BASE_URL, cfg["api_key"])
        default_model = "deepseek-chat"
    elif provider == "opencode":
        if "base_url" not in cfg:
            cfg["base_url"] = GO_BASE_URL
        models = _fetch_llm_models(cfg["base_url"], cfg["api_key"])
        default_model = "deepseek-v4-flash"

    if models:
        console.print("[bold]选择模型[/bold]")
        for i, m in enumerate(models):
            console.print(f"  {i+1}. {m}")
        mc = user_input(f"请输入 1-{len(models)}，回车默认 1", default="1")
        cfg["model"] = models[int(mc) - 1]
    else:
        cfg["model"] = user_input("无法获取模型列表，请手动输入模型 ID", default=default_model)


def _change_provider(cfg):
    provider_keys = list(PROVIDERS.keys())
    console.print("[bold]选择 AI 提供商[/bold]")
    for i, k in enumerate(provider_keys):
        console.print(f"  {i+1}. {PROVIDERS[k][0]}")
    choice = user_input(f"请输入 1-{len(provider_keys)}，回车默认 1", default="1")
    cfg["provider"] = provider_keys[int(choice) - 1]
    cfg["api_key"] = user_input("输入 API key")
    if cfg["provider"] == "opencode":
        cfg["base_url"] = user_input("输入 API 地址", default=GO_BASE_URL)
    _select_model(cfg)


def _make_llm(cfg):
    provider = cfg["provider"]
    info = PROVIDERS[provider]
    api_key = cfg["api_key"]

    if provider == "gemini":
        return info[1](api_key=api_key, model=cfg.get("model", "gemini-3.5-flash"))
    if provider == "opencode":
        return info[1](
            api_key=api_key,
            base_url=cfg.get("base_url", GO_BASE_URL),
            model=cfg.get("model", "deepseek-v4-flash"),
        )
    return info[1](
        api_key=api_key,
        base_url=DEEPSEEK_BASE_URL,
        model=cfg.get("model", "deepseek-chat"),
    )


def _ensure_config():
    cfg = load_config()
    if cfg.get("api_key"):
        if cfg.get("provider") == "opencode" and cfg.get("base_url") == OLD_ZEN_URL:
            cfg["base_url"] = GO_BASE_URL
            save_config(cfg)
        return cfg

    try:
        console.print(Panel("[bold]首次使用，请配置 API[/bold]", border_style="blue"))
        _change_provider(cfg)
        save_config(cfg)
        console.print(f"[green]配置已保存到 ~/.config/errgrind/config.json[/green]\n")
    except KeyboardInterrupt:
        console.print("\n[yellow]已取消[/yellow]")
        sys.exit(0)
    return cfg


def _show_welcome():
    console.print(Panel(
        "把一次错误，变成下一次不再犯。\n"
        "输入 [bold]/help[/bold] 查看命令列表。",
        title="[bold]ErrGrind[/bold]",
        title_align="center",
        border_style="blue",
        padding=(1, 2),
        expand=False,
    ))


def _print_separator():
    width = console.width or 80
    width = min(max(width, 20), 80)
    console.print(f"[dim]{'─' * width}[/dim]")


def run_session():
    try:
        cfg = _ensure_config()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]已取消[/yellow]")
        sys.exit(0)

    db = Database()
    llm = _make_llm(cfg)
    prompts = PromptManager()

    state = AppState(db=db, llm=llm, prompts=prompts, cfg=cfg)

    _show_welcome()
    init_completer(COMMAND_DESCRIPTIONS)

    while True:
        _print_separator()
        try:
            line = prompt_line().strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not line:
            continue

        if line.startswith("/"):
            parts = line.split(None, 1)
            cmd = parts[0][1:].lower()
            arg = parts[1] if len(parts) > 1 else ""

            handler = COMMANDS.get(cmd)
            if handler:
                try:
                    handler(state, arg)
                except SystemExit:
                    break
                except (LLMError, GeminiError) as e:
                    errmsg(f"API 错误: {e}")
                except Exception as e:
                    errmsg(f"错误: {e}")
            else:
                sysmsg(f"未知命令: {cmd}，输入 /help 查看命令")
        else:
            sysmsg("输入 / 开头的命令，/help 查看全部")

    db.close()
