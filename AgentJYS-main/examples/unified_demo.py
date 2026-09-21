"""Run the complete local Aether demo and print every internal flow node."""

from __future__ import annotations

import asyncio

from aether_agent_memory.demo import UnifiedDemoRunner


async def main() -> None:
    report = await UnifiedDemoRunner().run()
    print("Aether unified project demo (local P2 simulation)")
    for event in report.events:
        print(
            f"[{event.sequence:02}] {event.node:<16} | {event.action:<32} | "
            f"{event.status.upper():<7} | {event.detail}"
        )


if __name__ == "__main__":
    asyncio.run(main())
