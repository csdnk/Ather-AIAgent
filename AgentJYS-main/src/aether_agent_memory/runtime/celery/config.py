"""Explicit broker identity. Credentials never enter task messages."""

import os

from pydantic import BaseModel, ConfigDict, Field


class CeleryConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    broker_url_env: str = "P3_CELERY_BROKER_URL"
    queue_prefix: str = "p3.remember"
    repair_seconds: float = Field(default=30, gt=0, le=300)
    lease_grace_seconds: float = Field(default=5, gt=0, le=60)
    visibility_timeout: int = Field(default=7200, ge=3660)
    batch_size: int = Field(default=64, ge=1, le=1000)

    def broker_url(self) -> str:
        value = os.environ.get(self.broker_url_env)
        if not value:
            raise ValueError("configured Celery broker credential is missing")
        return value
