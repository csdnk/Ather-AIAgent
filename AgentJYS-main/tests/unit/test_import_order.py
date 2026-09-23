"""公共入口必须能在干净进程中独立加载，不能依赖其他测试预热模块。"""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "aether_agent_memory.session.models",
        "aether_agent_memory.context_store",
        "examples.p3_closed_loop",
    ],
)
def test_public_entrypoint_imports_in_fresh_process(module: str) -> None:
    # 必须另起进程；同一 pytest 进程中的模块缓存会掩盖循环导入。
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
