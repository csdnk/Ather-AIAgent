"""Explicit Remember runtime composition; existing Recall and Operate are reused."""

from pathlib import Path
from typing import Any, cast

from aether_agent_memory.recall.basic.tokenization import TokenCounter
from aether_agent_memory.recall.basic.vector_search import SearchAccess
from aether_agent_memory.recall.contracts.foundation import EmbeddingSpace
from aether_agent_memory.runtime.flows.host import ThreeFlows
from aether_agent_memory.runtime.foundation.common import fingerprint
from aether_agent_memory.runtime.storage.cache import BodyCache, CacheLocationReader

from .basic.boundary import RememberBoundary
from .basic.content import Bodies
from .basic.pipeline import RememberPipeline
from .basic.policy import RememberPolicy
from .basic.reflection import Reflection
from .basic.retention import Retention


class RememberFactory:
    def __init__(
        self,
        database: str | Path,
        *,
        policy: RememberPolicy | None = None,
        body_root: str | Path | None = None,
        p2: Any = None,
        redis: Any = None,
        body_cache: BodyCache | None = None,
        cache_reader: CacheLocationReader | None = None,
        generation_search: Any = None,
        space: EmbeddingSpace | None = None,
        **providers: Any,
    ) -> None:
        self.policy = policy or RememberPolicy()
        if redis is not None:
            raise ValueError("the retired redis argument is unavailable; pass explicit body_cache")
        if p2 is None:
            raise ValueError("Remember requires an explicit object provider")
        self.p2 = p2
        self.root = Path(body_root) if body_root else Path(str(database) + ".bodies")
        self.providers = providers
        self.body_cache = body_cache
        self.cache_reader = cache_reader
        self.generation_search, self.space = generation_search, space

    def __call__(self, *args: Any, tokenizer: TokenCounter) -> RememberPipeline:
        bodies = Bodies(
            self.root,
            self.policy,
            p2=self.p2,
            cache=self.body_cache,
            cache_reader=self.cache_reader,
        )
        bodies.remote_only = self.p2 is not None
        bodies.require_prepared = bodies.remote_only
        binding = {
            "provider": "p2",
            "endpoint": fingerprint(
                [getattr(self.p2, "endpoint", None), getattr(self.p2, "bucket", None)]
            ),
            "binding_version": 2,
            "authority": "p2",
        }
        object_binding = getattr(self.p2, "binding", None)
        if callable(object_binding):
            binding = {
                **object_binding(),
                "binding_version": 3,
                "authority": "objects",
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
            tokenizer=tokenizer,
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
            remember.embedding_tokenizer_id = space.tokenizer_id
            if self.policy.projection_chunk_tokens > backend.max_input_tokens:
                raise ValueError("Remember chunks exceed the passage model budget")
        elif space is None:
            space = getattr(host.embedding, "space", None)
        if space is None:
            raise ValueError("injected embedding needs an explicit EmbeddingSpace")
        remember.embedding_tokenizer_id = space.tokenizer_id
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
    body_cache: BodyCache | None = None,
    cache_reader: CacheLocationReader | None = None,
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
    """Compose Remember, Recall and Operate with explicit providers."""
    return ThreeFlows(
        database,
        cache_root,
        remember_factory=RememberFactory(
            database,
            policy=remember_policy,
            body_root=body_root,
            p2=p2,
            redis=redis,
            body_cache=body_cache,
            cache_reader=cache_reader,
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
