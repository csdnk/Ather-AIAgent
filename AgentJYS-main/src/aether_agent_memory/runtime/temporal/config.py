"""Explicit deployment identity and transport configuration, without secret payloads."""

from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class TemporalConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deployment_id: Name
    endpoint: NonBlank
    namespace: NonBlank = "default"
    task_queue_prefix: Name = "p3"
    tls_ca_file: Path | None = None
    tls_cert_file: Path | None = None
    tls_key_file: Path | None = None
    connect_timeout_seconds: float = Field(default=10, gt=0, le=60, allow_inf_nan=False)

    @model_validator(mode="after")
    def tls_pair(self) -> Self:
        if (self.tls_cert_file is None) != (self.tls_key_file is None):
            raise ValueError("Temporal client certificate and key must be configured together")
        return self


def deployment_configuration(config: TemporalConfiguration) -> TemporalConfiguration:
    """The same durable queue identity for service, migration and local tools."""
    from aether_agent_memory.runtime.foundation.common import fingerprint

    prefix = config.task_queue_prefix
    digest = fingerprint([prefix, config.deployment_id])[:24]
    return config.model_copy(update={"task_queue_prefix": f"{prefix[:24]}.{digest}"})
