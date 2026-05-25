"""source-claim-verifier — external-theorem extraction-fidelity audit (issue #12).

For ``external-theorem`` kind nodes (theorems cited from a published
source — arXiv paper, textbook, etc.). Verifies two things:

1. **Alignment** — does your node's statement faithfully reproduce
   the source paper's statement? (no quantifier drift, no dropped
   hypotheses, no widened conclusion)
2. **Source-proof soundness** (when proof text is provided) — does
   the source paper's proof actually establish the claim?

## Decision vocabulary

- ``accepted``       — alignment + (when checked) source proof OK
- ``mismatch``       — extracted statement differs from your node
- ``proof_gap``      — source proof has a gap (recoverable)
- ``proof_critical`` — source proof has a fatal error or is wrong
- ``cannot_verify``  — insufficient source passage to judge

## v1 scope note

The PDF extractor (deterministic locator → passage text) is
**deferred**. v1 takes pre-extracted source passages as input
(``--source-passage PATH`` to the CLI, or ``source_passage``
keyword to the role). The user can hand-extract or use a separate
LLM session to produce the passage from the paper. A future v1.5+
will add the deterministic extractor.

Re-use of ProofVerifier for the source proof is also deferred to
keep v1 contained. v1 emits one combined verdict per audit; if you
want a deeper proof check on the source proof text, run
``proof-verifier`` separately on a staged copy of the proof.
"""

from __future__ import annotations

from .decoder import (
    SourceClaimReview,
    SourceClaimReviewParseError,
    parse,
)
from .prompt import compose
from .role import AGENT_ROLE, SourceClaimVerifier

__all__ = [
    "AGENT_ROLE",
    "SourceClaimReview",
    "SourceClaimReviewParseError",
    "SourceClaimVerifier",
    "compose",
    "parse",
]
