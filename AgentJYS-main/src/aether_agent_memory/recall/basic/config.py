"""Server-owned Recall deployment policy; never read policy from a query body."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RecallSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tokenizer: str = "o200k_base"
    tokenizer_path: str | None = None
    candidate_limit: int = Field(default=20, ge=1, le=100)
    max_items: int = Field(default=5, ge=1, le=20)
    max_discovery: int = Field(default=100, ge=20, le=1000)
    source_timeout_seconds: float = Field(default=10, gt=0, le=120)
    reranker_model: str | None = None
    reranker_revision: str | None = None
    reranker_cache: str | None = None
    rerank_policy: Literal["disabled", "required", "fallback"] = "disabled"
    rerank_timeout_seconds: float = Field(default=5, gt=0, le=120)
    rerank_max_length: int = Field(default=512, ge=32, le=8192)
    milvus_uri: str | None = None
    milvus_collection: str = "p3_memories"
    milvus_token_env: str = "P3_MILVUS_TOKEN"

    @model_validator(mode="after")
    def consistent(self) -> "RecallSettings":
        if self.rerank_policy != "disabled" and not self.reranker_model:
            raise ValueError("enabled reranking requires a model")
        if self.rerank_policy == "disabled" and self.reranker_model:
            raise ValueError("configured reranker requires an explicit enabled policy")
        if self.max_discovery < self.candidate_limit:
            raise ValueError("discovery budget must cover candidate limit")
        if self.tokenizer == "huggingface" and not self.tokenizer_path:
            raise ValueError("huggingface tokenizer requires a local tokenizer.json")
        return self
