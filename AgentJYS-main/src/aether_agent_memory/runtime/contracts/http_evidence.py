"""Evidence of received HTTP bytes; a containing transaction proves admission."""

import re
from typing import Literal, Self
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

from pydantic import Field, model_validator

from .client_admission import ClientRunAdmission
from .models import ContractModel, Digest

HTTP_EFFECT_ROUTES = {
    "remember.save": ("POST", "/p3/remember"),
    "recall.execute": ("POST", "/p3/recall"),
    "remember.correct": ("POST", "/p3/remember/{memory_id}/correct"),
    "remember.document": ("PUT", "/p3/documents/{document_id}"),
    **{
        "remember." + kind: ("POST", "/p3/remember/" + kind)
        for kind in ("consolidate", "distill", "reflection")
    },
    **{
        "remember." + kind: ("POST", "/p3/remember/{memory_id}/" + kind)
        for kind in ("reprocess", "reindex", "lifecycle", "retention", "delete")
    },
    **{
        "source." + kind: ("POST", "/p3/sources/{source_id}/" + kind)
        for kind in ("delete", "revoke")
    },
}


def effective_http_target(route: str, target: str) -> str:
    """Resolve a supported business target independently of gateway mount prefixes."""
    if route not in {path for _, path in HTTP_EFFECT_ROUTES.values()}:
        raise ValueError("unsupported HTTP effect route")
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or "#" in target:
        raise ValueError("relative HTTP target required")
    parts = re.split(r"(\{[a-z_]+\})", route)
    template = "".join(
        "[A-Za-z0-9_-]+" if part.startswith("{") else re.escape(part) for part in parts
    )
    match = re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)*(?P<path>" + template + ")", unquote(parsed.path))
    if match is None:
        raise ValueError("HTTP target differs from its route")
    path = quote(match.group("path"), safe="/")
    if route == "/p3/documents/{document_id}":
        query = dict(parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=8))
        if "version" not in query:
            raise ValueError("document version required")
        path += "?" + urlencode({"version": query["version"]})
    return path


class HttpRequestEvidence(ContractModel):
    version: Literal[1] = 1
    method: Literal["POST", "PUT"]
    route: str = Field(max_length=192)
    target: str = Field(max_length=4096)
    body_hash: Digest
    content_type: str = Field(max_length=128, pattern=r"^[ -~]*$")
    client_run: ClientRunAdmission | None = None

    def matches_kind(self, kind: str) -> bool:
        return HTTP_EFFECT_ROUTES.get(kind) == (self.method, self.route)

    @model_validator(mode="after")
    def canonical_target(self) -> Self:
        if (self.method, self.route) not in HTTP_EFFECT_ROUTES.values():
            raise ValueError("unsupported HTTP method and route")
        if effective_http_target(self.route, self.target) != self.target:
            raise ValueError("server evidence must use the effective business target")
        return self
