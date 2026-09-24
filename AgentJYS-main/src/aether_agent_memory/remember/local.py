"""Explicit Remember runtime composition; existing Recall and Operate are reused."""

from pathlib import Path
from typing import Any, cast

from aether_agent_memory.recall.basic.adapters import LexicalEmbedding
from aether_agent_memory.recall.basic.tokenization import ModelTokenizer
from aether_agent_memory.recall.basic.vector_search import SearchAccess
from aether_agent_memory.recall.contracts.foundation import EmbeddingSpace
from aether_agent_memory.runtime.flows.host import ThreeFlows
from aether_agent_memory.runtime.foundation.common import fingerprint

from .basic.boundary import RememberBoundary
from .basic.content import Bodies, RedisBodyCache
from .basic.pipeline import RememberPipeline
from .basic.policy import RememberPolicy
from .basic.reflection import Reflection
from .basic.retention import Retention
from .basic.sqlite_p2 import SQLiteP2


class RememberFactory:
    def __init__(
        self,
        database: str | Path,
        *,
        policy: RememberPolicy | None = None,
        body_root: str | Path | None = None,
        p2: Any = None,
        redis: Any = None,
        generation_search: Any = None,
        space: EmbeddingSpace | None = None,
        **providers: Any,
    ) -> None:
        self.policy = policy or RememberPolicy()
        self.p2 = p2 if p2 is not None else SQLiteP2(str(database) + ".p2.db")
        self.root = Path(body_root) if body_root else Path(str(database) + ".bodies")
        self.redis, self.providers = redis, providers
        self.generation_search, self.space = generation_search, space

    def __call__(self, *args: Any) -> RememberPipeline:
        bodies = Bodies(
            self.root,
            self.policy,
            p2=self.p2,
            cache=RedisBodyCache(self.redis, self.policy) if self.redis is not None else None,
        )
        binding = {
            "provider": "p2",
            "endpoint": fingerprint(
                [getattr(self.p2, "endpoint", None), getattr(self.p2, "bucket", None)]
            ),
            "replica_root": str(self.root.resolve()),
        }
        with args[0].transaction() as tx:
            prior = tx.read("settings", "remember_body_binding")
            if prior is not None and prior != binding:
                raise ValueError("Remember body provider changed; explicit migration required")
            tx.write("settings", "remember_body_binding", binding)
        return RememberPipeline(
            *args,
            bodies=bodies,
            policy=self.policy,
            tokenizer=ModelTokenizer("cl100k_base"),
            **self.providers,
        )

    def attach(self, host: ThreeFlows) -> None:
        remember = cast(RememberPipeline, host.remember)
        remember.tokenizer = host.recall.tokenizer
        remember.vector_search = SearchAccess(host.vectors)
        remember.retention, remember.reflection = Retention(remember), Reflection(remember)
        host.foundation.events.subscribe(
            "recall.access", "remember_retention", remember.retention.consume
        )
        boundary = RememberBoundary(remember)
        space = self.space
        if host.native_embedding:
            space = host.native_embedding.space
            backend = host.native_embedding.backends["Passage"]
            remember.embedding_count = lambda text: backend.count_tokens(text, "Passage")
            remember.embedding_tokenizer_id = "native_passage"
            if self.policy.projection_chunk_tokens > backend.max_input_tokens:
                raise ValueError("Remember chunks exceed the passage model budget")
        elif space is None and isinstance(host.embedding, LexicalEmbedding):
            space = EmbeddingSpace(
                model_space=host.model_space,
                model_id="local_lexical",
                model_revision="v1",
                dimensions=256,
                tokenizer_id=host.recall.tokenizer.identifier,
                query_prefix="",
                passage_prefix="",
                normalization="unit",
                metric="inner_product",
                max_input_tokens=self.policy.projection_chunk_tokens,
            )
        if space is None:
            raise ValueError("injected embedding needs an explicit EmbeddingSpace")
        if host.owned_vectors is not None and self.generation_search is None:
            raise ValueError("non-SQLite vectors need a generation search provider")
        host.enable_generation_recall(
            memories=remember,
            qualification=boundary,
            bodies=cast(Any, boundary),
            guards=boundary,
            space=space,
            search=self.generation_search,
        )


def create_runtime(
    database: str | Path,
    cache_root: str | Path,
    *,
    remember_policy: RememberPolicy | None = None,
    body_root: str | Path | None = None,
    p2: Any = None,
    redis: Any = None,
    comparison: Any = None,
    equivalence_verifier: Any = None,
    support_verifier: Any = None,
    compressor: Any = None,
    compression_quality: Any = None,
    documents: Any = None,
    summarizer: Any = None,
    generation_search: Any = None,
    space: EmbeddingSpace | None = None,
    **host_options: Any,
) -> ThreeFlows:
    """Opt-in factory. The default ThreeFlows constructor remains unchanged in behavior."""
    return ThreeFlows(
        database,
        cache_root,
        remember_factory=RememberFactory(
            database,
            policy=remember_policy,
            body_root=body_root,
            p2=p2,
            redis=redis,
            comparison=comparison,
            equivalence_verifier=equivalence_verifier,
            support_verifier=support_verifier,
            compressor=compressor,
            quality=compression_quality,
            documents=documents,
            summarizer=summarizer,
            generation_search=generation_search,
            space=space,
        ),
        **host_options,
    )
