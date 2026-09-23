"""Subprocess fault fixture; not an application entry point."""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from aether_agent_memory.runtime.foundation.host import Foundation  # noqa: E402


async def run():
    database, mode, marker = sys.argv[1:]
    app = Foundation(database)
    # 故障点不能依赖亚秒级调度；父进程会在 kill 后推进恢复时钟。
    app.tasks.lease_seconds = 30
    try:
        if mode == "claim":
            app.tasks.lease_seconds = 30
            task = app.tasks.claim(
                "child_" + Path(marker).stem, "engineering", "1900-01-01T00:00:00.000Z"
            )
            Path(marker).write_text(json.dumps(None if task is None else task.task_id))
            return
        original = app.sample.run

        async def blocked(ctx, task):
            if mode == "before_commit":
                with app.uow.transaction() as tx:
                    app.tasks.guard(tx, task)
                    # Deliberately hold an uncommitted write while the process is killed.
                    tx.write("fault", "uncommitted", {"must_rollback": True})
                    Path(marker).write_text(task.task_id)
                    await asyncio.sleep(120)
            elif mode == "after_commit":
                result = await original(ctx, task)
                Path(marker).write_text("committed")
                await asyncio.sleep(120)
                return result
            else:
                Path(marker).write_text(task.task_id)
                await asyncio.sleep(120)

        app.sample.run = blocked
        await app.tasks.run_once("crash_worker", "engineering")
    finally:
        app.close()


asyncio.run(run())
