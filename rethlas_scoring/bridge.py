"""Bridge audit — DESIGN §6.

Three-stage check before two frontiers (F, B) are declared bridged:

1. Embedding cosine ≥ ``DEFAULTS.bridge_cosine_threshold`` (= 0.85).
2. LLM equivalence judge says ``YES_strict`` or ``YES_modulo_renaming``.
3. Refute task on the combined claim does **not** find a counterexample.

If all three pass, the bridge is accepted but flagged for end-of-proof
re-verification (DESIGN §6.2).

Pure stdlib. The LLM equivalence backend is injected as a Callable so
this file has no LLM dependency at import time.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .cluster import cosine
from .data import DEFAULTS, ScoredNode
from .refute import RefuteTask, RefuteVerdict


class EquivalenceJudgement(str, Enum):
    YES_STRICT = "YES_strict"
    YES_MODULO_RENAMING = "YES_modulo_renaming"
    NO = "NO"


EquivalenceJudge = Callable[[str, str], EquivalenceJudgement]


@dataclass(frozen=True, slots=True)
class BridgeAuditReport:
    accepted: bool
    requires_endgame_recheck: bool
    reason: str
    cosine_value: float
    judge_value: EquivalenceJudgement | None = None
    refute_verdict: RefuteVerdict | None = None


@dataclass
class BridgeAudit:
    """Three-stage equivalence audit for a candidate bridge ``(u, v)``."""

    judge: EquivalenceJudge
    refuter: RefuteTask
    cosine_threshold: float = DEFAULTS.bridge_cosine_threshold

    def audit(self, u: ScoredNode, v: ScoredNode) -> BridgeAuditReport:
        cos = cosine(u.embedding, v.embedding)
        if cos < self.cosine_threshold:
            return BridgeAuditReport(
                accepted=False,
                requires_endgame_recheck=False,
                reason=f"cosine {cos:.3f} below threshold {self.cosine_threshold:.3f}",
                cosine_value=cos,
            )
        verdict = self.judge(u.claim_text, v.claim_text)
        if verdict is EquivalenceJudgement.NO:
            return BridgeAuditReport(
                accepted=False,
                requires_endgame_recheck=False,
                reason="judge: not equivalent",
                cosine_value=cos,
                judge_value=verdict,
            )
        combined = f"({u.claim_text}) ⇒ ({v.claim_text})"
        refute_v = self.refuter(combined)
        if refute_v.found_counterexample:
            return BridgeAuditReport(
                accepted=False,
                requires_endgame_recheck=False,
                reason="refute: counterexample found",
                cosine_value=cos,
                judge_value=verdict,
                refute_verdict=refute_v,
            )
        return BridgeAuditReport(
            accepted=True,
            requires_endgame_recheck=True,  # §6.2 always re-verify end-of-proof
            reason="passed cosine + judge + refute",
            cosine_value=cos,
            judge_value=verdict,
            refute_verdict=refute_v,
        )


__all__ = [
    "BridgeAudit",
    "BridgeAuditReport",
    "EquivalenceJudge",
    "EquivalenceJudgement",
]
