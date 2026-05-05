"""scheduler.py — invariants I5 (certify-set termination) + I6 (no double dispatch).

Plus the ``make_priority_fn`` adapter contract used by
``coordinator/dispatcher.py`` when ``use_voi_scoring=True``.
"""

from __future__ import annotations

import random

from rethlas_scoring.calibration import perfect_verifier_roc
from rethlas_scoring.cluster import ClusterIndex
from rethlas_scoring.data import NodeStatus, ProofGraph, ScoredNode
from rethlas_scoring.scheduler import Scheduler, make_priority_fn


def _g_two_open() -> ProofGraph:
    return ProofGraph.build(
        {
            "a": ScoredNode(id="a", claim_text="a", posterior_p=0.5),
            "b": ScoredNode(id="b", claim_text="b", posterior_p=0.5),
        }
    )


# I6
def test_step_marks_dispatched_and_skips_repeat() -> None:
    g = _g_two_open()
    sched = Scheduler(
        graph=g,
        roc=perfect_verifier_roc(),
        cluster=ClusterIndex().build(g),
        rng=random.Random(0),
        n_voi_samples=200,
    )
    first = sched.step(capacity=1)
    assert len(first) == 1
    second = sched.step(capacity=1)
    assert len(second) == 1
    assert first[0].node_id != second[0].node_id
    third = sched.step(capacity=1)
    assert third == []


# I5
def test_certifying_set_done_when_all_verified() -> None:
    g = ProofGraph.build(
        {
            "a": ScoredNode(
                id="a", claim_text="a", posterior_p=0.99,
                status=NodeStatus.VERIFIED,
            ),
            "b": ScoredNode(
                id="b", claim_text="b", posterior_p=0.99,
                status=NodeStatus.VERIFIED,
            ),
        }
    )
    sched = Scheduler(
        graph=g,
        roc=perfect_verifier_roc(),
        cluster=ClusterIndex().build(g),
        rng=random.Random(0),
        n_voi_samples=200,
    )
    assert sched.certifying_set_done(epsilon=0.05)


def test_certifying_set_not_done_at_low_posteriors() -> None:
    g = _g_two_open()
    sched = Scheduler(
        graph=g,
        roc=perfect_verifier_roc(),
        cluster=ClusterIndex().build(g),
        rng=random.Random(0),
        n_voi_samples=2000,
    )
    assert not sched.certifying_set_done(epsilon=0.01)


def test_observe_outcome_failure_cancels_descendants() -> None:
    g = ProofGraph.build(
        {
            "a": ScoredNode(id="a", claim_text="a", posterior_p=0.5),
            "b": ScoredNode(
                id="b", claim_text="b", posterior_p=0.5, depends_on=("a",)
            ),
        }
    )
    sched = Scheduler(
        graph=g,
        roc=perfect_verifier_roc(),
        cluster=ClusterIndex().build(g),
        rng=random.Random(0),
        n_voi_samples=200,
    )
    cancel = sched.observe_outcome(
        "a", verifier_says_ok=False, confidence=0.9, cancel_descendants=True
    )
    assert "b" in cancel
    assert sched.graph.get("a").status is NodeStatus.REFUTED


def test_make_priority_fn_returns_subset_of_candidates() -> None:
    g = _g_two_open()
    fn = make_priority_fn(
        graph=g,
        roc=perfect_verifier_roc(),
        cluster=ClusterIndex().build(g),
        rng=random.Random(0),
        n_voi_samples=200,
    )
    out = fn(["a", "b"], 1)
    assert len(out) == 1
    assert out[0] in {"a", "b"}


def test_make_priority_fn_handles_unknown_labels_safely() -> None:
    g = _g_two_open()
    fn = make_priority_fn(
        graph=g,
        roc=perfect_verifier_roc(),
        cluster=ClusterIndex().build(g),
        rng=random.Random(0),
        n_voi_samples=100,
    )
    out = fn(["xxx", "yyy"], 2)
    # No candidates known → fall back to input order.
    assert out == ["xxx", "yyy"]
