"""statement-verifier — does the node's *statement* read correctly?

This is the cheapest agent in the v1 pipeline: it judges whether the
node's claim (definition / lemma / proposition / theorem) is itself
well-formed. Proof correctness is *not* in scope — that belongs to
``proof-verifier`` (issue #9).

Public API:

- :class:`StatementVerifier` — the agent role; constructed with an
  :class:`AgentBackend`, invoked with a node_id and a
  :class:`KbAdapter`.
- :class:`StatementReview` — typed result.
- :class:`StatementReviewParseError` — raised by the decoder when
  the LLM output can't be parsed into a review.
- :func:`compose` — pure prompt builder.
- :func:`parse` — pure decoder.

See ``AGENTS.md`` for how the agent fits into the QED-style multi-
stage verification pipeline.
"""

from __future__ import annotations

from .decoder import StatementReview, StatementReviewParseError, parse
from .prompt import compose
from .role import AGENT_ROLE, StatementVerifier

__all__ = [
    "AGENT_ROLE",
    "StatementReview",
    "StatementReviewParseError",
    "StatementVerifier",
    "compose",
    "parse",
]
