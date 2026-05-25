"""SourceClaimVerifier role (issue #12).

v1 takes pre-extracted source passage + (optional) source proof text
as input. The actual PDF extractor is deferred to v1.5+ (see package
docstring for the rationale).
"""

from __future__ import annotations

from dataclasses import dataclass

from rethlas_kb.adapter import KbAdapter
from rethlas_kb.backends import AgentBackend

from .decoder import SourceClaimReview, parse
from .prompt import compose


AGENT_ROLE = "source-claim-verifier"


@dataclass
class SourceClaimVerifier:
    backend: AgentBackend
    timeout_seconds: int = 600
    include_staged_context: bool = True

    @property
    def name(self) -> str:
        return AGENT_ROLE

    def run(
        self,
        node_id: str,
        adapter: KbAdapter,
        *,
        source_passage: str = "",
        source_proof: str = "",
    ) -> SourceClaimReview:
        node = adapter.read_node(node_id)
        if node.kind != "external-theorem":
            # We accept other kinds too (audit a claim against a paper),
            # but this is the canonical use case. Warn via the prompt
            # by including the kind in the target rendering.
            pass
        context = adapter.context_pack(
            target_id=node_id,
            include_staged=self.include_staged_context,
        )
        project_rules = adapter.read_project_rules_combined(AGENT_ROLE)
        prompt_text = compose(
            node, context,
            source_passage=source_passage,
            source_proof=source_proof,
            project_rules=project_rules,
        )
        result = self.backend.run(
            agent_role=AGENT_ROLE,
            prompt=prompt_text,
            timeout_seconds=self.timeout_seconds,
            output_format="json",
        )
        return parse(result.stdout)


__all__ = ["AGENT_ROLE", "SourceClaimVerifier"]
