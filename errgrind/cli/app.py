import sys

import httpx
from rich.columns import Columns
from rich.panel import Panel

from ..config import DEFAULT_GEMINI_MODEL, load as load_config, save as save_config
from ..db.ops import Database
from ..llm.client import DEEPSEEK_BASE_URL, GO_BASE_URL, LLMClient, LLMError
from ..llm.gemini import GeminiClient, GeminiError
from ..llm.prompts import PromptManager
from .commands import COMMANDS, COMMAND_DESCRIPTIONS
from .state import AppState
from .ui import console, prompt_line, init_completer, sysmsg, errmsg, select_from_list, popup_input


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
    console.print(" [dim]正在获取可用模型...[/dim]")
    provider = cfg["provider"]
    models = []
    default_model = ""
    if provider == "gemini":
        models = _fetch_gemini_models(cfg["api_key"])
        default_model = DEFAULT_GEMINI_MODEL
    elif provider == "deepseek":
        models = _fetch_llm_models(DEEPSEEK_BASE_URL, cfg["api_key"])
        default_model = "deepseek-chat"
    elif provider == "opencode":
        if "base_url" not in cfg:
            cfg["base_url"] = GO_BASE_URL
        models = _fetch_llm_models(cfg["base_url"], cfg["api_key"])
        default_model = "deepseek-v4-flash"

    if models:
        if default_model in models:
            models.remove(default_model)
            models.insert(0, default_model)
        idx = select_from_list(
            models,
            lambda m: f"{m}（推荐）" if m == default_model else m,
            title="选择 AI 模型",
        )
        if idx is None:
            raise KeyboardInterrupt
        cfg["model"] = models[idx]
    else:
        val = popup_input("选择模型", "无法获取模型列表，请手动输入模型 ID：", multiline=False)
        if val is None:
            raise KeyboardInterrupt
        cfg["model"] = val.strip() or default_model


def _change_provider(cfg):
    provider_keys = list(PROVIDERS.keys())
    provider_names = [PROVIDERS[k][0] for k in provider_keys]
    idx = select_from_list(provider_names, lambda n: n, title="选择 AI 提供商")
    if idx is None:
        raise KeyboardInterrupt
    cfg["provider"] = provider_keys[idx]

    val = popup_input("API Key", "输入 API key：", multiline=False)
    if val is None:
        raise KeyboardInterrupt
    cfg["api_key"] = val.strip()

    if cfg["provider"] == "opencode":
        base = popup_input("API 地址", f"输入 API 地址（默认 {GO_BASE_URL}）：", multiline=False)
        if base is None:
            raise KeyboardInterrupt
        cfg["base_url"] = base.strip() or GO_BASE_URL

    _select_model(cfg)


def _make_llm(cfg):
    provider = cfg["provider"]
    info = PROVIDERS[provider]
    api_key = cfg["api_key"]

    if provider == "gemini":
        return info[1](api_key=api_key, model=cfg.get("model", DEFAULT_GEMINI_MODEL))
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
        console.print(Panel("[bold cyan]✦ 首次使用，请配置 AI 模型接口[/bold cyan]", border_style="cyan"))
        _change_provider(cfg)
        save_config(cfg)
        console.print(f" [bold green]✔ 配置已成功保存至 ~/.config/errgrind/config.json[/bold green]\n")
    except KeyboardInterrupt:
        console.print("\n[yellow]已取消设置[/yellow]")
        sys.exit(0)
    return cfg


def _show_welcome(state: AppState):
    ascii_banner = r"""[bold cyan]
  ______           _____     _            _
 |  ____|         / ____|   (_)          | |
 | |__   _ __ _ _| |  __ ___ _ _ __   __| |
 |  __| | '__| '__| | |_ | '__| | '_ \ / _` |
 | |____| |  | |  | |__| | |  | | | | | (_| |
 |______|_|_ |_|   \_____|_|  |_|_| |_|\__,_|
[/bold cyan]"""

    counts = state.db.count_by_status()
    provider_name = PROVIDERS.get(state.cfg.get("provider", ""), ("未知",))[0]
    model_name = state.cfg.get("model", "默认")

    console.print(ascii_banner)
    console.print(" [bold bright_cyan]✦ 把一次错误，变成下一次不再犯。[/bold bright_cyan]\n")

    engine_panel = Panel(
        f"[bold bright_cyan]{provider_name}[/bold bright_cyan]\n[dim]模型:[/dim] [cyan]{model_name}[/cyan]",
        title="[bold cyan]🧠 AI Engine[/bold cyan]",
        border_style="cyan",
        expand=True,
    )

    stats_panel = Panel(
        f"[yellow]⏳ 待审讯: {counts['pending-grill']}[/yellow]   "
        f"[cyan]📖 待讲解: {counts['pending-teach']}[/cyan]\n"
        f"[green]✅ 已完成: {counts['done']}[/green]   "
        f"[dim]🗂  总计: {counts['total']} 条[/dim]",
        title="[bold cyan]📊 错题库概览[/bold cyan]",
        border_style="cyan",
        expand=True,
    )

    console.print(Columns([engine_panel, stats_panel], equal=True))
    console.print("\n [dim]💡 提示：输入 [bold bright_white]/help[/bold bright_white] 查看命令列表，输入 [bold bright_white]/resume[/bold bright_white] 进入错题工作台[/dim]\n")


def _print_separator():
    width = min(max(console.width or 80, 20), 80)
    console.print(f"[dim cyan]{'─' * width}[/dim cyan]")


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

    _show_welcome(state)
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
                except KeyboardInterrupt:
                    break
                except (LLMError, GeminiError) as e:
                    errmsg(f"API 错误: {e}")
                except Exception as e:
                    errmsg(f"错误: {e}")
            else:
                sysmsg(f"未知命令: {cmd}，输入 /help 查看命令列表")
        else:
            sysmsg("请输入以 / 开头的命令（如 /resume），输入 /help 查看全部")

    db.close()
