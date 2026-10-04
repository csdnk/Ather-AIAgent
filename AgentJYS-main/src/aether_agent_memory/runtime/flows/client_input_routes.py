"""Authenticated, bounded binary transport for original caller request bytes."""

import asyncio
from hashlib import sha256
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, Request, Response

from aether_agent_memory.runtime.contracts.client_inputs import ClientInputReceipt
from aether_agent_memory.runtime.contracts.models import ErrorCode, Identifier, TrustedContext
from aether_agent_memory.runtime.foundation.client_inputs import ClientInputs
from aether_agent_memory.runtime.foundation.common import FoundationError


def attach(app: FastAPI, inputs: ClientInputs) -> None:
    dependency = app.state.trusted_dependency

    @app.put("/p3/client-runs/{run_id}/inputs/{operation_id}")
    async def prepare(
        run_id: UUID,
        operation_id: Identifier,
        request: Request,
        owner_id: Annotated[Identifier, Header(alias="X-P3-Run-Owner")],
        revision: Annotated[int, Header(alias="X-P3-Run-Revision", ge=1)],
        ctx: TrustedContext = dependency,
    ) -> ClientInputReceipt:
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > inputs.max_bytes:
                raise FoundationError(
                    ErrorCode.INVALID_ARGUMENT, "caller input exceeds ingress limit"
                )
            payload.extend(chunk)
        return await asyncio.to_thread(
            inputs.prepare, ctx, run_id, operation_id, owner_id, revision, bytes(payload)
        )

    @app.get("/p3/client-runs/{run_id}/inputs/{operation_id}")
    def read(run_id: UUID, operation_id: Identifier, ctx: TrustedContext = dependency) -> Response:
        payload = inputs.read(ctx, run_id, operation_id)
        return Response(
            payload,
            media_type="application/octet-stream",
            headers={
                "X-P3-Request-Hash": sha256(payload).hexdigest(),
                "Cache-Control": "no-store",
            },
        )
