"""Provider-neutral multimodal messages.

Application workflows use these small value objects instead of constructing a
provider's wire payload.  Each provider adapter is responsible for translating
the same image bytes into its native request shape.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ImagePart:
    """One already validated image sent with a user turn."""

    mime_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class MultimodalMessage:
    """A text-and-image message understood by every provider adapter."""

    role: str
    text: str
    images: tuple[ImagePart, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or not self.role:
            raise ValueError("消息 role 必须是非空文本")
        if not isinstance(self.text, str):
            raise ValueError("消息文本必须是文本")
        if not isinstance(self.images, tuple):
            object.__setattr__(self, "images", tuple(self.images))


def multimodal_message(
    role: str, text: str, images: tuple[ImagePart, ...] | list[ImagePart]
) -> MultimodalMessage:
    """Construct a stable message without exposing provider payload details."""

    return MultimodalMessage(role=role, text=text, images=tuple(images))
