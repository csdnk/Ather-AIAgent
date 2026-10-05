"""Controlled model responses for the summary scenario; never a deployable model."""

from aether_agent_memory.remember.contracts.models import CandidateFact, ExtractionResult

EVENT = "The deployment failed on Tuesday."
RULE = "Rollback requires an approval."


class ModelDouble:
    async def extract(self, ctx, request):
        return ExtractionResult(
            candidates=(
                CandidateFact(
                    text=EVENT,
                    kind="episodic",
                    sources=(request.source,),
                    evidence_status="supported",
                    event_key="deployment_tuesday",
                ),
            )
            if EVENT in request.text
            else (),
            model_id="deterministic-demo",
            policy_version=request.policy_version,
        )

    async def review_episodes(self, ctx, episodes, originals, policy_version):
        source = next(i.sources[0] for i in originals if RULE in i.content)
        return ExtractionResult(
            candidates=(
                CandidateFact(
                    text=RULE, kind="semantic", sources=(source,), evidence_status="supported"
                ),
            ),
            model_id="deterministic-demo-review",
            policy_version=policy_version,
        )
