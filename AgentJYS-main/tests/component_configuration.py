"""Controlled model configuration with explicitly injected real Azure storage."""

from typing import Literal, Self

from pydantic import model_validator

from aether_agent_memory.runtime.flows.config import ServiceConfiguration


class ComponentConfiguration(ServiceConfiguration):
    profile: Literal["local", "production"] = "local"  # type: ignore[assignment]
    storage_mode: Literal["component"] = "component"  # type: ignore[assignment]
    metadata_backend: Literal["postgresql"] = "postgresql"
    embedding_profile: Literal["native", "injected"] = "injected"  # type: ignore[assignment]
    postgres_dsn_env: str = "AETHER_POSTGRES_DSN"

    @model_validator(mode="after")
    def production_dependencies(self) -> Self:
        if self.profile == "production":
            super().production_dependencies()
        return self
