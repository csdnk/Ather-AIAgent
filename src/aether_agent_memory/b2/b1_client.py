"""B2 adapter for the B1 Sidecar embedding boundary."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class B1EmbeddingServiceError(RuntimeError):
    """B1 rejected a request or returned an incompatible response."""


class B1EmbeddingUnavailableError(B1EmbeddingServiceError):
    """B1 could not be reached or has not finished loading its model."""


class B1EmbeddingServiceClient:
    """Translate B2's stable record contract to the B1 Sidecar protocol."""

    def __init__(
        self,
        endpoint: str,
        *,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        normalized = endpoint.strip()
        if not normalized:
            raise ValueError("B1 embedding endpoint must not be empty")
        self._endpoint = normalized
        self._opener = opener

    def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        sidecar_payload = self._to_sidecar_payload(payload)
        request = Request(
            self._endpoint,
            data=json.dumps(sidecar_payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with self._opener(request, timeout=60) as response:
                raw = json.loads(response.read())
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            message = f"B1 returned HTTP {exc.code}: {detail}"
            if exc.code >= 500:
                raise B1EmbeddingUnavailableError(message) from exc
            raise B1EmbeddingServiceError(message) from exc
        except (URLError, OSError) as exc:
            raise B1EmbeddingUnavailableError(f"B1 request failed: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise B1EmbeddingServiceError("B1 returned invalid JSON") from exc
        return self._to_b2_result(payload, raw)

    @staticmethod
    def _to_sidecar_payload(payload: dict[str, Any]) -> dict[str, Any]:
        required = ("text", "source_type", "request_id", "tenant_id", "source_id")
        missing = [name for name in required if not str(payload.get(name, "")).strip()]
        if missing:
            raise ValueError(f"missing B2/B1 fields: {', '.join(missing)}")
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        metadata = dict(metadata)
        memory_id = payload.get("memory_id")
        if memory_id is not None:
            metadata["memory_id"] = str(memory_id)
        body: dict[str, Any] = {
            "text": str(payload["text"]),
            "source_type": str(payload["source_type"]),
            "request_id": str(payload["request_id"]),
            "tenant_id": str(payload["tenant_id"]),
            "source_id": str(payload["source_id"]),
            "metadata": metadata,
            "embedding_required": bool(payload.get("embedding_required", True)),
            "input_type": str(payload.get("input_type", "passage")),
        }
        for name in ("trace_id", "object_id", "chunk_id"):
            value = payload.get(name)
            if value is not None and str(value).strip():
                body[name] = str(value)
        return body

    @classmethod
    def _to_b2_result(cls, request: dict[str, Any], raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise B1EmbeddingServiceError("B1 returned a non-object JSON payload")
        results = raw.get("results")
        if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
            raise B1EmbeddingServiceError("B1 returned an unexpected result envelope")
        result = results[0]
        if result.get("status") != "success":
            code = str(result.get("error_code") or "B1_FAILED")
            message = str(result.get("error_message") or code)
            if code in {"B1_MODEL_NOT_READY", "B1_BUSY", "B1_EMBEDDING_TIMEOUT"}:
                raise B1EmbeddingUnavailableError(f"{code}: {message}")
            raise B1EmbeddingServiceError(f"{code}: {message}")
        chunks = result.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            raise B1EmbeddingServiceError("B1 returned no chunks")
        source_text = str(request["text"])
        embedding_model = str(result.get("embedding_model") or "b1-sidecar")
        object_id = result.get("object_id") or request.get("object_id")
        records = [
            cls._record_from_chunk(
                chunk=chunk,
                request=request,
                source_text=source_text,
                embedding_model=embedding_model,
                object_id=str(object_id) if object_id is not None else None,
            )
            for chunk in chunks
        ]
        return {
            "status": "success",
            "request_id": str(result.get("request_id") or request["request_id"]),
            "trace_id": str(result.get("trace_id") or request.get("trace_id") or ""),
            "source_id": str(result.get("source_id") or request["source_id"]),
            "records": records,
        }

    @staticmethod
    def _record_from_chunk(
        *,
        chunk: Any,
        request: dict[str, Any],
        source_text: str,
        embedding_model: str,
        object_id: str | None,
    ) -> dict[str, Any]:
        if not isinstance(chunk, dict):
            raise B1EmbeddingServiceError("B1 returned a non-object chunk")
        chunk_id = chunk.get("chunk_id")
        vector = chunk.get("vector")
        start = chunk.get("start_char")
        end = chunk.get("end_char")
        if not isinstance(chunk_id, str) or not chunk_id:
            raise B1EmbeddingServiceError("B1 returned a chunk without chunk_id")
        if not isinstance(vector, list) or not vector or not all(
            isinstance(value, (int, float)) and math.isfinite(float(value)) for value in vector
        ):
            raise B1EmbeddingServiceError("B1 returned an invalid embedding vector")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start:
            raise B1EmbeddingServiceError("B1 returned invalid chunk offsets")
        chunk_text = chunk.get("chunk_text")
        if not isinstance(chunk_text, str):
            chunk_text = source_text[start:end]
        if not chunk_text:
            raise B1EmbeddingServiceError("B1 returned an empty chunk")
        return {
            "chunk_id": chunk_id,
            "chunk_text": chunk_text,
            "vector": [float(value) for value in vector],
            "object_id": object_id,
            "embedding_model": embedding_model,
            "metadata": {
                "chunk_index": chunk.get("chunk_index", 0),
                "start_char": start,
                "end_char": end,
                "session_id": request.get("metadata", {}).get("session_id"),
                "agent_id": request.get("metadata", {}).get("agent_id"),
            },
        }
