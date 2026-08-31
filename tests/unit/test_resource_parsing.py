from __future__ import annotations

from io import BytesIO

import pytest

from aether_agent_memory.resource.parsing import OptionalDocumentResourceParser


@pytest.mark.unit
def test_optional_docx_parser_extracts_paragraph_text() -> None:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("AetherStore keeps Resource identity in P3.")
    payload = BytesIO()
    document.save(payload)

    parsed = OptionalDocumentResourceParser().parse_bytes(
        payload.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert parsed.parser == "optional-pdf-docx-v1"
    assert "Resource identity" in parsed.text
    assert parsed.token_estimate > 0
