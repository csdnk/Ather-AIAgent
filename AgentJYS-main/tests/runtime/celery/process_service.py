"""Shared real-storage service composition for hybrid HTTP process tests."""

import json
import os

from aether_agent_memory.runtime.flows.application import Service as ProductionService
from azure_component_service import Service as ComponentService
from azure_test_runtime import OwnedResources, _resources
from component_configuration import ComponentConfiguration


class HybridService(ComponentService):
    execution_service = ProductionService.execution_service


_service = None


def factory():
    global _service
    _resources.set(OwnedResources(json.loads(os.environ["CELERY_TEST_MANIFEST"])))
    config = ComponentConfiguration.model_validate_json(os.environ["CELERY_TEST_CONFIGURATION"])
    _service = HybridService(config, worker_role=True)
    return _service.execution.celery
