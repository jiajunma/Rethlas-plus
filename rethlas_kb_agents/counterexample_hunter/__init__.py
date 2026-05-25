"""counterexample-hunter — second generator agent (issue #11).

A *differentiator* agent for research math: actively tries to refute
a stated claim by searching for a concrete witness. This is an
*inverse search* (does there exist X violating the claim?) — the
opposite cognitive task to proof-verifier's forward verification.

The agent honours QED's principle 11 + 37:

  Actively search for a counterexample. If you cannot find one, the
  failed attempts typically reveal why the statement must be true.

  Before proving a general statement, verify it computationally for
  small cases (n=1..20, specific matrices, small graphs, etc.). If
  computation refutes the claim for some case, you've found a
  counterexample — no proof is needed.

## Decision vocabulary

- ``counterexample_found``      → explicit witness + suggested_fix list
- ``no_counterexample_found``   → enumerated attempted_cases that all
                                   satisfied the claim (no proof — only
                                   "I tried these and none refuted it")
- ``inconclusive``              → search was meaningfully incomplete
                                   (e.g. case-explosion, timeout,
                                   needed computation tools unavailable)

The agent NEVER concludes "the claim is therefore true". That's
proof-verifier's job. The hunter only reports search outcomes.
"""

from __future__ import annotations

from .decoder import (
    AttemptedCase,
    CounterexampleHuntReview,
    CounterexampleHuntReviewParseError,
    Witness,
    parse,
)
from .prompt import compose
from .role import AGENT_ROLE, CounterexampleHunter

__all__ = [
    "AGENT_ROLE",
    "AttemptedCase",
    "CounterexampleHunter",
    "CounterexampleHuntReview",
    "CounterexampleHuntReviewParseError",
    "Witness",
    "compose",
    "parse",
]
