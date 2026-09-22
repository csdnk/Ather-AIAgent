"""Discover public contract models without importing application adapters."""

import sys
from importlib import import_module
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
FLOWS = ("runtime", "remember", "recall", "operate")


def models() -> dict[str, type[BaseModel]]:
    result = {}
    for flow in FLOWS:
        for source in ("models", "foundation"):
            module = import_module(f"aether_agent_memory.{flow}.contracts.{source}")
            for name, value in vars(module).items():
                if (
                    isinstance(value, type)
                    and issubclass(value, BaseModel)
                    and value.__module__ == module.__name__
                    and name != "ContractModel"
                ):
                    key = f"{flow}.{name}"
                    if key in result:
                        raise ValueError(f"duplicate public contract: {key}")
                    result[key] = value
    return result


def schema_path(key: str) -> Path:
    return ROOT / "contracts/p3/schemas" / f"{key}.json"
