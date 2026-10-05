"""Capture at HTTP boundaries; domain/internal calls supply no HTTP evidence."""

import re
from hashlib import sha256
from urllib.parse import quote, urlencode

from fastapi import Request

from aether_agent_memory.runtime.contracts.client_admission import ClientRunAdmission
from aether_agent_memory.runtime.contracts.http_evidence import HttpRequestEvidence
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError


def client_admission(request: Request) -> ClientRunAdmission | None:
    names = ("X-P3-Run-ID", "X-P3-Run-Owner", "X-P3-Run-Revision")
    values = [request.headers.getlist(name) for name in names]
    if not any(values):
        return None
    if any(len(value) != 1 for value in values):
        raise FoundationError(ErrorCode.INVALID_ARGUMENT, "caller admission headers incomplete")
    if re.fullmatch(r"[1-9][0-9]{0,18}", values[2][0]) is None:
        raise FoundationError(ErrorCode.INVALID_ARGUMENT, "caller admission revision invalid")
    return ClientRunAdmission.model_validate(
        {"run_id": values[0][0], "owner_id": values[1][0], "revision": int(values[2][0])}
    )


def evidence_from_hash(
    request: Request, digest: str, *, version: str | None = None
) -> HttpRequestEvidence:
    route = str(request.scope["route"].path)
    target = route.format_map(
        {key: quote(str(value), safe="") for key, value in request.path_params.items()}
    )
    if version is not None:
        target += "?" + urlencode({"version": version})
    return HttpRequestEvidence(
        method="PUT" if request.method == "PUT" else "POST",
        route=route,
        target=target,
        body_hash=digest,
        content_type=request.headers.get("content-type", ""),
        client_run=client_admission(request),
    )


async def capture_http_request(request: Request) -> HttpRequestEvidence:
    digest, size = sha256(), 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > request.app.state.runtime.remember.policy.max_input_bytes:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "HTTP body exceeds ingress limit")
        digest.update(chunk)
    return evidence_from_hash(request, digest.hexdigest())
