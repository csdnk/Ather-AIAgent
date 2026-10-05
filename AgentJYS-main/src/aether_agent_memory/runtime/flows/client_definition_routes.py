"""Authenticated binary transport for immutable caller definitions."""

import asyncio
from hashlib import sha256
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, Request, Response

from aether_agent_memory.runtime.contracts.client_definitions import ClientDefinitionReceipt
from aether_agent_memory.runtime.contracts.models import ErrorCode, Identifier, TrustedContext
from aether_agent_memory.runtime.foundation.client_definitions import ClientDefinitions
from aether_agent_memory.runtime.foundation.common import FoundationError


def attach(app: FastAPI, definitions: ClientDefinitions) -> None:
    dependency = app.state.trusted_dependency

    @app.put("/p3/client-runs/{run_id}/definition")
    async def prepare(
        run_id: UUID,
        request: Request,
        owner: Annotated[Identifier, Header(alias="X-P3-Run-Owner")],
        revision: Annotated[int, Header(alias="X-P3-Run-Revision", ge=1)],
        ctx: TrustedContext = dependency,
    ) -> ClientDefinitionReceipt:
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > definitions.max_bytes:
                raise FoundationError(
                    ErrorCode.INVALID_ARGUMENT, "caller definition exceeds ingress limit"
                )
            payload.extend(chunk)
        return await asyncio.to_thread(
            definitions.prepare, ctx, run_id, owner, revision, bytes(payload)
        )

    @app.get("/p3/client-runs/{run_id}/definition")
    def read(run_id: UUID, ctx: TrustedContext = dependency) -> Response:
        payload = definitions.read(ctx, run_id)
        return Response(
            payload,
            media_type="application/octet-stream",
            headers={
                "X-P3-Definition-Hash": sha256(payload).hexdigest(),
                "Cache-Control": "no-store",
            },
        )
