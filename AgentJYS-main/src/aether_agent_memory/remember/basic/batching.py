"""Whole-message admission units, independent from model token windows."""

from typing import Any

from .policy import RememberPolicy


def select_batch(
    rows: list[tuple[str, dict[str, Any]]], policy: RememberPolicy
) -> list[tuple[str, dict[str, Any]]]:
    """Include the threshold-crossing message in full, even when it is oversized."""
    selected = []
    size = 0
    for entry in rows:
        selected.append(entry)
        size += entry[1]["bytes"]
        if len(selected) >= policy.consolidation_messages or size >= policy.consolidation_bytes:
            break
    return selected


def source_descriptor(source_id: str, version: int, byte_size: int) -> str:
    return (
        f"已保存长内容；来源 {source_id}@{version}；UTF-8 大小 {byte_size} 字节。\n"
        "此 Working 记录只含来源引用；全文请通过来源读取。"
    )
