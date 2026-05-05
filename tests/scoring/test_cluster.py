"""cluster.py — invariant I3 (monotone-decreasing posterior on propagation)."""

from __future__ import annotations

from rethlas_scoring.cluster import (
    ClusterIndex,
    cluster_suspicion,
    cosine,
    propagate_failure,
)
from rethlas_scoring.data import ProofGraph, ScoredNode


def test_cosine_known_pairs() -> None:
    assert cosine((1.0, 0.0), (1.0, 0.0)) == 1.0
    assert cosine((1.0, 0.0), (0.0, 1.0)) == 0.0
    assert abs(cosine((1.0, 1.0), (1.0, 0.0)) - 1.0 / 2 ** 0.5) < 1e-12
    assert cosine((), (1.0,)) == 0.0


def test_cosine_zero_vector_safe() -> None:
    assert cosine((0.0, 0.0), (1.0, 0.0)) == 0.0


# I3
def test_propagate_failure_monotone_decreasing(cluster_pair_graph, cluster_index) -> None:
    before = {nid: n.posterior_p for nid, n in cluster_pair_graph.nodes.items()}
    after_g = propagate_failure(cluster_pair_graph, "u", cluster_index)
    for nid, n in after_g.nodes.items():
        assert n.posterior_p <= before[nid] + 1e-12, nid
    # The similar neighbour 'v' should drop strictly; 'w' (sim < τ) unchanged.
    assert after_g.get("v").posterior_p < before["v"]
    assert abs(after_g.get("w").posterior_p - before["w"]) < 1e-12


def test_severity_zero_is_noop(cluster_pair_graph, cluster_index) -> None:
    after_g = propagate_failure(cluster_pair_graph, "u", cluster_index, severity=0.0)
    for nid, n in after_g.nodes.items():
        assert n.posterior_p == cluster_pair_graph.get(nid).posterior_p


def test_cluster_suspicion_picks_up_low_neighbour() -> None:
    g = ProofGraph.build(
        {
            "u": ScoredNode(id="u", claim_text="u", posterior_p=0.1, embedding=(1.0, 0.0)),
            "v": ScoredNode(id="v", claim_text="v", posterior_p=0.5, embedding=(1.0, 0.0)),
        }
    )
    idx = ClusterIndex().build(g)
    susp_v = cluster_suspicion("v", g, idx)
    assert susp_v > 0.0
