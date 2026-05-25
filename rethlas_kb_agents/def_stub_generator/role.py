"""DefStubGenerator role (v1.4)."""

from __future__ import annotations

from dataclasses import dataclass

from rethlas_kb.adapter import KbAdapter
from rethlas_kb.backends import AgentBackend

from .decoder import DefStubReview, parse
from .prompt import compose


AGENT_ROLE = "def-stub-generator"


@dataclass
class DefStubGenerator:
    backend: AgentBackend
    timeout_seconds: int = 300
    include_staged_context: bool = True

    @property
    def name(self) -> str:
        return AGENT_ROLE

    def run(
        self,
        missing_id: str,
        referring_node_id: str,
        adapter: KbAdapter,
        *,
        reason: str = "",
    ) -> DefStubReview:
        referring_node = adapter.read_node(referring_node_id)
        # Context built around the REFERRING node — the missing one
        # doesn't exist yet, so we have no closure for it.
        context = adapter.context_pack(
            target_id=referring_node_id,
            include_staged=self.include_staged_context,
        )
        project_rules = adapter.read_project_rules_combined(AGENT_ROLE)
        prompt_text = compose(
            missing_id, referring_node, context,
            reason=reason, project_rules=project_rules,
        )
        result = self.backend.run(
            agent_role=AGENT_ROLE,
            prompt=prompt_text,
            timeout_seconds=self.timeout_seconds,
            output_format="json",
        )
        return parse(result.stdout)


__all__ = ["AGENT_ROLE", "DefStubGenerator"]
