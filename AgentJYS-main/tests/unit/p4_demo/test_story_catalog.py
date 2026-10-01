"""The public picker offers five stories without accepting arbitrary scripts."""

import pytest
from pydantic import ValidationError

from aether_p4_simulator.demo.models import StartRequest
from aether_p4_simulator.demo.service import DemoService


def test_public_catalog_offers_five_complete_stories():
    service = DemoService(None)
    try:
        catalog = service.list_scenarios()
        assert {item["id"] for item in catalog["items"]} == {
            "library-full",
            "weather-weekend",
            "preference-update",
            "learning-review",
            "forget-sources",
        }
        assert all(item["total_steps"] >= 7 for item in catalog["items"])
    finally:
        service.close()


def test_unknown_story_or_extra_script_cannot_be_started():
    with pytest.raises(ValidationError):
        StartRequest(scenario_id="custom", request_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    with pytest.raises(ValidationError):
        StartRequest(
            scenario_id="library-full",
            request_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            script=[{"url": "http://untrusted"}],
        )
