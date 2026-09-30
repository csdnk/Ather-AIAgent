"""Explicit stage routes and side-effect policy. Missing routes never fall back to RF."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from aether_agent_memory.runtime.contracts.models import Permission

from .models import StagePolicy, StepRequest, StepResult

StageHandler = Callable[[StepRequest], Awaitable[StepResult]]


@dataclass(frozen=True)
class Stage:
    execute: StageHandler
    reconcile: StageHandler
    permission: Permission
    policy: StagePolicy


class StageRegistry:
    def __init__(self) -> None:
        self.routes: dict[str, dict[str, Stage]] = {}

    def register(
        self,
        kind: str,
        stage: str,
        execute: StageHandler,
        reconcile: StageHandler,
        permission: Permission,
        effect_mode: Literal["read", "idempotent", "uncertain"],
        *,
        timeout_seconds: float = 30,
    ) -> None:
        stages = self.routes.setdefault(kind, {})
        if not kind or not stage or stage in stages:
            raise ValueError("empty or duplicate stage registration")
        stages[stage] = Stage(
            execute,
            reconcile,
            permission,
            StagePolicy(effect_mode=effect_mode, timeout_seconds=timeout_seconds),
        )

    def get(self, kind: str, stage: str) -> Stage:
        try:
            return self.routes[kind][stage]
        except KeyError:
            raise ValueError("unregistered workflow stage") from None

    def policies(self, kind: str) -> dict[str, StagePolicy]:
        if kind not in self.routes:
            raise ValueError("unregistered workflow kind")
        return {name: stage.policy for name, stage in self.routes[kind].items()}

    def coverage_manifest(self) -> dict[str, str]:
        from .workflows import workflow_name

        return {
            kind: workflow_name(kind) + ":" + ",".join(stages)
            for kind, stages in sorted(self.routes.items())
        }

    def validate_catalog(self, kinds: set[str]) -> None:
        missing = kinds - self.routes.keys()
        if missing:
            raise ValueError(
                "registered tasks without Temporal stages: " + ", ".join(sorted(missing))
            )
        if self.routes.keys() - kinds or any(not stages for stages in self.routes.values()):
            raise ValueError("Temporal stages differ from the registered business catalog")
