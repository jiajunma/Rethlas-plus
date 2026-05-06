"""Refute prompt assembler (S4-light).

The refute task asks an LLM to play hostile reviewer of a single claim:
find a counterexample, edge case, hidden assumption, or otherwise
declare "no apparent issue". The strict JSON output schema is decoded
in :mod:`refute.decoder` to a ``RefuteVerdict``.

The prompt template lives in
:data:`rethlas_scoring.refute.REFUTE_PROMPT` so the scoring layer's
documentation can reference one canonical text. This module only
binds the runtime parameters (currently just the claim).
"""

from __future__ import annotations

from rethlas_scoring.refute import REFUTE_PROMPT


def compose_prompt(claim_text: str) -> str:
    """Render :data:`REFUTE_PROMPT` for a single claim.

    Trims leading/trailing whitespace on the input — the template
    embeds the claim inside a ``CLAIM:`` block, so stray newlines hurt
    LLM compliance with the JSON schema.
    """

    return REFUTE_PROMPT.format(claim=claim_text.strip())


__all__ = ["REFUTE_PROMPT", "compose_prompt"]
