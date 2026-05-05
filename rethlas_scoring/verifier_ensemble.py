"""Multi-verifier ensemble + Dawid-Skene aggregation — DESIGN §7.

Pure stdlib. ``Verifier`` is a Protocol so callers can plug in:

- ``CallableVerifier`` wrapping the existing ``verifier/role.py`` Codex
  pipeline (production).
- ``StubVerifier`` returning canned answers (tests).

``EnsembleVerifier`` runs k independent calls and aggregates with one
E-step of Dawid-Skene EM. Each worker maintains its own ``(p_tpr, p_fpr)``
posterior in ``WorkerConfusion``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Callable, Protocol, Sequence

from .calibration import BetaPosterior
from .data import DEFAULTS


# ---------------------------------------------------------------------------
# Worker confusion matrix.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class WorkerConfusion:
    """Per-worker (p_tpr, p_fpr) Beta posteriors."""

    worker_id: str
    tpr: BetaPosterior = BetaPosterior(
        alpha=DEFAULTS.roc_prior_alpha, beta=DEFAULTS.roc_prior_beta
    )
    fpr: BetaPosterior = BetaPosterior(
        alpha=DEFAULTS.roc_prior_alpha, beta=DEFAULTS.roc_prior_beta
    )

    def update(self, *, predicted_ok: bool, ground_truth_ok: bool) -> "WorkerConfusion":
        if ground_truth_ok:
            return replace(self, tpr=self.tpr.update(predicted_ok))
        return replace(self, fpr=self.fpr.update(predicted_ok))


# ---------------------------------------------------------------------------
# Verifier Protocol + stubs.
# ---------------------------------------------------------------------------
class Verifier(Protocol):
    """Minimal callable contract for one verifier worker."""

    worker_id: str

    def __call__(self, claim_text: str) -> tuple[bool, float]:
        """Return (predicted_ok, confidence ∈ [0, 1])."""
        ...


@dataclass(frozen=True, slots=True)
class CallableVerifier:
    """Adapter wrapping any ``(str) -> (bool, float)`` callable."""

    worker_id: str
    fn: Callable[[str], tuple[bool, float]]

    def __call__(self, claim_text: str) -> tuple[bool, float]:
        return self.fn(claim_text)


@dataclass(frozen=True, slots=True)
class StubVerifier:
    """Constant-answer verifier for tests."""

    worker_id: str
    answer: bool
    confidence: float = 0.9

    def __call__(self, claim_text: str) -> tuple[bool, float]:
        return self.answer, self.confidence


# ---------------------------------------------------------------------------
# Ensemble.
# ---------------------------------------------------------------------------
@dataclass
class EnsembleVerifier:
    workers: Sequence[Verifier]
    confusions: dict[str, WorkerConfusion] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for w in self.workers:
            self.confusions.setdefault(w.worker_id, WorkerConfusion(worker_id=w.worker_id))

    @property
    def k(self) -> int:
        return len(self.workers)

    def vote(self, claim_text: str) -> list[tuple[str, bool, float]]:
        """Run each worker once. Returns ``(worker_id, predicted_ok, conf)``."""
        results: list[tuple[str, bool, float]] = []
        for w in self.workers:
            ok, conf = w(claim_text)
            results.append((w.worker_id, ok, conf))
        return results

    def update_with_truth(
        self, *, results: Sequence[tuple[str, bool, float]], ground_truth_ok: bool
    ) -> None:
        """Push one labelled outcome into each worker's confusion."""
        for wid, predicted_ok, _conf in results:
            old = self.confusions.get(wid) or WorkerConfusion(worker_id=wid)
            self.confusions[wid] = old.update(
                predicted_ok=predicted_ok, ground_truth_ok=ground_truth_ok
            )


# ---------------------------------------------------------------------------
# Dawid-Skene aggregation.
# ---------------------------------------------------------------------------
def aggregate(
    results: Sequence[tuple[str, bool, float]],
    confusions: dict[str, WorkerConfusion],
    *,
    prior_p_true: float = 0.5,
) -> tuple[bool, float]:
    """Single-pass weighted vote.

    ``P(T=1 | obs) ∝ prior * Π_w P(obs_w | T=1)``,
    ``P(T=0 | obs) ∝ (1−prior) * Π_w P(obs_w | T=0)``.

    Returns ``(label, posterior_prob_of_label)``.
    """

    eps = DEFAULTS.prob_eps
    log_pos = math.log(max(eps, prior_p_true))
    log_neg = math.log(max(eps, 1.0 - prior_p_true))
    for wid, predicted_ok, _conf in results:
        c = confusions.get(wid) or WorkerConfusion(worker_id=wid)
        tpr = max(eps, min(1.0 - eps, c.tpr.mean()))
        fpr = max(eps, min(1.0 - eps, c.fpr.mean()))
        if predicted_ok:
            log_pos += math.log(tpr)
            log_neg += math.log(fpr)
        else:
            log_pos += math.log(1.0 - tpr)
            log_neg += math.log(1.0 - fpr)
    m = max(log_pos, log_neg)
    p_pos = math.exp(log_pos - m) / (math.exp(log_pos - m) + math.exp(log_neg - m))
    label = p_pos >= 0.5
    confidence = p_pos if label else 1.0 - p_pos
    return label, confidence


def disagreement(results: Sequence[tuple[str, bool, float]]) -> float:
    """``1 − |2·majority_fraction − 1|`` (DESIGN §7.3).

    1.0 when results are tied, 0.0 when unanimous.
    """

    if not results:
        return 0.0
    n_pos = sum(1 for _, ok, _ in results if ok)
    frac = n_pos / len(results)
    return 1.0 - abs(2.0 * frac - 1.0)


__all__ = [
    "CallableVerifier",
    "EnsembleVerifier",
    "StubVerifier",
    "Verifier",
    "WorkerConfusion",
    "aggregate",
    "disagreement",
]
