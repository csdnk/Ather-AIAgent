from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from aether_agent_memory.api.dependencies import json_model, runtime_of
from aether_agent_memory.b1 import EmbeddingRequest
from aether_agent_memory.demo.service import b1_sidecar_embedding, b1_sidecar_status
from aether_agent_memory.integration import request_context_from_payload

router = APIRouter()


@router.get("/api/v1/b1/status")
async def b1_status() -> dict[str, Any]:
    return await b1_sidecar_status()


@router.post("/api/v1/b1/embeddings")
async def b1_embeddings(body: dict[str, Any]) -> dict[str, Any]:
    return await b1_sidecar_embedding(body)


@router.post("/api/v1/embeddings")
async def embed(request: Request, body: EmbeddingRequest) -> dict[str, Any]:
    runtime = runtime_of(request)
    context = request_context_from_payload(body)
    result = await runtime.embed(body, context)
    return json_model(result)
