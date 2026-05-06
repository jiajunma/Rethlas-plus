"""End-to-end: coordinator.dispatcher with the rethlas_scoring priority_fn.

Validates the rollback toggle: same input → identical output without
``priority_fn``; reordered output (and never starves a label) when
``priority_fn`` is plugged in.
"""

from __future__ import annotations

import random
from typing import Sequence

from coordinator.dispatcher import VerifierCandidate, select_verifier_targets
from rethlas_scoring.calibration import perfect_verifier_roc
from rethlas_scoring.cluster import ClusterIndex
from rethlas_scoring.data import ProofGraph, ScoredNode
from rethlas_scoring.scheduler import make_priority_fn


def _candidates() -> list[VerifierCandidate]:
    return [
        VerifierCandidate(label="lem:a", pass_count=0),
        VerifierCandidate(label="lem:b", pass_count=0),
        VerifierCandidate(label="lem:c", pass_count=0),
    ]


def _graph_with_high_voi_on_b() -> ProofGraph:
    return ProofGraph.build(
        {
            "lem:a": ScoredNode(id="lem:a", claim_text="a", posterior_p=0.99),
            "lem:b": ScoredNode(id="lem:b", claim_text="b", posterior_p=0.5),
            "lem:c": ScoredNode(id="lem:c", claim_text="c", posterior_p=0.99),
        }
    )


def test_dispatcher_legacy_path_unchanged() -> None:
    out = select_verifier_targets(
        _candidates(), capacity=2, in_flight_targets=set()
    )
    assert out == ["lem:a", "lem:b"]


def test_dispatcher_voi_path_prefers_high_voi_node() -> None:
    g = _graph_with_high_voi_on_b()
    fn = make_priority_fn(
        graph=g,
        roc=perfect_verifier_roc(),
        cluster=ClusterIndex().build(g),
        rng=random.Random(0),
        n_voi_samples=400,
    )
    out = select_verifier_targets(
        _candidates(),
        capacity=1,
        in_flight_targets=set(),
        priority_fn=fn,
    )
    assert out == ["lem:b"]


def test_dispatcher_voi_path_never_starves_unscored_labels() -> None:
    def empty_fn(_labels: Sequence[str], _cap: int) -> list[str]:
        return []

    out = select_verifier_targets(
        _candidates(),
        capacity=2,
        in_flight_targets=set(),
        priority_fn=empty_fn,
    )
    assert sorted(out) == ["lem:a", "lem:b"]


def test_dispatcher_voi_path_handles_priority_fn_exception() -> None:
    def bomb(_labels: Sequence[str], _cap: int) -> list[str]:
        raise RuntimeError("scorer crashed")

    out = select_verifier_targets(
        _candidates(),
        capacity=2,
        in_flight_targets=set(),
        priority_fn=bomb,
    )
    # Legacy ordering still applied — dispatcher cannot regress.
    assert out == ["lem:a", "lem:b"]


def test_dispatcher_voi_path_respects_in_flight_targets() -> None:
    g = _graph_with_high_voi_on_b()
    fn = make_priority_fn(
        graph=g,
        roc=perfect_verifier_roc(),
        cluster=ClusterIndex().build(g),
        rng=random.Random(0),
        n_voi_samples=400,
    )
    out = select_verifier_targets(
        _candidates(),
        capacity=1,
        in_flight_targets={"lem:b"},
        priority_fn=fn,
    )
    assert len(out) == 1
    assert out[0] in {"lem:a", "lem:c"}


# ---------------------------------------------------------------------------
# S1 — tier-strict BFS by pass_count (SCORING_SCHEDULING.md §3).
# ---------------------------------------------------------------------------
def _mixed_tier_candidates() -> list[VerifierCandidate]:
    """Two tier-0 nodes + one tier-1 node, all eligible.

    Legacy ordering: ``lem:a`` (tier 0), ``lem:b`` (tier 0), then ``lem:c``
    (tier 1).
    """
    return [
        VerifierCandidate(label="lem:a", pass_count=0),
        VerifierCandidate(label="lem:b", pass_count=0),
        VerifierCandidate(label="lem:c", pass_count=1),
    ]


def _graph_high_voi_on_tier1_node() -> ProofGraph:
    """``lem:c`` (the tier-1 node) carries the only "interesting" posterior.

    A tier-blind priority_fn would put ``lem:c`` first; tier-strict
    ordering must still pick the tier-0 nodes ahead.
    """
    return ProofGraph.build(
        {
            "lem:a": ScoredNode(id="lem:a", claim_text="a", posterior_p=0.99),
            "lem:b": ScoredNode(id="lem:b", claim_text="b", posterior_p=0.99),
            "lem:c": ScoredNode(id="lem:c", claim_text="c", posterior_p=0.5),
        }
    )


