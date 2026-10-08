import math
from collections.abc import Mapping


def parse_usage(value: object) -> dict[str, int] | None:
    if value is None:
        return None
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    if not isinstance(value, dict):
        raise ValueError("Invalid model usage")
    usage: dict[str, int] = {}
    for key in keys:
        count = value.get(key)
        if type(count) is not int or count < 0:
            raise ValueError("Invalid model usage")
        usage[key] = count
    if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
        raise ValueError("Inconsistent model usage")
    return usage


def estimate_cost(
    usage: Mapping[str, int] | None, rates: Mapping[str, object] | None
) -> float | None:
    if usage is None or rates is None:
        return None
    values: list[int | float] = []
    for key in ("input_per_million", "output_per_million"):
        rate = rates.get(key)
        if type(rate) is not int and type(rate) is not float:
            return None
        if not math.isfinite(rate) or rate < 0:
            return None
        values.append(rate)
    return (usage["prompt_tokens"] * values[0] + usage["completion_tokens"] * values[1]) / 1000000
