"""Deterministic model doubles for the current two-stage storage workflow.

These exercise the real official-adapter contracts, not model quality. The
preprocessor preserves every character unless a test explicitly supplies a fault.
"""

import json
from types import SimpleNamespace

from aether_agent_memory.remember.basic.llmlingua import CompressionView
from aether_agent_memory.remember.basic.official_langmem import OfficialLangMemConsolidation
from aether_agent_memory.runtime.foundation.requests import text_hash


class ControlledManager:
    def __init__(self, fact=None, kind="semantic"):
        self.fact, self.kind = fact, kind
        self.extraction_inputs, self.decision_inputs = [], []
        self.before_extract = None

    async def ainvoke(self, payload, **kwargs):
        body = json.loads(payload["messages"][0]["content"])
        if "sources" in body:
            self.extraction_inputs.append(body)
            if self.before_extract is not None:
                await self.before_extract()
            if self.fact is None:
                return []
            evidence = [
                fragment["evidence_id"]
                for source in body["sources"]
                for fragment in source["fragments"]
                if self.fact in fragment["text"]
            ]
            return (
                [
                    SimpleNamespace(
                        id="candidate",
                        content={
                            "text": self.fact,
                            "kind": self.kind,
                            "evidence_ids": evidence,
                        },
                    )
                ]
                if evidence
                else []
            )
        self.decision_inputs.append(body)
        output = []
        for candidate in body["candidates"]:
            old = next(
                (
                    row
                    for row in payload["existing"]
                    if row[0] in body["related_ids"][candidate["candidate_id"]]
                    and row[2]["text"] == candidate["text"]
                    and row[2]["kind"] == candidate["kind"]
                ),
                None,
            )
            output.append(
                SimpleNamespace(
                    id=old[0] if old else "new-" + candidate["candidate_id"],
                    content={
                        "text": candidate["text"],
                        "kind": candidate["kind"],
                        "candidate_ids": [candidate["candidate_id"]],
                        "evidence_ids": [value["evidence_id"] for value in candidate["evidence"]],
                        "relationship": "no_change" if old else "create",
                        "reason": "controlled exact fact comparison",
                    },
                )
            )
        return output


class IdentityPreprocessor:
    model_identity = {"adapter": "controlled-token-deletion", "model": "test-double"}

    def __init__(self):
        self.inputs = []
        self.before_compress = None

    async def acompress(self, text):
        self.inputs.append(text)
        if self.before_compress is not None:
            await self.before_compress()
        return CompressionView(
            text=text,
            offsets=tuple(range(len(text))),
            source_hash=text_hash(text),
            source_chars=len(text),
            original_bytes=len(text.encode()),
            retained_bytes=len(text.encode()),
        )


def configure_candidates(app, fact=None, kind="semantic"):
    manager, processor = ControlledManager(fact, kind), IdentityPreprocessor()
    app.remember.extraction = OfficialLangMemConsolidation(
        manager,
        "controlled-current-flow",
        extraction_manager=manager,
        decision_manager=manager,
    )
    app.remember.llmlingua_preprocessor = processor
    return manager, processor
