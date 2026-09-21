from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AETHER_B2_", extra="ignore")

    default_ttl_seconds: int = 3600
    max_items_per_session: int = 200
    archive_batch_size: int = 50
    default_budget_tokens: int = 4096
    max_candidates: int = 100
    half_life_hours: float = 168.0
    b1_grpc: str = "localhost:50051"
    p2_grpc: str = "localhost:50052"
    otel_exporter: str = "localhost:4317"
    b1_model_name: str = "BAAI/bge-small-zh-v1.5"
    b1_chunk_max_chars: int = 1200
    b1_chunk_overlap_chars: int = 120
    b3_interval_seconds: float = 30.0
    b3_promote_threshold: float = 0.72
    b3_demote_threshold: float = 0.28

    @classmethod
    def from_toml(cls, path: str | Path) -> Settings:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return cls(
            default_ttl_seconds=data["working_memory"]["default_ttl_seconds"],
            max_items_per_session=data["working_memory"]["max_items_per_session"],
            archive_batch_size=data["episodic_memory"]["archive_batch_size"],
            default_budget_tokens=data["context_pack"]["default_budget_tokens"],
            max_candidates=data["context_pack"]["max_candidates"],
            half_life_hours=data["decay"]["half_life_hours"],
            b1_grpc=data["endpoints"]["b1_grpc"],
            p2_grpc=data["endpoints"]["p2_grpc"],
            otel_exporter=data["endpoints"]["otel_exporter"],
            b1_model_name=data.get("b1", {}).get("model_name", "BAAI/bge-small-zh-v1.5"),
            b1_chunk_max_chars=data.get("b1", {}).get("chunk_max_chars", 1200),
            b1_chunk_overlap_chars=data.get("b1", {}).get("chunk_overlap_chars", 120),
            b3_interval_seconds=data.get("b3", {}).get("interval_seconds", 30.0),
            b3_promote_threshold=data.get("b3", {}).get("promote_threshold", 0.72),
            b3_demote_threshold=data.get("b3", {}).get("demote_threshold", 0.28),
        )
