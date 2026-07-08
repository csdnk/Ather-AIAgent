from __future__ import annotations

import argparse
import sys
from importlib.util import find_spec
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

if find_spec("textual") is None:
    sys.stderr.write(
        "\n[demo] 缺少可选依赖 `textual`，TUI 演示需要它来渲染分屏界面。\n"
        "请先安装：\n\n"
        "    uv sync --group demo\n\n"
        "然后重新运行：\n\n"
        "    uv run python examples/minimal_loop/main.py\n\n"
    )
    raise SystemExit(1)

from tui_app import P3DataflowApp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="P3 数据流框架分屏交互式 TUI 演示",
    )
    parser.add_argument(
        "--step-delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="每个步骤之间的延迟秒数（默认 1.0；0 表示无延迟，方便调试）",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app = P3DataflowApp(step_delay=args.step_delay)
    app.run()
