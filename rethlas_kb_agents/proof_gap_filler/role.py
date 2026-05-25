"""GapFiller role — proof-gap-filler agent entry point (issue #10)."""

from __future__ import annotations

from dataclasses import dataclass

from rethlas_kb.adapter import KbAdapter
from rethlas_kb.backends import AgentBackend

from .decoder import GapFillReview, parse
from .prompt import compose


AGENT_ROLE = "proof-gap-filler"


@dataclass
class GapFiller:
    """Bind an :class:`AgentBackend` to the proof-gap-filler role.

    ``repair_count`` semantics: 0 = fresh attempt, 1 = first repair
    (uses verifier report), 2+ = Phase II reroute (previous proof
    dropped from context).
    """

    backend: AgentBackend
    timeout_seconds: int = 900  # generators take longer than verifiers
    include_staged_context: bool = True

    @property
    def name(self) -> str:
        return AGENT_ROLE

    def run(
        self,
        node_id: str,
        adapter: KbAdapter,
        *,
        prior_verification_report: str = "",
        repair_count: int = 0,
    ) -> GapFillReview:
        node = adapter.read_node(node_id)
        context = adapter.context_pack(
            target_id=node_id,
            include_staged=self.include_staged_context,
        )
        project_rules = adapter.read_project_rules_combined(AGENT_ROLE)
        prompt_text = compose(
            node, context,
            project_rules=project_rules,
            prior_verification_report=prior_verification_report,
            repair_count=repair_count,
        )

        result = self.backend.run(
            agent_role=AGENT_ROLE,
            prompt=prompt_text,
            timeout_seconds=self.timeout_seconds,
            output_format="json",
        )
        return parse(result.stdout)


__all__ = ["AGENT_ROLE", "GapFiller"]
