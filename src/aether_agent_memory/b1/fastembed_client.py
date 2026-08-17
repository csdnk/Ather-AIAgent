import asyncio
from importlib import import_module
from typing import Any


class FastEmbedClient:
    """Optional CPU ONNX embedding adapter; importing the package remains optional."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        *,
        cache_dir: str | None = None,
        threads: int | None = None,
    ) -> None:
        try:
            module = import_module("fastembed")
        except ImportError as exc:  # pragma: no cover - depends on optional installation
            raise RuntimeError(
                "FastEmbed is not installed. Install with: pip install -e '.[b1-real]'"
            ) from exc
        model_class: Any = module.TextEmbedding
        kwargs: dict[str, Any] = {"model_name": model_name}
        if cache_dir is not None:
            kwargs["cache_dir"] = cache_dir
        if threads is not None:
            kwargs["threads"] = threads
        self.model_name = model_name
        self._model: Any = model_class(**kwargs)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._embed_passages, texts)

    async def embed_one(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._embed_query, text)

    def _embed_passages(self, texts: list[str]) -> list[list[float]]:
        method = getattr(self._model, "passage_embed", self._model.embed)
        return [vector.tolist() for vector in method(texts)]

    def _embed_query(self, text: str) -> list[float]:
        method = getattr(self._model, "query_embed", self._model.embed)
        vector: Any = next(iter(method([text])))
        return [float(value) for value in vector.tolist()]
