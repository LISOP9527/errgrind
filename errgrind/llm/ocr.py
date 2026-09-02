"""Shared image validation and OCR response contract."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_IMAGE_BYTES = 20 * 1024 * 1024

OCR_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "user_thoughts": {"type": "string"},
        "reference_answer": {"type": "string"},
    },
    "required": ["question", "user_thoughts", "reference_answer"],
    "additionalProperties": False,
}


class OcrError(Exception):
    """User-facing OCR validation or response error."""


@dataclass(frozen=True, slots=True)
class ImagePayload:
    path: str
    mime_type: str
    data: bytes

    @property
    def base64_data(self) -> str:
        return base64.b64encode(self.data).decode("ascii")

    @property
    def data_url(self) -> str:
        return f"data:{self.mime_type};base64,{self.base64_data}"


def _detect_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def load_image(image_path: str, max_bytes: int = MAX_IMAGE_BYTES) -> ImagePayload:
    """Validate and load a PNG, JPEG, or WebP without trusting its suffix."""
    raw_path = (image_path or "").strip()
    if not raw_path:
        raise OcrError("请提供图片路径")

    path = Path(raw_path).expanduser()
    try:
        path = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise OcrError(f"图片不存在或无法访问: {raw_path}") from exc
    if not path.is_file():
        raise OcrError(f"图片路径不是文件: {path}")

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise OcrError(f"无法读取图片信息: {exc}") from exc
    if size <= 0:
        raise OcrError("图片文件为空")
    if size > max_bytes:
        raise OcrError(f"图片过大（上限 {max_bytes // 1024 // 1024} MB）")

    try:
        data = path.read_bytes()
    except OSError as exc:
        raise OcrError(f"无法读取图片: {exc}") from exc
    mime_type = _detect_mime(data)
    if mime_type is None:
        raise OcrError("仅支持 PNG、JPEG 和 WebP 图片")
    return ImagePayload(str(path), mime_type, data)


def parse_ocr_result(value: Any) -> dict[str, str]:
    """Parse and enforce the OCR contract shared by every provider."""
    if isinstance(value, str):
        cleaned = value.strip().removeprefix("```json").removeprefix("```")
        cleaned = cleaned.removesuffix("```").strip()
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise OcrError(f"OCR 响应不是合法 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise OcrError("OCR 响应必须是 JSON 对象")

    result: dict[str, str] = {}
    for field in ("question", "user_thoughts", "reference_answer"):
        item = value.get(field, "")
        if not isinstance(item, str):
            raise OcrError(f"OCR 字段 {field} 必须是文本")
        result[field] = item.strip()
    if not result["question"]:
        raise OcrError("没有从图片中识别出题目，请换一张更清晰的图片")
    return result
