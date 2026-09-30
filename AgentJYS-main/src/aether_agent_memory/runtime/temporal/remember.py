"""Complete Remember background catalog; each route has an explicit effect boundary."""

from typing import Literal

from aether_agent_memory.remember.basic.pipeline import RememberPipeline
from aether_agent_memory.runtime.contracts.models import Permission

from .registry import StageRegistry


def register_remember(registry: StageRegistry, remember: RememberPipeline) -> None:
    from aether_agent_memory.remember.basic.temporal_background import BackgroundStages

    stages = BackgroundStages(remember)
    remember.bodies.require_prepared = True
    for suffix in (
        "extract",
        "project",
        "cleanup",
        "compress",
        "distill",
        "revalidate",
        "summarize",
    ):
        kind = "remember." + suffix
        permission = Permission.DELETE if suffix == "cleanup" else Permission.WRITE
        phases: tuple[tuple[str, Literal["read", "idempotent", "uncertain"]], ...] = (
            ("prepare", "read"),
            ("generate", "read"),
            ("publish", "uncertain"),
            ("commit", "idempotent"),
        )
        for name, mode in phases:
            registry.register(
                kind,
                name,
                stages.handler(name),
                stages.handler(name, reconcile=True),
                permission,
                mode,
                timeout_seconds=300,
            )
