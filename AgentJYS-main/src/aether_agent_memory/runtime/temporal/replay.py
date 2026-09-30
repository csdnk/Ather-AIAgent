"""Offline upgrade check. SDK replay never runs Activities or opens a Client."""

from collections.abc import Sequence
from pathlib import Path

from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer


async def replay_histories(
    paths: Sequence[Path], workflows: Sequence[type]
) -> list[dict[str, str]]:
    failures = []
    replayer = Replayer(workflows=workflows, data_converter=pydantic_data_converter)
    for path in paths:
        try:
            history = WorkflowHistory.from_json(path.stem, path.read_text("utf-8"))
            await replayer.replay_workflow(history)
        except Exception as exc:
            failures.append(
                {
                    "history": str(path),
                    "reason_code": "HISTORY_INCOMPATIBLE",
                    "error_type": type(exc).__name__,
                }
            )
    return failures
