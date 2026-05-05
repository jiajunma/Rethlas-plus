"""Verifier calibration — DESIGN §3.

Two pieces:

1. ``IsotonicCalibrator`` — fit ``P(T=1 | LLM-confidence)`` as a monotone
   non-decreasing step function via PAVA (pool-adjacent-violators).
   Standard textbook algorithm.

2. ``VerifierROC`` — per-difficulty-bucket online Beta posterior over
   ``(p_tpr, p_fpr)``. Updates only when ground truth is known
   (DESIGN §3.1). The likelihood ratios ``LR_+ = p_tpr/p_fpr`` and
   ``LR_- = (1-p_tpr)/(1-p_fpr)`` consumed by ``voi.py`` come from the
   posterior means.

Pure stdlib.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field, replace

from .data import DEFAULTS


# ---------------------------------------------------------------------------
# IsotonicCalibrator (PAVA)
# ---------------------------------------------------------------------------
@dataclass
class IsotonicCalibrator:
    """Pool-adjacent-violators isotonic regression of P(y=1 | x).

    Mutable on purpose — calibration data accrues over time. ``fit``
    rebuilds the step function in O(n log n) (sort) + O(n) (PAVA pass).
    Below ``DEFAULTS.isotonic_min_samples`` the calibrator stays in
    "identity" mode (returns the input unchanged) so we don't poison the
    pipeline with under-fit junk.
    """

    _samples_x: list[float] = field(default_factory=list)
    _samples_y: list[int] = field(default_factory=list)
    _xs: list[float] = field(default_factory=list)
    _ys: list[float] = field(default_factory=list)
    _fitted: bool = False

    def add(self, x: float, y: bool) -> None:
        self._samples_x.append(float(x))
        self._samples_y.append(1 if y else 0)
        self._fitted = False

    def fit(self) -> None:
        n = len(self._samples_x)
        if n < DEFAULTS.isotonic_min_samples:
            self._xs = []
            self._ys = []
            self._fitted = True
            return

        # Sort by x; carry y along.
        order = sorted(range(n), key=lambda i: self._samples_x[i])
        xs = [self._samples_x[i] for i in order]
        ys = [float(self._samples_y[i]) for i in order]

        # PAVA: stack of [sum, count].
        stack: list[list[float]] = []
        for y in ys:
            stack.append([y, 1.0])
            while len(stack) >= 2 and (stack[-2][0] / stack[-2][1]) > (
                stack[-1][0] / stack[-1][1]
            ):
                top = stack.pop()
                stack[-1][0] += top[0]
                stack[-1][1] += top[1]

        # Expand pooled blocks back to (x, fitted_y) pairs.
        out_x: list[float] = []
        out_y: list[float] = []
        idx = 0
        for s, c in stack:
            mean = s / c
            block_size = int(c)
            for _ in range(block_size):
                out_x.append(xs[idx])
                out_y.append(mean)
                idx += 1
        self._xs = out_x
        self._ys = out_y
        self._fitted = True

    def predict(self, x: float) -> float:
        if not self._fitted:
            self.fit()
        if not self._xs:
            return max(0.0, min(1.0, x))  # identity mode
        # Right-anchored step function.
        i = bisect.bisect_right(self._xs, x) - 1
        if i < 0:
            return self._ys[0]
        return self._ys[min(i, len(self._ys) - 1)]


# ---------------------------------------------------------------------------
# Beta posterior helper
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class BetaPosterior:
    alpha: float
    beta: float

    def mean(self) -> float:
        denom = self.alpha + self.beta
        if denom <= 0:
            return 0.5
        return self.alpha / denom

    def update(self, success: bool) -> "BetaPosterior":
        if success:
            return replace(self, alpha=self.alpha + 1.0)
        return replace(self, beta=self.beta + 1.0)


# ---------------------------------------------------------------------------
# VerifierROC
# ---------------------------------------------------------------------------
def _bucket_for(claim_text: str, hardness: float, n_buckets: int = 5) -> int:
    """Map (token-length proxy, LLM-self-rated hardness) to one of n_buckets.

    DESIGN §3.1: "token length + LLM self-eval hardness, discretised
    into 5 buckets". We approximate token length with whitespace word
    count to avoid a tiktoken dependency.
    """

    if n_buckets <= 1:
        return 0
    word_count = max(1, len(claim_text.split()))
    length_signal = min(1.0, math.log(word_count + 1) / math.log(513.0))
    h = max(0.0, min(1.0, hardness))
    composite = 0.5 * length_signal + 0.5 * h
    raw = int(composite * n_buckets)
    return max(0, min(n_buckets - 1, raw))


@dataclass
class VerifierROC:
    """Per-difficulty-bucket Beta posterior over (p_tpr, p_fpr).

    The Beta(α, β) prior is ``Beta(roc_prior_alpha, roc_prior_beta)`` —
    default Beta(1,1) = uniform. Updates apply only when ground truth is
    available.
    """

    n_buckets: int = 5
    tpr_priors: dict[int, BetaPosterior] = field(default_factory=dict)
    fpr_priors: dict[int, BetaPosterior] = field(default_factory=dict)

    def _ensure(self, b: int) -> None:
        if b not in self.tpr_priors:
            self.tpr_priors[b] = BetaPosterior(
                alpha=DEFAULTS.roc_prior_alpha, beta=DEFAULTS.roc_prior_beta
            )
        if b not in self.fpr_priors:
            self.fpr_priors[b] = BetaPosterior(
                alpha=DEFAULTS.roc_prior_alpha, beta=DEFAULTS.roc_prior_beta
            )

    def bucket(self, claim_text: str, hardness: float) -> int:
        return _bucket_for(claim_text, hardness, self.n_buckets)

    def update(
        self, *, bucket_id: int, predicted_ok: bool, ground_truth_ok: bool
    ) -> None:
        """Update the appropriate Beta with one (predicted, truth) pair.

        - Truth=True  → updates p_tpr (success iff predicted_ok)
        - Truth=False → updates p_fpr (success iff predicted_ok)
        """

        self._ensure(bucket_id)
        if ground_truth_ok:
            self.tpr_priors[bucket_id] = self.tpr_priors[bucket_id].update(
                predicted_ok
            )
        else:
            self.fpr_priors[bucket_id] = self.fpr_priors[bucket_id].update(
                predicted_ok
            )

    def p_tpr(self, bucket_id: int) -> float:
        self._ensure(bucket_id)
        return self.tpr_priors[bucket_id].mean()

    def p_fpr(self, bucket_id: int) -> float:
        self._ensure(bucket_id)
        return self.fpr_priors[bucket_id].mean()

    def likelihood(self, bucket_id: int, *, observed_ok: bool, truth: bool) -> float:
        """P(Verify=observed_ok | T=truth) at the posterior mean."""
        eps = DEFAULTS.prob_eps
        tpr = max(eps, min(1.0 - eps, self.p_tpr(bucket_id)))
        fpr = max(eps, min(1.0 - eps, self.p_fpr(bucket_id)))
        if truth and observed_ok:
            return tpr
        if truth and not observed_ok:
            return 1.0 - tpr
        if not truth and observed_ok:
            return fpr
        return 1.0 - fpr

    def lr_plus(self, bucket_id: int) -> float:
        """LR_+ = p_tpr / p_fpr (DESIGN §3.2)."""
        eps = DEFAULTS.prob_eps
        return max(eps, self.p_tpr(bucket_id)) / max(eps, self.p_fpr(bucket_id))

    def lr_minus(self, bucket_id: int) -> float:
        """LR_- = (1-p_tpr) / (1-p_fpr)."""
        eps = DEFAULTS.prob_eps
        num = max(eps, 1.0 - self.p_tpr(bucket_id))
        den = max(eps, 1.0 - self.p_fpr(bucket_id))
        return num / den


# ---------------------------------------------------------------------------
# Lean-mode helper: a perfect verifier (calibration short-circuited).
# ---------------------------------------------------------------------------
def perfect_verifier_roc(n_buckets: int = 5) -> VerifierROC:
    """Return a VerifierROC pinned at (p_tpr=1, p_fpr=0) for every bucket.

    Used when the verifier is a Lean kernel (DESIGN §11). Posterior means
    are forced to the corner of ROC space by stuffing a huge α / β.
    """

    roc = VerifierROC(n_buckets=n_buckets)
    huge = 1e9
    for b in range(n_buckets):
        roc.tpr_priors[b] = BetaPosterior(alpha=huge, beta=1.0)  # mean → 1
        roc.fpr_priors[b] = BetaPosterior(alpha=1.0, beta=huge)  # mean → 0
    return roc


__all__ = [
    "BetaPosterior",
    "IsotonicCalibrator",
    "VerifierROC",
    "perfect_verifier_roc",
]
