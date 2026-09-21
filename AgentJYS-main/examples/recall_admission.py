"""Run with: python examples/recall_admission.py (installed project required).

Uses ONLY test doubles; no model inference, database or external writes.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from aether_agent_memory.mocks.recall import InMemoryRecallRecords, StaticRecallAuthority
from aether_agent_memory.recall.admission import (
    RecallAdmissionService,
    RecallAuthorization,
    RecallError,
    RecallInput,
    RecallPolicy,
)
from aether_agent_memory.recall.execution import RecallExecutionService
from aether_agent_memory.recall.store import RecordRecallExecutionStore
from aether_agent_memory.runtime.contract_types import Scope, utcnow


async def main() -> None:
    policy = RecallPolicy(
        tokenizer_id="fixture-tokenizer",
        tokenizer_version="1",
        template_version="1",
        retrieval_space_ref="fixture-space",
    )
    for name, current, working, long_term in (
        ("combined", True, True, True),
        ("working_only", True, True, False),
        ("long_term_only", False, False, True),
        ("scope_denied", True, False, False),
        ("authority_unknown", True, None, True),
    ):
        scope = Scope(
            tenant_id="fixture-tenant",
            project_id=None,
            agent_id="fixture-agent",
            session_id="fixture-session" if current else None,
            task_id=None,
        )
        authority = StaticRecallAuthority(
            RecallAuthorization(
                scope=scope,
                principal_ref="fixture-principal",
                evidence_ref="fixture-proof",
                scope_valid=True,
                working_read=working,
                long_term_read=long_term,
                valid_until=utcnow() + timedelta(seconds=30),
            )
        )
        store = RecordRecallExecutionStore(InMemoryRecallRecords())
        service = RecallAdmissionService(None, authority, policy, execution_store=store)
        request = RecallInput(
            request_id=name,
            trace_id=name,
            query="这个项目之前定过哪些约定？",
            scope=scope,
            principal_ref="fixture-principal",
            authorization_ref="fixture-proof",
            idempotency_key=name,
            deadline_at=utcnow() + timedelta(seconds=5),
            token_budget=128,
        )
        try:
            admitted = await service.admit(request)
            execution = RecallExecutionService(store).start(
                scope.tenant_id,
                admitted.request.recall_id,
                owner="fixture-worker",
            )
            print(
                json.dumps(
                    {
                        "scenario": name,
                        "mode": admitted.request.retrieval_mode,
                        "state": execution.state,
                        "recall_id": execution.recall_id,
                        "context_produced": False,
                        "backend": "test-double",
                    },
                    ensure_ascii=False,
                )
            )
        except RecallError as exc:
            print(json.dumps({"scenario": name, "rejected": exc.code}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
