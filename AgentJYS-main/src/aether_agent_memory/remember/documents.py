"""Authorized immutable document ingress and the existing Remember document port."""

import asyncio
import io
from hashlib import sha256
from typing import Any

from aether_agent_memory.remember.basic.sources import PreparedDocument
from aether_agent_memory.remember.contracts.models import DocumentInput
from aether_agent_memory.resource.parsing import (
    OptionalDocumentResourceParser,
    PlainTextResourceParser,
)
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Flow,
    Permission,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.requests import text_hash


class Documents:
    provider_id = "p3_documents"

    def __init__(self, remember: Any) -> None:
        self.remember = remember

    @staticmethod
    def parse_document(raw: bytes, media_type: str) -> tuple[str, str]:
        """Document ingress owns parsing; business memory consumes verified text."""
        # Malformed user files are input errors, not a broken P2 response contract.
        # Import optional parser exception types only for the selected format.
        invalid: tuple[type[Exception], ...] = (ValueError, UnicodeError)
        if media_type == "application/pdf":
            from pypdf.errors import PdfReadError

            invalid += (PdfReadError,)
        elif (
            media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ):
            from zipfile import BadZipFile

            from lxml.etree import XMLSyntaxError

            invalid += (BadZipFile, XMLSyntaxError, KeyError)
        try:
            return Documents._parse_document(raw, media_type)
        except invalid as exc:
            raise FoundationError(
                ErrorCode.INVALID_ARGUMENT, "document is malformed or cannot be decoded"
            ) from exc

    @staticmethod
    def _parse_document(raw: bytes, media_type: str) -> tuple[str, str]:
        if media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            from docx import Document
            from docx.table import Table
            from docx.text.paragraph import Paragraph

            document = Document(io.BytesIO(raw))
            parts = []
            for block in document.iter_inner_content():
                if isinstance(block, Paragraph):
                    parts.append(block.text)
                elif isinstance(block, Table):
                    for row in block.rows:
                        parts.append("\t".join(cell.text for cell in row.cells))
            text, parser = "\n".join(parts), "docx-paragraph-table-v2"
        else:
            plain = PlainTextResourceParser()
            result = (
                plain.parse(raw.decode("utf-8-sig"), media_type)
                if plain.supports(media_type)
                else OptionalDocumentResourceParser().parse_bytes(raw, media_type)
            )
            text, parser = result.text, result.parser
        if not text.strip():
            raise FoundationError(
                ErrorCode.INVALID_ARGUMENT,
                "document has no readable text; OCR is not configured",
            )
        return text, parser

    def ref(self, ctx: TrustedContext, document_id: str) -> RecordRef:
        return RecordRef(
            owner=Flow.REMEMBER,
            object_type="document",
            object_id=document_id,
            scope=ctx.principal.home_scope,
        )

    async def upload(
        self, ctx: TrustedContext, document_id: str, version: str, body: bytes, media_type: str
    ) -> DocumentInput:
        prepared = self.prepare_upload(ctx, document_id, version, body, media_type)
        await self.persist_upload(prepared, body)
        return self.commit_upload(ctx, prepared)

    def prepare_upload(
        self,
        ctx: TrustedContext,
        document_id: str,
        version: str,
        body: bytes,
        media_type: str,
    ) -> dict[str, Any]:
        if not body or len(body) > self.remember.policy.max_input_bytes:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "document exceeds ingress limit")
        media_type = media_type.split(";", 1)[0].strip().lower()
        if not (
            PlainTextResourceParser().supports(media_type)
            or OptionalDocumentResourceParser().supports(media_type)
        ):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "unsupported document media type")
        ref = self.ref(ctx, document_id)
        digest = sha256(body).hexdigest()
        document = DocumentInput(
            kind="document",
            provider_id=self.provider_id,
            document_id=document_id,
            document_version=version,
            expected_hash=digest,
        )
        key = fingerprint([ref.model_dump(mode="json"), version])
        object_key = "documents/" + fingerprint([key, digest])
        value = {
            "document": document.model_dump(mode="json"),
            "ref": ref.model_dump(mode="json"),
            "object_key": object_key,
            "bytes": len(body),
            "media_type": media_type,
        }
        with self.remember.uow.transaction() as tx:
            self.remember.identity.authorize(tx, ctx, Permission.WRITE, ref)
            old = tx.read("documents", key)
            if old and old != value:
                tx.abort(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "document version already has different content"
                )
        return {"key": key, "value": value}

    async def persist_upload(self, prepared: dict[str, Any], body: bytes) -> None:
        object_key = prepared["value"]["object_key"]
        await self.remember.bodies.p2_call("put_object", object_key, body)
        saved = await self.remember.bodies.p2_call("get_object", object_key)
        if saved != body:
            raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "document bytes not confirmed")

    def commit_upload(self, ctx: TrustedContext, prepared: dict[str, Any]) -> DocumentInput:
        key, value = prepared["key"], prepared["value"]
        ref = RecordRef.model_validate(value["ref"])
        document = DocumentInput.model_validate(value["document"])
        with self.remember.uow.transaction() as tx:
            self.remember.identity.authorize(tx, ctx, Permission.WRITE, ref)
            old = tx.read("documents", key)
            if old and old != value:
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "concurrent document version conflict")
            tx.write("documents", key, value)
        return document

    async def acquire_text(self, ctx: TrustedContext, document: DocumentInput) -> PreparedDocument:
        ref = self.ref(ctx, document.document_id)
        key = fingerprint([ref.model_dump(mode="json"), document.document_version])
        with self.remember.uow.transaction() as tx:
            self.remember.identity.authorize(tx, ctx, Permission.READ, ref)
            row = tx.read("documents", key)
            if not row or row["document"] != document.model_dump(mode="json"):
                tx.abort(ErrorCode.NOT_FOUND, "exact document version unavailable")
        raw = await self.remember.bodies.p2_call("get_object", row["object_key"])
        if (
            raw is None
            or sha256(raw).hexdigest() != document.expected_hash
            or len(raw) != row["bytes"]
        ):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "document source hash mismatch")

        text, parser_version = await asyncio.to_thread(self.parse_document, raw, row["media_type"])
        if len(text.encode("utf-8")) > self.remember.policy.max_input_bytes:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "parsed document exceeds limit")
        with self.remember.uow.transaction() as tx:
            self.remember.identity.authorize(tx, ctx, Permission.READ, ref)
            if tx.read("documents", key) != row:
                tx.abort(ErrorCode.RESULT_INVALIDATED, "document changed while reading")
        return PreparedDocument(
            document=document,
            original_storage_ref=row["object_key"],
            original_bytes=row["bytes"],
            media_type=row["media_type"],
            parsed_text=text,
            parsed_hash=text_hash(text),
            parser_version=parser_version,
        )
