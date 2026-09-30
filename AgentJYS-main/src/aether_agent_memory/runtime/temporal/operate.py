"""Operate action reconciliation and the explicit P3 cache-repair allowlist."""

from aether_agent_memory.operate.basic.maintenance import CacheMaintenance
from aether_agent_memory.operate.basic.service import Operate
from aether_agent_memory.runtime.contracts.models import Permission

from .registry import StageRegistry


def register_operate(
    registry: StageRegistry, operate: Operate, maintenance: CacheMaintenance
) -> None:
    from aether_agent_memory.operate.basic.temporal_stages import OperateStages, RepairStages

    actions, repairs = OperateStages(operate), RepairStages(maintenance)
    registry.register(
        "operate.evaluate",
        "prepare",
        actions.prepare,
        actions.prepare,
        Permission.READ,
        "idempotent",
        timeout_seconds=60,
    )
    registry.register(
        "operate.evaluate",
        "submit",
        actions.submit,
        actions.reconcile,
        Permission.READ,
        "uncertain",
        timeout_seconds=60,
    )
    registry.register(
        "operate.evaluate",
        "reconcile",
        actions.reconcile,
        actions.reconcile,
        Permission.READ,
        "uncertain",
        timeout_seconds=60,
    )
    registry.register(
        "operate.evaluate",
        "commit",
        actions.commit,
        actions.commit,
        Permission.READ,
        "idempotent",
        timeout_seconds=60,
    )
    registry.register(
        "operate_repair_cache",
        "diagnose",
        repairs.diagnose,
        repairs.diagnose,
        Permission.RECOVER,
        "read",
        timeout_seconds=30,
    )
    registry.register(
        "operate_repair_cache",
        "repair",
        repairs.repair,
        repairs.reconcile,
        Permission.RECOVER,
        "uncertain",
        timeout_seconds=30,
    )
    registry.register(
        "operate_repair_cache",
        "verify",
        repairs.verify,
        repairs.verify,
        Permission.RECOVER,
        "read",
        timeout_seconds=5,
    )
    registry.register(
        "operate_repair_cache",
        "close",
        repairs.close,
        repairs.close,
        Permission.RECOVER,
        "idempotent",
        timeout_seconds=5,
    )
