import math


def parse_usage(value):
    if value is None:
        return None
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    if not isinstance(value, dict) or any(
        type(value.get(key)) is not int or value[key] < 0 for key in keys
    ):
        raise ValueError("Invalid model usage")
    if value["total_tokens"] != value["prompt_tokens"] + value["completion_tokens"]:
        raise ValueError("Inconsistent model usage")
    return {key: value[key] for key in keys}


def estimate_cost(usage, rates):
    if usage is None or rates is None:
        return None
    values = [rates.get(key) for key in ("input_per_million", "output_per_million")]
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values):
        return None
    return (usage["prompt_tokens"] * values[0] + usage["completion_tokens"] * values[1]) / 1000000
