"""Compatibility startup: P3 runs through the unified Temporal service."""

from aether_agent_memory.runtime.flows.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
