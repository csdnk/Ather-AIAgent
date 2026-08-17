"""EmbeddingClient adapter for the deployed B1 Sidecar HTTP boundary."""

from __future__ import annotations

import math
import uuid
from typing import Any


class SidecarEmbeddingError(RuntimeError):
    """Raised when the B1 Sidecar cannot return a valid embedding batch."""


class SidecarEmbeddingClient:
    """Keep the existing ``EmbeddingClient`` contract while calling B1 over HTTP."""

    def __init__(
        self,
        base_url: str,
        *,
        tenant_id: str = "p3-runtime",
        source_type: str = "p3_embedding",
        timeout_seconds: float = 120.0,
        max_batch_items: int = 32,
        transport: Any | None = None,
    ) -> None:
        normalized = base_url.strip().rstrip("/")
        if not normalized:
            raise ValueError("B1 Sidecar URL must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("Sidecar timeout must be positive")
        if max_batch_items <= 0:
            raise ValueError("Sidecar batch size must be positive")
        self.base_url = normalized
        self.tenant_id = tenant_id
        self.source_type = source_type
        self.timeout_seconds = timeout_seconds
        self.max_batch_items = max_batch_items
        self._transport = transport
        self._client: Any | None = None
        self._ready = False
        self._dimension: int | None = None
        self._model_name: str | None = None

    @property
    def dimension(self) -> int | None:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name or "b1-sidecar"

    async def _http_client(self) -> Any:
        if self._client is None:
            try:
                import httpx
            except ImportError as exc:  # pragma: no cover - optional dependency boundary
                raise SidecarEmbeddingError(
                    "httpx is required for B1 Sidecar integration; install .[b1-sidecar]"
                ) from exc
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout_seconds),
                transport=self._transport,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def ensure_ready(self) -> None:
        if self._ready:
            return
        client = await self._http_client()
        try:
            response = await client.get("/health/ready")
        except Exception as exc:
            raise SidecarEmbeddingError(f"B1 Sidecar readiness request failed: {exc}") from exc
        payload = self._response_json(response)
        if response.status_code != 200 or payload.get("status") != "ready":
            raise SidecarEmbeddingError(
                f"B1 Sidecar is not ready: {payload.get('load_error') or payload}"
            )
        dimension = payload.get("dimension")
        if not isinstance(dimension, int) or dimension <= 0:
            raise SidecarEmbeddingError("B1 Sidecar readiness did not report a valid dimension")
        self._dimension = dimension
        model = payload.get("model")
        self._model_name = str(model) if model else None
        self._ready = True

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, input_type="passage")

    async def embed_one(self, text: str) -> list[float]:
        vectors = await self._embed([text], input_type="query")
        return vectors[0]

    async def _embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        if not texts:
            return []
        await self.ensure_ready()
        output: list[list[float]] = []
        run_id = uuid.uuid4().hex
        for batch_start in range(0, len(texts), self.max_batch_items):
            batch = texts[batch_start : batch_start + self.max_batch_items]
            request_items = [
                {
                    "request_id": f"p3-sidecar-{run_id}-{batch_start + index:04d}",
                    "trace_id": uuid.uuid4().hex,
                    "tenant_id": self.tenant_id,
                    "source_type": self.source_type,
                    "source_id": f"p3-sidecar-source-{run_id}",
                    "chunk_id": f"p3-sidecar-input-{run_id}-{batch_start + index:04d}",
                    "chunk_text": text,
                    "embedding_required": True,
                    "input_type": input_type,
                    "metadata": {"integration": "p3-runtime"},
                }
                for index, text in enumerate(batch)
            ]
            client = await self._http_client()
            try:
                response = await client.post("/v1/intercept", json={"items": request_items})
            except Exception as exc:
                raise SidecarEmbeddingError(f"B1 Sidecar embedding request failed: {exc}") from exc
            payload = self._response_json(response)
            if response.status_code != 200:
                raise SidecarEmbeddingError(
                    f"B1 Sidecar returned HTTP {response.status_code}: {payload}"
                )
            results = payload.get("results")
            if not isinstance(results, list) or len(results) != len(batch):
                raise SidecarEmbeddingError("B1 Sidecar returned an unexpected result count")
            for result in results:
                output.append(self._extract_vector(result))
        return output

    def _extract_vector(self, result: Any) -> list[float]:
        if not isinstance(result, dict):
            raise SidecarEmbeddingError("B1 Sidecar returned a non-object result")
        if result.get("status") != "success":
            raise SidecarEmbeddingError(
                f"B1 Sidecar item failed: {result.get('error_code')}: "
                f"{result.get('error_message')}"
            )
        chunks = result.get("chunks")
        if not isinstance(chunks, list) or len(chunks) != 1:
            raise SidecarEmbeddingError(
                "B1 Sidecar returned multiple chunks for one pipeline chunk; "
                "align the Sidecar and pipeline chunk limits"
            )
        vector = chunks[0].get("vector") if isinstance(chunks[0], dict) else None
        if not isinstance(vector, list) or not vector:
            raise SidecarEmbeddingError("B1 Sidecar returned an empty vector")
        if not all(
            isinstance(value, (int, float)) and math.isfinite(float(value)) for value in vector
        ):
            raise SidecarEmbeddingError("B1 Sidecar returned a non-finite vector")
        values = [float(value) for value in vector]
        expected = result.get("embedding_dim") or self._dimension
        if not isinstance(expected, int) or len(values) != expected:
            raise SidecarEmbeddingError("B1 Sidecar returned an inconsistent vector dimension")
        if self._dimension is None:
            self._dimension = expected
        if len(values) != self._dimension:
            raise SidecarEmbeddingError("B1 Sidecar vector dimension changed during a request")
        return values

    @staticmethod
    def _response_json(response: Any) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception as exc:
            raise SidecarEmbeddingError("B1 Sidecar returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise SidecarEmbeddingError("B1 Sidecar returned a non-object JSON payload")
        return payload
