"""Candidate-only extraction adapters. Neither adapter owns persistence."""

from typing import Any

from pydantic import BaseModel

from aether_agent_memory.remember.contracts.models import (
    CandidateFact,
    ExtractionRequest,
    ExtractionResult,
)
from aether_agent_memory.runtime.contracts.models import TrustedContext


class LiteralExtraction:
    """Preserve supplied text verbatim as an episode; no inferred semantic facts."""

    async def extract(self, ctx: TrustedContext, request: ExtractionRequest) -> ExtractionResult:
        return ExtractionResult(
            candidates=(
                CandidateFact(
                    text=request.text, sources=(request.source,), evidence_status="supported"
                ),
            ),
            model_id="literal_episode_baseline",
            policy_version=request.policy_version,
        )


class SupportedFact(BaseModel):
    text: str
    evidence_quote: str


class LangMemExtraction:
    """Inject a configured LangMem Core manager or construct one with a real model.

    The model and credentials are deployment concerns. No fallback on provider failure.
    """

    def __init__(self, manager: Any, model_id: str) -> None:
        self.manager, self.model_id = manager, model_id

    @classmethod
    def from_model(cls, model: Any, model_id: str) -> "LangMemExtraction":
        from langmem import create_memory_manager  # type: ignore[import-not-found]

        manager = create_memory_manager(
            model,
            schemas=[SupportedFact],
            instructions=(
                "Extract only facts supported by this input. Include an exact "
                "non-empty evidence_quote. Do not treat quoted third-party "
                "statements or hypotheticals as user preferences."
            ),
            enable_updates=False,
            enable_deletes=False,
        )
        return cls(manager, model_id)

    async def extract(self, ctx: TrustedContext, request: ExtractionRequest) -> ExtractionResult:
        output = await self.manager.ainvoke(
            {"messages": [{"role": "user", "content": request.text}], "existing": []}
        )
        candidates = []
        for item in output:
            content = item.content
            fact = (
                content
                if isinstance(content, SupportedFact)
                else SupportedFact.model_validate(content)
            )
            if (
                not fact.text.strip()
                or not fact.evidence_quote.strip()
                or fact.evidence_quote not in request.text
                or fact.text not in fact.evidence_quote
            ):
                raise ValueError("LangMem candidate lacks exact supporting evidence")
            candidates.append(
                CandidateFact(
                    text=fact.text, sources=(request.source,), evidence_status="supported"
                )
            )
        return ExtractionResult(
            candidates=tuple(candidates),
            model_id=self.model_id,
            policy_version=request.policy_version,
        )
