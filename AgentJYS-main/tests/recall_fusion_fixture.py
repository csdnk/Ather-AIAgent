"""Explicit B contract double for F3; never evidence of real Remember publication.

Facts are keyed by exact Ref so the baseline can represent multiple versions.
RF identity, UOW, assembly and persisted planning remain real.
"""

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from aether_agent_memory.recall.contracts.foundation import MemoryCandidate
from aether_agent_memory.remember.contracts.foundation import (
    FullBodyReadResult,
    MemoryRelationSnapshot,
    ProjectionReadiness,
)
from aether_agent_memory.remember.contracts.models import (
    EligibilityBatch,
    EligibilityResult,
    MemoryReadBatch,
    MemorySnapshot,
)
from aether_agent_memory.runtime.contracts.models import ErrorCode


class FusionFacts:
    def __init__(self, app, ctx):
        self.app = app
        self.snapshots, self.bodies, self.candidates = {}, {}, {}
        cases = json.loads(
            (Path(__file__).resolve().parents[1] / "contracts/p3/fixtures/cases.json").read_text()
        )

        def example(name):
            return deepcopy(next(c["payload"] for c in cases if c["model"] == name and c["valid"]))

        scope = ctx.principal.home_scope.model_dump(mode="json")
        for name, version in (("A", 2), ("B", 1), ("C", 1), ("A", 1)):
            ref = {"scope": scope, "memory_id": name, "version": version}
            # B and C deliberately have identical text but distinct Ref/provenance.
            content = (
                f"项目评审规则 A v{version}，保留完整条件。" if name == "A" else "相同完整正文。"
            )
            digest = sha256(content.encode()).hexdigest()
            vector_id = sha256(f"{name}-{version}".encode()).hexdigest()
            generation = f"F3-{name}-{version}"
            candidate = example("recall.MemoryCandidate")
            manifest, guard = candidate["manifest"], candidate["guard"]
            manifest.update(
                memory=ref,
                body_hash=digest,
                model_space=app.model_space,
                generation=generation,
                dimensions=app.embedding.space.dimensions,
                embedding_tokenizer=app.embedding.space.tokenizer_id,
            )
            manifest["vector_location"].update(content_hash=digest, generation=generation)
            manifest["chunks"][0].update(
                vector_id=vector_id, input_hash=digest, end_char=len(content)
            )
            guard.update(
                memory=ref,
                body_hash=digest,
                authorization_epoch=ctx.principal.auth_epoch,
                checked_at=app.foundation.identity.clock(),
            )
            candidate["memory"] = ref
            candidate["hits"][0].update(
                memory=ref,
                vector_id=vector_id,
                body_hash=digest,
                input_hash=digest,
                model_space=app.model_space,
                memory_source="long_term",
                generation=generation,
            )
            validated = MemoryCandidate.model_validate(candidate)
            snapshot = example("remember.MemorySnapshot")
            snapshot.update(
                ref=ref,
                content=content,
                content_hash=digest,
                model_space=app.model_space,
                projection_state="ready",
            )
            snapshot["sources"][0].update(source_id=f"source-{name}-{version}", content_hash=digest)
            memory = MemorySnapshot.model_validate(snapshot)
            body = example("remember.FullBodyReadResult")
            body.update(memory=ref, content=content, guard=guard, sources=snapshot["sources"])
            body["location"].update(content_hash=digest, generation=generation)
            self.snapshots[memory.ref] = memory
            self.bodies[memory.ref] = FullBodyReadResult.model_validate(body)
            self.candidates[name, version] = validated

    def load(self, ctx, refs):
        with self.app.foundation.uow.transaction() as tx:
            self.app.foundation.identity.revalidate(tx, ctx)
        return MemoryReadBatch(
            items=tuple(self.snapshots[r] for r in refs),
            eligibility=EligibilityBatch(
                authorization_epoch=ctx.principal.auth_epoch,
                items=tuple(
                    EligibilityResult(
                        ref=r,
                        decision="allowed",
                        reason="F3 fixture",
                        checked_revision=1,
                    )
                    for r in refs
                ),
            ),
            conflicts=(),
        )

    async def projection_readiness(self, ctx, selection, memory_source):
        return ProjectionReadiness(
            source=memory_source,
            ready_count=len(self.snapshots),
            pending_count=0,
            failed_count=0,
            complete=True,
        )

    def relations(self, ctx, refs):
        return MemoryRelationSnapshot(
            guards=tuple(self.bodies[r].guard for r in refs), conflicts=()
        )

    async def load_bodies(self, ctx, refs):
        return tuple(self.bodies[r] for r in refs)

    def revalidate_context(self, tx, ctx, request):
        self.app.foundation.identity.revalidate(tx, ctx)
        for stamp in request.expected:
            if stamp.model_dump(exclude={"checked_at"}) != self.bodies[
                stamp.memory
            ].guard.model_dump(exclude={"checked_at"}):
                tx.abort(ErrorCode.RESULT_INVALIDATED, "F3 body guard mismatch")
        manifests = {c.memory: c.manifest for c in self.candidates.values()}
        for manifest in request.manifests:
            if manifests[manifest.memory] != manifest:
                tx.abort(ErrorCode.RESULT_INVALIDATED, "F3 manifest mismatch")
        return tuple(
            self.bodies[s.memory].guard.model_copy(
                update={"checked_at": self.app.foundation.identity.clock()}
            )
            for s in request.expected
        )
