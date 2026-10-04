"""Seed controlled candidate faults in real Milvus, then use production pagination."""

import os
from uuid import uuid4

from aether_agent_memory.recall.basic.milvus_generation import MilvusGenerationSearch
from aether_agent_memory.remember.contracts.models import ProjectionTarget
from aether_agent_memory.runtime.storage.vectors import AzureVectors
from azure_test_runtime import owned


class AzureCandidateSearch:
    def __init__(self, uow, identity):
        self.uow, self.identity = uow, identity
        self.vectors = AzureVectors(
            uow, identity, "test_space", 2,
            namespace="test-" + uuid4().hex, collection="candidates",
            uri=os.environ["P3_TEST_MILVUS_URI"], token=os.environ["P3_TEST_MILVUS_TOKEN"],
            database=os.environ["P3_TEST_MILVUS_DATABASE"],
            ca_file=os.environ["P3_TEST_MILVUS_CA_FILE"],
            server_name=os.environ["P3_TEST_MILVUS_SERVER_NAME"],
        )
        owned().clients.append(self.vectors)
        self.prepared_ids = set()

    async def search(self, ctx, request):
        await self.vectors.prepare(ctx)
        with self.uow.transaction() as tx:
            rows = tx.rows("generation_vectors")
        ids = {key for key, _ in rows}
        for missing in self.prepared_ids - ids:
            self.vectors.client.delete(collection_name=self.vectors.collection, ids=[missing])
        values = []
        for key, row in rows:
            hit = row["hit"]
            target = ProjectionTarget.model_validate(
                {k: v for k, v in hit.items() if k not in {"rank", "score", "score_semantics"}}
            )
            values.append({
                "vector_id": key, "vector": row["vector"], "target": target.model_dump(mode="json"),
                "model_space": target.model_space,
                **{k: v or "" for k, v in target.memory.scope.model_dump().items()},
            })
        if values:
            # Invalid dimensions are a deliberate corruption gate, rejected by the actual SDK.
            self.vectors.client.upsert(collection_name=self.vectors.collection, data=values)
        self.prepared_ids = ids
        return await MilvusGenerationSearch(self.vectors).search(ctx, request)
