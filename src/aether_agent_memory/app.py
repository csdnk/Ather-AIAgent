"""FastAPI application host for the P3 AetherBrain memory service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from aether_agent_memory.api.routers import (
    context,
    demo,
    embedding,
    health,
    memory,
    scheduling,
    tasks,
)
from aether_agent_memory.bootstrap import build_runtime
from aether_agent_memory.config.app_settings import AppSettings
from aether_agent_memory.integration import runtime_error_to_http
from aether_agent_memory.runtime import RuntimeErrorBase


def create_app(settings: AppSettings | None = None) -> FastAPI:
    """Build the P3 ASGI application with one long-lived runtime."""
    resolved = settings or AppSettings()
    resolved.validate_for_profile()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = build_runtime(resolved)
        app.state.runtime = runtime
        app.state.settings = resolved
        try:
            yield
        finally:
            await runtime.close()

    app = FastAPI(
        title="AetherBrain P3",
        version=resolved.version,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.exception_handler(RuntimeErrorBase)
    async def _runtime_error(_: Request, exc: RuntimeErrorBase) -> JSONResponse:
        status, payload = runtime_error_to_http(exc)
        return JSONResponse(status_code=status, content=payload)

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": str(exc)})

    app.include_router(demo.router)
    app.include_router(health.router)
    app.include_router(embedding.router)
    app.include_router(memory.router)
    app.include_router(context.router)
    app.include_router(tasks.router)
    app.include_router(scheduling.router)
    return app


def main() -> None:
    """Run the P3 application host with uvicorn."""
    import uvicorn

    resolved = AppSettings()
    resolved.validate_for_profile()
    uvicorn.run(
        "aether_agent_memory.app:create_app",
        factory=True,
        host=resolved.host,
        port=resolved.port,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
