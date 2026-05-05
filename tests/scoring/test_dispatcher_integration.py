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
