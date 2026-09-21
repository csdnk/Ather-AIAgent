from __future__ import annotations

from typing import Self
from urllib.parse import quote, unquote

from pydantic import ConfigDict, RootModel, model_validator

_SCHEME = "aether://"
_SEGMENT_SAFE = "-._~"


class AetherUri(RootModel[str]):
    """Canonical internal identity for a context item.

    Provider locations such as ``p2://`` and Milvus collection names remain
    placement references. They are deliberately not used as context identity.
    """

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _validate_and_normalize(cls, value: object) -> str:
        if isinstance(value, cls):
            return value.root
        if isinstance(value, dict) and "root" in value:
            value = value["root"]
        if not isinstance(value, str):
            raise TypeError("Aether URI must be a string")
        if not value.startswith(_SCHEME):
            raise ValueError("Aether URI must start with aether://")
        if "?" in value or "#" in value:
            raise ValueError("Aether URI cannot contain query or fragment components")
        encoded_parts = value[len(_SCHEME) :].split("/")
        if not encoded_parts or any(not part for part in encoded_parts):
            raise ValueError("Aether URI must contain non-empty path segments")
        decoded_parts = tuple(unquote(part) for part in encoded_parts)
        for part in decoded_parts:
            _validate_segment(part)
        return _SCHEME + "/".join(_encode_segment(part) for part in decoded_parts)

    @classmethod
    def from_segments(cls, *segments: str) -> Self:
        if not segments:
            raise ValueError("Aether URI requires at least one segment")
        for segment in segments:
            _validate_segment(segment)
        return cls(_SCHEME + "/".join(_encode_segment(segment) for segment in segments))

    @property
    def segments(self) -> tuple[str, ...]:
        return tuple(unquote(part) for part in self.root[len(_SCHEME) :].split("/"))

    @property
    def namespace(self) -> str:
        return self.segments[0]

    @property
    def parent(self) -> AetherUri | None:
        parts = self.segments
        if len(parts) == 1:
            return None
        return AetherUri.from_segments(*parts[:-1])

    def child(self, *segments: str) -> AetherUri:
        return AetherUri.from_segments(*self.segments, *segments)

    def is_within(self, other: AetherUri) -> bool:
        own = self.segments
        parent = other.segments
        return len(own) >= len(parent) and own[: len(parent)] == parent

    def __str__(self) -> str:
        return self.root


def _validate_segment(segment: str) -> None:
    if not segment or segment in {".", ".."}:
        raise ValueError("Aether URI segments must be non-empty and cannot traverse parents")
    if any(character in segment for character in ("/", "\\", "\x00", "?", "#")):
        raise ValueError("Aether URI segment contains a reserved character")


def _encode_segment(segment: str) -> str:
    return quote(segment, safe=_SEGMENT_SAFE)
