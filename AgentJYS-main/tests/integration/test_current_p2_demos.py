"""The public scenarios use real current-P2 objects/vectors and real Temporal.

Model outputs, metadata and cache remain controlled reference components; this
does not certify the formal P2 database implementation or model quality.
"""

from pathlib import Path

import pytest

from azure_component_service import Service

from .test_current_p2_http import configuration as configuration


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_name", ["flows", "summary"])
async def test_configured_scenario_uses_actual_current_p2(
    configuration, tmp_path, monkeypatch, scenario_name
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts/p3"))
    from demo_flows import scenario as flow_scenario
    from demo_remember_summary import scenario as summary_scenario
    from demo_support import DemoRun

    from summary_demo_models import ModelDouble

    config = configuration.model_copy(
        update={
            "remember": configuration.remember.model_copy(
                update={
                    "working_summary_min_bytes": 512,
                    "working_summary_max_chars": 256,
                    "source_page_chars": 256,
                }
            )
        }
    )
    service = Service(config)
    if scenario_name == "summary":
        service.runtime.remember.extraction = ModelDouble()
    try:
        run = DemoRun(service, "alice", tmp_path / "scenario")
        result = await (flow_scenario if scenario_name == "flows" else summary_scenario)(run)
        assert result["passed"], result
        assert not result["production_acceptance"]
        assert result["providers"]["objects"].endswith(".CephObjects")
        assert result["providers"]["vectors"].endswith(".AzureVectors")
        with service.runtime.foundation.uow.transaction() as tx:
            assert tx.rows(service.runtime.vectors.projection_namespace)
        assert not list((config.data_dir / "bodies").glob("*"))
        assert not list((config.data_dir / "inputs").glob("*"))
    finally:
        await service.close()
