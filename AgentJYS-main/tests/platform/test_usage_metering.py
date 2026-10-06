import pytest

from aether_platform.operations.metering import estimate_cost, parse_usage


def test_missing_usage_is_unknown_not_zero():
    assert parse_usage(None) is None
    assert estimate_cost(None, {"input_per_million": 1, "output_per_million": 2}) is None


def test_usage_is_strict_and_keeps_no_provider_extra_fields():
    assert parse_usage(
        {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40, "secret": "x"}
    ) == {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}
    for value in (-1, True, "10"):
        with pytest.raises(ValueError):
            parse_usage({"prompt_tokens": value, "completion_tokens": 1, "total_tokens": 2})


def test_cost_requires_explicit_rates_and_uses_both_directions():
    usage = {"prompt_tokens": 1000000, "completion_tokens": 500000, "total_tokens": 1500000}
    assert estimate_cost(usage, None) is None
    assert estimate_cost(usage, {"input_per_million": 2, "output_per_million": 4}) == 4
