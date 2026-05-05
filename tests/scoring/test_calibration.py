"""calibration.py — IsotonicCalibrator + VerifierROC + perfect-mode."""

from __future__ import annotations

import random

from rethlas_scoring.calibration import (
    BetaPosterior,
    IsotonicCalibrator,
    VerifierROC,
    perfect_verifier_roc,
)
from rethlas_scoring.data import DEFAULTS


# ---------------------------------------------------------------------------
# IsotonicCalibrator
# ---------------------------------------------------------------------------
def test_isotonic_identity_below_min_samples() -> None:
    cal = IsotonicCalibrator()
    cal.add(0.2, True)
    cal.add(0.8, False)
    cal.fit()
    assert cal.predict(0.2) == 0.2
    assert cal.predict(0.8) == 0.8


def test_isotonic_pava_monotone() -> None:
    cal = IsotonicCalibrator()
    rng = random.Random(123)
    for _ in range(60):
        x = rng.random()
        y = rng.random() < x  # ground-truth probability == x
        cal.add(x, y)
    cal.fit()
    grid = [i / 100 for i in range(101)]
    preds = [cal.predict(g) for g in grid]
    assert all(b >= a - 1e-9 for a, b in zip(preds, preds[1:])), preds
    assert min(preds) >= 0.0
    assert max(preds) <= 1.0


# ---------------------------------------------------------------------------
# Beta + VerifierROC
# ---------------------------------------------------------------------------
def test_beta_posterior_mean_after_updates() -> None:
    p = BetaPosterior(alpha=1.0, beta=1.0)
    p = p.update(True).update(True).update(False)
    assert p.alpha == 3.0
    assert p.beta == 2.0
    assert abs(p.mean() - 0.6) < 1e-9


def test_verifier_roc_likelihood_consistency() -> None:
    roc = VerifierROC()
    for _ in range(9):
        roc.update(bucket_id=0, predicted_ok=True, ground_truth_ok=True)
    for _ in range(1):
        roc.update(bucket_id=0, predicted_ok=False, ground_truth_ok=True)
    for _ in range(1):
        roc.update(bucket_id=0, predicted_ok=True, ground_truth_ok=False)
    for _ in range(9):
        roc.update(bucket_id=0, predicted_ok=False, ground_truth_ok=False)
    p_ok_t1 = roc.likelihood(0, observed_ok=True, truth=True)
    p_fail_t1 = roc.likelihood(0, observed_ok=False, truth=True)
    assert abs(p_ok_t1 + p_fail_t1 - 1.0) < 1e-9
    assert roc.lr_plus(0) > 1.0
    assert roc.lr_minus(0) < 1.0


def test_perfect_verifier_roc_at_corners() -> None:
    roc = perfect_verifier_roc()
    eps = DEFAULTS.prob_eps
    for b in range(roc.n_buckets):
        assert roc.p_tpr(b) > 1.0 - 1e-6
        assert roc.p_fpr(b) < 1e-6
        assert roc.lr_plus(b) > 1.0 / (eps * 2)
