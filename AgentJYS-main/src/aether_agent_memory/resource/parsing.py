from __future__ import annotations

import importlib
import importlib.util
import io
from typing import Any, Protocol

from pydantic import BaseModel, Field


class ParsedResourceContent(BaseModel):
    """Provider-neutral result of turning a resource payload into text."""

    text: str
    media_type: str
    parser: str
    token_estimate: int = Field(ge=0)


class ResourceContentParserPort(Protocol):
    @property
    def name(self) -> str: ...

    def supports(self, media_type: str) -> bool: ...

    def parse(self, content: str, media_type: str) -> ParsedResourceContent: ...


class BinaryResourceContentParserPort(Protocol):
    @property
    def name(self) -> str: ...

    def supports(self, media_type: str) -> bool: ...

    def parse_bytes(self, content: bytes, media_type: str) -> ParsedResourceContent: ...


class PlainTextResourceParser:
    """Reference parser for text resources; binary parsers remain replaceable."""

    name = "plain-text-v1"
    supported_media_types = frozenset(
        {
            "text/plain",
            "text/markdown",
            "text/csv",
            "application/json",
        }
    )

    def supports(self, media_type: str) -> bool:
        normalized = media_type.casefold().split(";", 1)[0].strip()
        return normalized in self.supported_media_types

    def parse(self, content: str, media_type: str) -> ParsedResourceContent:
        if not self.supports(media_type):
            raise ValueError(
                f"no resource parser is configured for media type {media_type!r}; "
                "provide a supported parser or an external content_ref"
            )
        return ParsedResourceContent(
            text=content,
            media_type=media_type,
            parser=self.name,
            token_estimate=max(len(content) // 4, 1) if content else 0,
        )


class OptionalDocumentResourceParser:
    """Parse PDF/DOCX only when their optional libraries are installed."""

    name = "optional-pdf-docx-v1"
    supported_media_types = frozenset(
        {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    )

    def supports(self, media_type: str) -> bool:
        normalized = media_type.casefold().split(";", 1)[0].strip()
        if normalized not in self.supported_media_types:
            return False
        module_name = "pypdf" if normalized == "application/pdf" else "docx"
        return importlib.util.find_spec(module_name) is not None

    def parse_bytes(self, content: bytes, media_type: str) -> ParsedResourceContent:
        normalized = media_type.casefold().split(";", 1)[0].strip()
        if not self.supports(normalized):
            raise ValueError(
                f"no binary resource parser is configured for media type {media_type!r}"
            )
        if normalized == "application/pdf":
            text = self._parse_pdf(content)
        else:
            text = self._parse_docx(content)
        return ParsedResourceContent(
            text=text,
            media_type=media_type,
            parser=self.name,
            token_estimate=max(len(text) // 4, 1) if text else 0,
        )

    @staticmethod
    def _parse_pdf(content: bytes) -> str:
        module: Any = importlib.import_module("pypdf")
        reader = module.PdfReader(io.BytesIO(content))
        return "\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()

    @staticmethod
    def _parse_docx(content: bytes) -> str:
        module: Any = importlib.import_module("docx")
        document = module.Document(io.BytesIO(content))
        paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs]
        return "\n".join(text for text in paragraphs if text).strip()
