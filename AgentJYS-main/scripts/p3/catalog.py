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
        module = import_module(f"aether_agent_memory.{flow}.contracts.models")
        for name, value in vars(module).items():
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value.__module__ == module.__name__
                and name != "ContractModel"
            ):
                result[f"{flow}.{name}"] = value
    return result


def schema_path(key: str) -> Path:
    return ROOT / "contracts/p3/schemas" / f"{key}.json"
