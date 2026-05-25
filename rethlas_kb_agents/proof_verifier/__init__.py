"""proof-verifier — QED-style 3-stage proof verification (issue #9).

Three independent stages, each with its own prompt and decoder:

- **judge** (cheap) — classify Easy / Hard. On Easy, also emit a
  full one-shot verdict (no escalation needed). On Hard, just
  classify; the structural stage takes over.
- **structural** (medium) — four high-level checks
  (statement quality / alignment / completeness / architecture).
  PASS or FAIL. Explicitly does NOT verify step correctness.
- **detailed** (deep) — step-by-step verification. Inherits the
  structural report; never re-checks structural claims.

Pipeline (auto depth):

    judge
      ├─ if easy:  judge's full verdict IS the answer
      └─ if hard:  structural
                    ├─ if fail: short-circuit (no detailed run)
                    └─ if pass: detailed → answer

Mode B exposes ``--depth {auto|easy|structural|detailed}``. Mode A
(slash commands) lets the agentic CLI compose the pipeline itself
via per-stage ``compose-prompt`` and ``write-review`` calls.

Pattern ported from ``~/mycodes/QED/verify/verify.py``.
"""

from __future__ import annotations

from .decoder import (
    DetailedVerdict,
    JudgeVerdict,
    ProofReview,
    ProofReviewParseError,
    RigorIssue,
    StepVerdict,
    StructuralCheck,
    StructuralVerdict,
    parse_detailed,
    parse_judge,
    parse_structural,
)
from .prompts import compose_detailed, compose_judge, compose_structural
from .role import AGENT_ROLE, ProofVerifier

__all__ = [
    "AGENT_ROLE",
    "DetailedVerdict",
    "JudgeVerdict",
    "ProofReview",
    "ProofReviewParseError",
    "ProofVerifier",
    "RigorIssue",
    "StepVerdict",
    "StructuralCheck",
    "StructuralVerdict",
    "compose_detailed",
    "compose_judge",
    "compose_structural",
    "parse_detailed",
    "parse_judge",
    "parse_structural",
]
