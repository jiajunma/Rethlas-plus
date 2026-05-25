"""StatementFixer role — invokes the agent (v1.4)."""

from __future__ import annotations

from dataclasses import dataclass

from rethlas_kb.adapter import KbAdapter
from rethlas_kb.backends import AgentBackend

from .decoder import StatementFixReview, parse
from .prompt import compose


AGENT_ROLE = "statement-fixer"


@dataclass
class StatementFixer:
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
        prior_review: str = "",
    ) -> StatementFixReview:
        node = adapter.read_node(node_id)
        context = adapter.context_pack(
            target_id=node_id,
            include_staged=self.include_staged_context,
        )
        project_rules = adapter.read_project_rules_combined(AGENT_ROLE)
        prompt_text = compose(
            node, context,
            prior_review=prior_review,
            project_rules=project_rules,
        )
        result = self.backend.run(
            agent_role=AGENT_ROLE,
            prompt=prompt_text,
            timeout_seconds=self.timeout_seconds,
            output_format="json",
        )
        return parse(result.stdout)


__all__ = ["AGENT_ROLE", "StatementFixer"]
