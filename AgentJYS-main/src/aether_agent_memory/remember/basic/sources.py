"""Remember's authorized text-source reader; P2 owns parsing and physical range I/O."""

import asyncio
from typing import Any

from pydantic import BaseModel, Field, model_validator

from aether_agent_memory.remember.contracts.models import (
    DocumentInput,
    MemorySnapshot,
    SourceRef,
)
from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Flow,
    Permission,
    RecordRef,
    Scope,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import text_hash


class PreparedDocument(BaseModel):
    """Trusted P2/provider result. Original bytes and parsed text have separate identities.

    acquire_text(ctx, DocumentInput) must authorize and verify the original object
    before returning. Remember never runs PDF/DOCX parsers or arbitrary URL fetches.
    A pending/failed parser must raise a typed dependency/input error, not empty text.
    """

    document: DocumentInput
    original_storage_ref: str = Field(min_length=1)
    original_bytes: int = Field(gt=0)
    media_type: str = Field(min_length=1)
    parsed_text: str = Field(min_length=1)
    parsed_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    parser_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def text_binding(self) -> "PreparedDocument":
        if not self.parsed_text.strip() or text_hash(self.parsed_text) != self.parsed_hash:
            raise ValueError("parsed text/hash mismatch")
        return self


class SourceAccess:
    def __init__(self, owner: Any) -> None:
        self.owner = owner

    def register(self, tx: Any, source: SourceRef, text: str) -> None:
        pages = []
        offset = 0
        size = self.owner.policy.source_page_chars
        for start in range(0, len(text), size):
            part = text[start : start + size]
            length = len(part.encode("utf-8"))
            pages.append(
                {
                    "start_char": start,
                    "end_char": start + len(part),
                    "start_byte": offset,
                    "end_byte": offset + length,
                    "hash": text_hash(part),
                }
            )
            offset += length
        tx.write(
            "remember_source_ranges",
            source.source_id,
            {"source": source.model_dump(mode="json"), "chars": len(text), "pages": pages},
        )

    def checked(self, tx: Any, ctx: TrustedContext, source: SourceRef) -> dict[str, Any]:
        row = tx.read("remember_sources", source.source_id)
        if row is None:
            raise FoundationError(ErrorCode.NOT_FOUND, "source unavailable")
        self.owner.identity.authorize(
            tx,
            ctx,
            Permission.READ,
            RecordRef(
                owner=Flow.REMEMBER,
                object_type="source",
                object_id=source.source_id,
                scope=Scope.model_validate(row["scope"]),
            ),
        )
        if not row.get("valid") or row["ref"] != source.model_dump(mode="json"):
            raise FoundationError(ErrorCode.MEMORY_GONE, "source revoked or version changed")
        return dict(row)

    async def read(
        self, ctx: TrustedContext, source: SourceRef, start: int = 0, end: int | None = None
    ) -> dict[str, Any]:
        def source_binding() -> tuple[Any, Any]:
            with self.owner.uow.transaction() as tx:
                row = self.checked(tx, ctx, source)
                manifest = tx.read("remember_source_ranges", source.source_id)
            return row, manifest

        row, manifest = await asyncio.to_thread(source_binding)
        if manifest is None:
            # Legacy sources only. New saves always commit a range manifest.
            location = ResourceLocation.model_validate(row["original_location"])
            text, _ = await self.owner.bodies.read(Scope.model_validate(row["scope"]), location)
            if text_hash(text) != source.content_hash:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "source hash mismatch")

            def register_ranges() -> Any:
                with self.owner.uow.transaction() as tx:
                    self.checked(tx, ctx, source)
                    self.register(tx, source, text)
                    manifest = tx.read("remember_source_ranges", source.source_id)
                return manifest

            manifest = await asyncio.to_thread(register_ranges)
        if manifest["source"] != source.model_dump(mode="json"):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "source manifest mismatch")
        limit = self.owner.policy.source_read_max_chars
        total = manifest["chars"]
        stop = min(total, start + limit) if end is None else end
        if not 0 <= start <= stop <= total or stop - start > limit:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "source range exceeds read budget")
        location = ResourceLocation.model_validate(row["original_location"])
        self.owner.bodies.check_binding(location)
        pieces = []
        for page in manifest["pages"]:
            if page["end_char"] <= start or page["start_char"] >= stop:
                continue
            if location.provider_id != "local":
                raw = await self.owner.bodies.p2_call(
                    "read_range", location.object_key, page["start_byte"], page["end_byte"]
                )
            else:

                def local_read(begin: int, length: int) -> bytes:
                    with self.owner.bodies.path(location).open("rb") as stream:
                        stream.seek(begin)
                        return bytes(stream.read(length))

                raw = await asyncio.to_thread(
                    local_read, page["start_byte"], page["end_byte"] - page["start_byte"]
                )
            try:
                part = raw.decode("utf-8")
            except (AttributeError, UnicodeError) as exc:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "invalid source bytes") from exc
            if (
                text_hash(part) != page["hash"]
                or len(part) != page["end_char"] - page["start_char"]
            ):
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "source page hash mismatch")
            pieces.append(part[max(0, start - page["start_char"]) : stop - page["start_char"]])
        content = "".join(pieces)
        if len(content) != stop - start:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "source range coverage mismatch")

        def recheck_source() -> Any:
            with self.owner.uow.transaction() as tx:
                current = self.checked(tx, ctx, source)
                if current["revision"] != row["revision"]:
                    raise FoundationError(
                        ErrorCode.RESULT_INVALIDATED, "source changed during read"
                    )
                document = tx.read("remember_source_documents", source.source_id)
            return document

        document = await asyncio.to_thread(recheck_source)
        return {
            "source": source.model_dump(mode="json"),
            "representation": "source_text_range",
            "content": content,
            "start_char": start,
            "end_char": stop,
            "total_chars": total,
            "next_start": stop if stop < total else None,
            "is_complete": start == 0 and stop == total,
            "source_hash": source.content_hash,
            "range_hash": text_hash(content),
            "document": document,
        }

    async def originals(
        self, ctx: TrustedContext, items: tuple[MemorySnapshot, ...]
    ) -> tuple[MemorySnapshot, ...]:
        """Private worker evidence DTOs. Never return source text as Working to Recall."""
        originals = []
        for item in items:
            if item.kind != "working":
                originals.append(item)
                continue
            for source in item.sources:
                parts = []
                start = 0
                while True:
                    page = await self.read(ctx, source, start)
                    parts.append(page["content"])
                    if page["next_start"] is None:
                        break
                    start = page["next_start"]
                text = "".join(parts)
                if text_hash(text) != source.content_hash:
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "source body hash mismatch")
                originals.append(
                    item.model_copy(
                        update={
                            "content": text,
                            "content_hash": source.content_hash,
                            "sources": (source,),
                        }
                    )
                )
        return tuple(originals)
