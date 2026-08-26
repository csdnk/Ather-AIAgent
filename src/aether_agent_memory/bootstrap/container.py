"""Composition root for the P3 application host."""

from __future__ import annotations

from aether_agent_memory.config.app_settings import AppSettings
from aether_agent_memory.runtime.dependencies import RuntimeProfile
from aether_agent_memory.runtime.legacy import P3RuntimeConfig
from aether_agent_memory.runtime.service import MemoryRuntime


def build_runtime(settings: AppSettings) -> MemoryRuntime:
    """Build the long-lived P3 runtime from resolved application settings."""
    settings.validate_for_profile()
    return MemoryRuntime.from_config(
        P3RuntimeConfig.from_settings(settings),
        profile=RuntimeProfile(settings.profile),
    )
