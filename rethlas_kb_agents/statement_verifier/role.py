"""StatementVerifier role — backend-agnostic agent entry point (issue #7).

Walking skeleton for the v1 pipeline. The role:

1. Loads the target node + a context pack via :class:`KbAdapter`.
2. Composes the prompt with :func:`prompt.compose`.
3. Invokes the injected :class:`AgentBackend`.
4. Decodes the response with :func:`decoder.parse`.
5. Returns the typed :class:`StatementReview`.

The role intentionally knows *nothing* about which LLM the backend
wraps — that's the whole point of the Protocol layer. Cross-backend
isolation (the "proof-verifier ≠ statement-verifier" rule, issue
#13) is enforced one level up at config-validation time.
"""

from __future__ import annotations

from dataclasses import dataclass

from rethlas_kb.adapter import KbAdapter
from rethlas_kb.backends import AgentBackend, AgentResult

from .decoder import StatementReview, parse
from .prompt import compose


AGENT_ROLE = "statement-verifier"


@dataclass
class StatementVerifier:
    """Bind an :class:`AgentBackend` to the statement-verifier role.

    ``include_staged_context`` — when True (the default), the context
    pack passes ``include_staged=True`` so the LLM sees nodes that
    are still under review. The output contract requires those to be
    treated as provisional, not as established facts. Turn off if the
    caller wants a strictly-admitted view (e.g. when sanity-checking
    a freshly admitted node).
    """

    backend: AgentBackend
    timeout_seconds: int = 300
    include_staged_context: bool = True

    @property
    def name(self) -> str:
        return AGENT_ROLE

    def run(self, node_id: str, adapter: KbAdapter) -> StatementReview:
        """Verify one node's statement; return the typed review."""
        node = adapter.read_node(node_id)
        context = adapter.context_pack(
            target_id=node_id,
            include_staged=self.include_staged_context,
        )
        prompt_text = compose(node, context)

        result: AgentResult = self.backend.run(
            agent_role=AGENT_ROLE,
            prompt=prompt_text,
            timeout_seconds=self.timeout_seconds,
            output_format="json",
        )
        return parse(result.stdout)


__all__ = ["AGENT_ROLE", "StatementVerifier"]
