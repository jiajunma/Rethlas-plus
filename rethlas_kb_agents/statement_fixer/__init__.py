"""statement-fixer — auto-repair a flagged statement (issue #23, v1.4).

Closes the loop: statement-verifier flags issues; this agent proposes
a corrected statement. Generator discipline.

When statement-verifier returns ``formulation_issue`` /
``generality_concern`` / ``context_insufficient``, the user (or the
``/fix-loop`` orchestrator) invokes ``statement-fixer`` with the
prior review. The fixer either:

- ``fixed`` — produces a revised statement body that addresses every
  listed issue. The CLI applies it to the staged node.
- ``cannot_fix`` — the issue is fundamental (genuine ambiguity,
  needs research judgement). The fixer explains why; human must take it.
- ``defers_to_human`` — fixing would change the mathematical claim
  in ways the LLM shouldn't decide unilaterally (e.g. weakening a
  conclusion when the conclusion might be the whole point).

Anti-handwave still applies — the fixer doesn't paper over issues
with vaguer wording.
"""

from __future__ import annotations

from .decoder import (
    StatementFixReview,
    StatementFixReviewParseError,
    parse,
)
from .prompt import compose
from .role import AGENT_ROLE, StatementFixer

__all__ = [
    "AGENT_ROLE",
    "StatementFixReview",
    "StatementFixReviewParseError",
    "StatementFixer",
    "compose",
    "parse",
]
