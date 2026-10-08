import pytest

from aether_platform.operations.quotas import QuotaExceededError, check_limits


def test_concurrency_and_daily_request_limits_are_enforced():
    with pytest.raises(QuotaExceededError):
        check_limits({"concurrent_turns": 2}, {"pending": 2, "requests": 0, "tokens": 0}, True)
    with pytest.raises(QuotaExceededError):
        check_limits({"daily_requests": 10}, {"pending": 0, "requests": 10, "tokens": 0}, True)
    check_limits({"daily_requests": 10}, {"pending": 0, "requests": 10, "tokens": 0}, False)


def test_observed_token_budget_stops_further_admission_without_claiming_exact_cap():
    with pytest.raises(QuotaExceededError):
        check_limits(
            {"observed_token_budget": 100}, {"pending": 0, "requests": 2, "tokens": 100}, True
        )
    check_limits({}, {"pending": 100, "requests": 100000, "tokens": 1000000}, True)
