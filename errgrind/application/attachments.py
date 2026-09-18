"""Small application-side helpers for durable multimodal attachments."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ..llm.messages import ImagePart, MultimodalMessage
from ..llm.ocr import load_image


def image_parts_from_paths(paths: Iterable[str]) -> tuple[ImagePart, ...]:
    """Validate and load image files without carrying their paths downstream."""
    return tuple(
        ImagePart(payload.mime_type, payload.data)
        for path in paths
        for payload in (load_image(path),)
    )


def _message_attachment_ids(message: Mapping) -> list[int]:
    values = message.get("attachments", [])
    if values is None:
        return []
    if not isinstance(values, list) or any(
        isinstance(value, bool) or not isinstance(value, int) for value in values
    ):
        raise ValueError("对话附件引用无效")
    return values


def hydrate_message(db, message: Mapping, *, error_id: int):
    """Turn persisted attachment IDs into a provider-neutral user message."""
    ids = _message_attachment_ids(message)
    if not ids:
        return dict(message)
    stored = db.get_attachments_for_ids(ids, error_id=error_id)
    return MultimodalMessage(
        role=message.get("role", "user"),
        text=message.get("content", ""),
        images=tuple(ImagePart(item.mime_type, item.data) for item in stored),
    )


def hydrate_messages(db, messages: Sequence[Mapping], *, error_id: int) -> list:
    """Hydrate only messages which carry attachments; keep text fixtures stable."""
    return [
        hydrate_message(db, message, error_id=error_id)
        if _message_attachment_ids(message)
        else dict(message)
        for message in messages
    ]


def attachment_tuples(images: Iterable[ImagePart]) -> list[tuple[str, bytes]]:
    """Return the neutral values accepted by the DB attachment relation."""
    return [(image.mime_type, image.data) for image in images]