def test_tier_constraint_lower_pass_count_first() -> None:
    """A high-VOI tier-1 node must lose to lower-VOI tier-0 nodes."""
    g = _graph_high_voi_on_tier1_node()
    fn = make_priority_fn(
        graph=g,
        roc=perfect_verifier_roc(),
        cluster=ClusterIndex().build(g),
        rng=random.Random(0),
        n_voi_samples=400,
    )
    out = select_verifier_targets(
        _mixed_tier_candidates(),
        capacity=2,
        in_flight_targets=set(),
        priority_fn=fn,
    )
    # Tier 0 has two members; both must come before tier 1's ``lem:c``.
    assert sorted(out) == ["lem:a", "lem:b"]
    assert "lem:c" not in out


def test_voi_ordering_within_tier_still_applies() -> None:
    """Within tier 0, VOI ordering should pick the higher-VOI node first."""
    g = ProofGraph.build(
        {
            "lem:a": ScoredNode(id="lem:a", claim_text="a", posterior_p=0.99),
            "lem:b": ScoredNode(id="lem:b", claim_text="b", posterior_p=0.5),
            "lem:c": ScoredNode(id="lem:c", claim_text="c", posterior_p=1.0),
        }
    )
    fn = make_priority_fn(
        graph=g,
        roc=perfect_verifier_roc(),
        cluster=ClusterIndex().build(g),
        rng=random.Random(0),
        n_voi_samples=400,
    )
    candidates = [
        VerifierCandidate(label="lem:a", pass_count=0),
        VerifierCandidate(label="lem:b", pass_count=0),
        VerifierCandidate(label="lem:c", pass_count=0),
    ]
    out = select_verifier_targets(
        candidates,
        capacity=1,
        in_flight_targets=set(),
        priority_fn=fn,
    )
    # ``lem:b`` has posterior 0.5 → maximal VOI; should be picked first.
    assert out == ["lem:b"]


def test_tier_constraint_spills_when_lower_tier_fully_busy() -> None:
    """If every tier-0 candidate is in_flight, dispatcher may move to tier 1."""
    g = _graph_high_voi_on_tier1_node()
    fn = make_priority_fn(
        graph=g,
        roc=perfect_verifier_roc(),
        cluster=ClusterIndex().build(g),
        rng=random.Random(0),
        n_voi_samples=400,
    )
    out = select_verifier_targets(
        _mixed_tier_candidates(),
        capacity=1,
        in_flight_targets={"lem:a", "lem:b"},  # tier 0 fully busy
        priority_fn=fn,
    )
    # Only tier-1 candidate left and capacity > 0 → spill to tier 1.
    assert out == ["lem:c"]


def test_legacy_dispatcher_path_alphabetical_within_tier() -> None:
    """``priority_fn=None`` keeps the (pass_count asc, label asc) ordering."""
    candidates = [
        VerifierCandidate(label="thm:z", pass_count=0),
        VerifierCandidate(label="lem:y", pass_count=0),
        VerifierCandidate(label="lem:a", pass_count=1),
    ]
    out = select_verifier_targets(
        candidates,
        capacity=3,
        in_flight_targets=set(),
        # priority_fn=None — legacy alphabetical-within-tier
    )
    # Tier 0: ``lem:y, thm:z`` (alphabetical). Tier 1: ``lem:a``.
    assert out == ["lem:y", "thm:z", "lem:a"]


def test_priority_fn_failure_falls_back_per_tier_not_globally() -> None:
    """A scorer crash on tier 0 must still let tier 1 dispatch normally."""
    crash_count = {"n": 0}

    def selective_bomb(labels, _capacity):
        crash_count["n"] += 1
        if "lem:a" in labels or "lem:b" in labels:
            raise RuntimeError("scorer crash on tier 0")
        return list(labels)

    out = select_verifier_targets(
        _mixed_tier_candidates(),
        capacity=3,
        in_flight_targets=set(),
        priority_fn=selective_bomb,
    )
    # Tier-0 still alphabetical (fallback), tier-1 still works.
    assert out == ["lem:a", "lem:b", "lem:c"]
    # priority_fn was called per tier (not globally).
    assert crash_count["n"] >= 2
