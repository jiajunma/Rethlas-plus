"""proof-gap-filler — first generator agent (issue #10).

Takes a node with partial / missing proof + KB context + (optionally) a
prior proof-verifier report, and produces a completed proof body.

Generator discipline (vs. verifier discipline):
- **Stateful / repair-aware**: receives the failing verification
  report and acts on it.
- **Phase II reroute**: after enough repair iterations, drop the
  previous proof entirely from context and demand a materially
  different strategy (from Rethlas-original ``generator/prompt.py``).
- **Anti-handwave**: explicit ban on "clearly / obviously / by a
  standard argument" (from QED ``super_math_skill.md``).
- **Counterexample-first**: before attempting a proof, the generator
  is asked to try to refute the claim. Failed refutation often
  reveals why the proof must work.

Public API:

- :class:`GapFiller` — agent role; ``.run(node_id, adapter, ...)``
- :class:`GapFillReview` — typed result
- :class:`GapFillReviewParseError` — decoder error
- :func:`compose` — pure prompt builder
- :func:`parse` — pure decoder
"""

from __future__ import annotations

from .decoder import (
    GapFillReview,
    GapFillReviewParseError,
    NewSubLemma,
    parse,
)
from .prompt import compose
from .role import AGENT_ROLE, GapFiller

__all__ = [
    "AGENT_ROLE",
    "GapFillReview",
    "GapFillReviewParseError",
    "GapFiller",
    "NewSubLemma",
    "compose",
    "parse",
]
