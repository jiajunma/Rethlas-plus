"""voi.py — invariant I1 + four boundary cases from DESIGN §4.3."""

from __future__ import annotations

import math
import random

from rethlas_scoring.calibration import (
    BetaPosterior,
    VerifierROC,
    perfect_verifier_roc,
)
from rethlas_scoring.data import ProofGraph, ScoredNode
from rethlas_scoring.voi import binary_entropy, estimate_p_z, sem_blast, voi_node


def _make_singleton(p: float) -> ProofGraph:
    return ProofGraph.build(
        {"a": ScoredNode(id="a", claim_text="x", posterior_p=p)}
    )


# I1
def test_voi_nonneg_random_graphs() -> None:
    rng = random.Random(7)
    roc = perfect_verifier_roc()
    for _ in range(50):
        p = rng.random()
        g = _make_singleton(p)
        v = voi_node("a", g, roc, n_samples=200, rng=rng)
        assert v >= 0.0


# Boundary 1: p̂ → 0
def test_voi_zero_when_posterior_zero() -> None:
    roc = perfect_verifier_roc()
    g = _make_singleton(0.0001)
    v = voi_node("a", g, roc, n_samples=2000, rng=random.Random(1))
    assert v < 0.05


# Boundary 2: p̂ → 1
def test_voi_zero_when_posterior_one() -> None:
    roc = perfect_verifier_roc()
    g = _make_singleton(0.9999)
    v = voi_node("a", g, roc, n_samples=2000, rng=random.Random(2))
    assert v < 0.05


# Boundary 3: perfect verifier + p̂=0.5 → VOI ≈ H(0.5) = ln 2
def test_voi_perfect_verifier_at_half_recovers_log2() -> None:
    roc = perfect_verifier_roc()
    g = _make_singleton(0.5)
    v = voi_node("a", g, roc, n_samples=4000, rng=random.Random(3))
    expected = binary_entropy(0.5)
    assert abs(v - expected) < 0.05


# Boundary 4: useless verifier → VOI == 0 regardless of prior
def test_voi_zero_when_verifier_useless() -> None:
    roc = VerifierROC()
    for b in range(roc.n_buckets):
        roc.tpr_priors[b] = BetaPosterior(alpha=1.0, beta=1.0)
        roc.fpr_priors[b] = BetaPosterior(alpha=1.0, beta=1.0)
    for p in (0.1, 0.3, 0.5, 0.7, 0.9):
        g = _make_singleton(p)
        v = voi_node("a", g, roc, n_samples=2000, rng=random.Random(4))
        assert v < 1e-6


def test_binary_entropy_known_values() -> None:
    assert binary_entropy(0.0) == 0.0
    assert binary_entropy(1.0) == 0.0
    assert abs(binary_entropy(0.5) - math.log(2)) < 1e-12


def test_sem_blast_sums_descendants() -> None:
    nodes = {
        "a": ScoredNode(id="a", claim_text="a"),
        "b": ScoredNode(id="b", claim_text="b", depends_on=("a",), speculative_load=2.0),
        "c": ScoredNode(id="c", claim_text="c", depends_on=("b",), speculative_load=3.0),
    }
    g = ProofGraph.build(nodes)
    assert abs(sem_blast("a", g) - 5.0) < 1e-12


def test_estimate_p_z_increases_with_posteriors() -> None:
    low = ProofGraph.build(
        {
            "a": ScoredNode(id="a", claim_text="a", posterior_p=0.1),
            "b": ScoredNode(id="b", claim_text="b", posterior_p=0.1),
        }
    )
    high = ProofGraph.build(
        {
            "a": ScoredNode(id="a", claim_text="a", posterior_p=0.9),
            "b": ScoredNode(id="b", claim_text="b", posterior_p=0.9),
        }
    )
    p_low = estimate_p_z(low, n_samples=2000, rng=random.Random(5))
    p_high = estimate_p_z(high, n_samples=2000, rng=random.Random(6))
    assert p_high > p_low
