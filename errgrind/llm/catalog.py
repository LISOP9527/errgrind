"""Provider model catalog discovery.

This module deliberately only returns provider metadata.  It never probes a
model with a generation request and its errors are safe to show to users.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

import httpx

from ..config import DEFAULT_CODEX_MODEL, DEFAULT_GEMINI_MODEL
from .client import DEEPSEEK_BASE_URL, GO_BASE_URL


# OpenCode Go's catalog spans Responses, Anthropic Messages, and OpenAI-
# compatible Chat Completions.  ErrGrind currently implements only the last
# protocol, so the default Go catalog must not advertise models it cannot call.
_OPENCODE_GO_CHAT_PREFIXES = (
    "deepseek-", "glm-", "hy", "kimi-", "longcat-", "mimo-",
)


class ModelCatalogError(Exception):
    """A safe, user-facing model catalog failure."""


@dataclass(frozen=True)
class ModelInfo:
    id: str
    display_name: str
    is_default: bool = False
    supported_reasoning_efforts: tuple[str, ...] = ()
    default_reasoning_effort: str | None = None

    def public(self) -> dict:
        result = asdict(self)
        result["supported_reasoning_efforts"] = list(self.supported_reasoning_efforts)
        return result


def _safe_url(value: str, *, default: str | None = None) -> str:
    value = (value or default or "").strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        valid = valid and not parsed.username and not parsed.password
    except ValueError:
        valid = False
    if not valid:
        raise ModelCatalogError("请输入有效的模型 API 地址（http 或 https）。")
    return value


def _request(
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str | int] | None = None,
) -> dict:
    try:
        response = httpx.get(
            url,
            headers=headers,
            params=params,
            follow_redirects=False,
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        raise ModelCatalogError("模型目录获取失败，请检查 API Key 和服务地址。") from None
    if not isinstance(payload, dict):
        raise ModelCatalogError("模型目录返回格式无效。")
    return payload


def _codex() -> list[ModelInfo]:
    from .codex import CodexClient, CodexError

    client = None
    try:
        client = CodexClient(model=DEFAULT_CODEX_MODEL)
        entries = client.models()
        result = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            model = entry.get("model") or entry.get("id")
            if not isinstance(model, str) or not model:
                continue
            efforts = entry.get("supported_reasoning_efforts") or []
            if isinstance(efforts, str):
                efforts = [efforts]
            clean_efforts = []
            for effort in efforts:
                effort = getattr(effort, "root", effort)
                effort = getattr(effort, "value", effort)
                if isinstance(effort, str) and effort not in clean_efforts:
                    clean_efforts.append(effort)
            default_effort = entry.get("default_reasoning_effort")
            default_effort = getattr(default_effort, "root", default_effort)
            default_effort = getattr(default_effort, "value", default_effort)
            result.append(ModelInfo(
                id=model,
                display_name=str(entry.get("display_name") or model),
                is_default=bool(entry.get("is_default")),
                supported_reasoning_efforts=tuple(clean_efforts),
                default_reasoning_effort=(
                    default_effort if isinstance(default_effort, str) else None
                ),
            ))
        return _dedupe(result)
    except CodexError:
        raise ModelCatalogError("Codex 模型目录获取失败，请先完成 CLI 登录。") from None
    except Exception:
        raise ModelCatalogError("Codex 模型目录获取失败，请先完成 CLI 登录。") from None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _gemini(api_key: str) -> list[ModelInfo]:
    if not api_key:
        raise ModelCatalogError("请输入 Gemini API Key。")
    payload = _request(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": api_key},
        params={"pageSize": 1000},
    )
    result = []
    entries = payload.get("models")
    if not isinstance(entries, list):
        raise ModelCatalogError("模型目录返回格式无效。")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        methods = entry.get("supportedGenerationMethods") or []
        if "generateContent" not in methods:
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        model = name.removeprefix("models/")
        result.append(ModelInfo(
            model,
            str(entry.get("displayName") or model),
            model == DEFAULT_GEMINI_MODEL,
        ))
    return _dedupe(result)


def _compatible(base_url: str, api_key: str, provider: str) -> list[ModelInfo]:
    if not api_key:
        raise ModelCatalogError("请输入 API Key。")
    default_url = GO_BASE_URL if provider == "opencode" else DEEPSEEK_BASE_URL
    catalog_url = _safe_url(base_url, default=default_url)
    payload = _request(
        catalog_url + "/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    result = []
    default = "deepseek-chat" if provider == "deepseek" else "deepseek-v4-flash"
    entries = payload.get("data")
    if not isinstance(entries, list):
        raise ModelCatalogError("模型目录返回格式无效。")
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("id"), str)
            or not entry["id"]
        ):
            continue
        model = entry["id"]
        if (
            provider == "opencode"
            and catalog_url == GO_BASE_URL.rstrip("/")
            and not model.startswith(_OPENCODE_GO_CHAT_PREFIXES)
        ):
            continue
        result.append(ModelInfo(
            model,
            str(entry.get("name") or entry.get("display_name") or model),
            model == default,
        ))
    return _dedupe(result)


def _dedupe(items: list[ModelInfo]) -> list[ModelInfo]:
    seen = set()
    result = []
    for item in items:
        if item.id not in seen:
            seen.add(item.id)
            result.append(item)
    return result


def discover_models(provider: str, *, api_key: str = "", base_url: str = "") -> list[dict]:
    """Return a JSON-safe model catalog for one provider."""
    if provider == "codex":
        return [item.public() for item in _codex()]
    if provider == "gemini":
        items = _gemini(api_key)
    elif provider == "deepseek":
        # DeepSeek is not configurable: never forward its saved key to a
        # caller-supplied host.  Only OpenCode exposes a custom compatible URL.
        items = _compatible(DEEPSEEK_BASE_URL, api_key, provider)
    elif provider == "opencode":
        items = _compatible(base_url, api_key, provider)
    else:
        raise ModelCatalogError("请选择有效的 AI 提供商。")
    return [item.public() for item in items]
