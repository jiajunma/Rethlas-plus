"""Cross-module invariants — collects checks not localised to one module.

I1 / I3 / I4 / I5 / I6 are tested in their respective modules. This file
holds I2 (`p̂ ∈ [0, 1]` always) plus mixed-flow regressions.
"""

from __future__ import annotations

import random

from rethlas_scoring.calibration import perfect_verifier_roc
from rethlas_scoring.cluster import ClusterIndex, propagate_failure
from rethlas_scoring.data import ProofGraph, ScoredNode
from rethlas_scoring.scheduler import Scheduler


def _seed_graph() -> ProofGraph:
    return ProofGraph.build(
        {
            "a": ScoredNode(
                id="a", claim_text="a", posterior_p=0.6, embedding=(1.0, 0.0, 0.0)
            ),
            "b": ScoredNode(
                id="b", claim_text="b", posterior_p=0.7, embedding=(1.0, 0.0, 0.0)
            ),
            "c": ScoredNode(
                id="c", claim_text="c", posterior_p=0.5,
                embedding=(0.0, 1.0, 0.0), depends_on=("a", "b"),
            ),
        }
    )


# I2 — propagate_failure result still in [0, 1].
def test_posterior_in_unit_interval_after_propagation() -> None:
    g = _seed_graph()
    cluster = ClusterIndex().build(g)
    g2 = propagate_failure(g, "a", cluster)
    for n in g2.nodes.values():
        assert 0.0 <= n.posterior_p <= 1.0


# I2 — Scheduler.observe_outcome end state still in [0, 1].
def test_posterior_in_unit_interval_after_observe_outcome() -> None:
    g = _seed_graph()
    sched = Scheduler(
        graph=g,
        roc=perfect_verifier_roc(),
        cluster=ClusterIndex().build(g),
        rng=random.Random(0),
        n_voi_samples=100,
    )
    sched.observe_outcome("a", verifier_says_ok=True, confidence=2.5)
    assert 0.0 <= sched.graph.get("a").posterior_p <= 1.0
    sched.observe_outcome("b", verifier_says_ok=False, confidence=2.5)
    assert 0.0 <= sched.graph.get("b").posterior_p <= 1.0


# Repeated propagation never amplifies (idempotent direction).
def test_repeated_propagation_only_decreases_posterior() -> None:
    g = _seed_graph()
    cluster = ClusterIndex().build(g)
    history = [g]
    cur = g
    for _ in range(5):
        cur = propagate_failure(cur, "a", cluster)
        history.append(cur)
    for prev, nxt in zip(history, history[1:]):
        for nid in prev.nodes:
            assert nxt.get(nid).posterior_p <= prev.get(nid).posterior_p + 1e-12
