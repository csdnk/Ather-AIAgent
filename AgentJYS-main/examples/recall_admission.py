"""Admission-only example using real PostgreSQL, with trusted inputs on stdin.

Input JSON contains postgres_dsn and authorization_snapshots. The latter are
verified setup evidence, supplied by the developer's trusted authority adapter.
Ordinary HTTP input must never provision its own authorization. Use a personal
p3_dev_ or p3_test_ database. This example writes admission records, starts their
execution state, and closes the database connection; it does not produce context.
See docs/recall_admission_first_batch.md for invocation and payload requirements.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta

from psycopg.conninfo import conninfo_to_dict

from aether_agent_memory.recall.admission import (
    RecallAdmissionService,
    RecallAuthorization,
    RecallError,
    RecallInput,
    RecallPolicy,
)
from aether_agent_memory.recall.execution import RecallExecutionService
from aether_agent_memory.recall.store import RecordRecallExecutionStore
from aether_agent_memory.runtime.contract_types import utcnow
from aether_agent_memory.runtime.foundation.postgres import PostgresCapabilityStore


class StoredAuthorization:
    """Read independently provisioned proof; unknown evidence fails closed."""

    def __init__(self, records: PostgresCapabilityStore) -> None:
        self.records = records

    async def authorize(self, request: RecallInput) -> RecallAuthorization:
        with self.records.transaction() as tx:
            raw = tx.get(
                "recall_example_authorizations", request.principal_ref, request.authorization_ref
            )
        if raw is None:
            raise RecallError("AUTHORITY_UNKNOWN")
        return RecallAuthorization.model_validate_json(raw)


async def main() -> None:
    payload = json.loads(sys.stdin.readline())
    dsn = payload["postgres_dsn"]
    database = conninfo_to_dict(dsn).get("dbname", "")
    if not database.startswith(("p3_dev_", "p3_test_")):
        raise ValueError("the example requires a personal p3_dev_ or p3_test_ database")
    snapshots = payload["authorization_snapshots"]
    if not 1 <= len(snapshots) <= 5:
        raise ValueError("supply one to five independently verified authorization snapshots")
    policy = RecallPolicy(
        tokenizer_id="example-tokenizer",
        tokenizer_version="1",
        template_version="1",
        retrieval_space_ref="example-space",
    )
    records = PostgresCapabilityStore(dsn)
    try:
        authority = StoredAuthorization(records)
        store = RecordRecallExecutionStore(records)
        service = RecallAdmissionService(None, authority, policy, execution_store=store)
        for row in snapshots:
            evidence = RecallAuthorization.model_validate(row["authorization"])
            with records.transaction() as tx:
                raw = evidence.model_dump_json()
                prior = tx.get(
                    "recall_example_authorizations", evidence.principal_ref, evidence.evidence_ref
                )
                if prior is not None and prior != raw:
                    raise ValueError("example authorization proof changed; use fresh evidence IDs")
                tx.put(
                    "recall_example_authorizations",
                    evidence.principal_ref,
                    evidence.evidence_ref,
                    raw,
                )
            request = RecallInput(
                request_id=row["scenario"],
                trace_id=row["scenario"],
                query="这个项目之前定过哪些约定？",
                scope=evidence.scope,
                principal_ref=evidence.principal_ref,
                authorization_ref=evidence.evidence_ref,
                idempotency_key=row["scenario"],
                deadline_at=utcnow() + timedelta(seconds=5),
                token_budget=128,
            )
            try:
                admitted = await service.admit(request)
                execution = RecallExecutionService(store).start(
                    evidence.scope.tenant_id, admitted.request.recall_id, owner="example-worker"
                )
                print(
                    json.dumps(
                        dict(
                            scenario=row["scenario"],
                            mode=admitted.request.retrieval_mode,
                            state=execution.state,
                            recall_id=execution.recall_id,
                            context_produced=False,
                            backend="postgresql",
                        ),
                        ensure_ascii=False,
                    )
                )
            except RecallError as exc:
                print(
                    json.dumps(
                        dict(scenario=row["scenario"], rejected=exc.code), ensure_ascii=False
                    )
                )
    finally:
        records.close()


if __name__ == "__main__":
    asyncio.run(main())
