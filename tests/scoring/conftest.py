"""Shared fixtures for the scoring test suite."""

from __future__ import annotations

import random

import pytest

from rethlas_scoring.calibration import (
    BetaPosterior,
    VerifierROC,
    perfect_verifier_roc,
)
from rethlas_scoring.cluster import ClusterIndex
from rethlas_scoring.data import NodeStatus, ProofGraph, ScoredNode


@pytest.fixture
def rng() -> random.Random:
    return random.Random(0xC0DE_5C0)


@pytest.fixture
def perfect_roc() -> VerifierROC:
    return perfect_verifier_roc()


@pytest.fixture
def useless_roc() -> VerifierROC:
    """Verifier with TPR == FPR == 0.5 → carries zero information."""
    roc = VerifierROC()
    for b in range(roc.n_buckets):
        roc.tpr_priors[b] = BetaPosterior(alpha=1.0, beta=1.0)
        roc.fpr_priors[b] = BetaPosterior(alpha=1.0, beta=1.0)
    return roc


def _node(
    node_id: str,
    *,
    p: float = 0.5,
    deps: tuple[str, ...] = (),
    emb: tuple[float, ...] = (),
    status: NodeStatus = NodeStatus.OPEN,
    claim: str | None = None,
) -> ScoredNode:
    return ScoredNode(
        id=node_id,
        claim_text=claim or f"claim {node_id}",
        status=status,
        prior_p=p,
        posterior_p=p,
        embedding=emb,
        depends_on=deps,
    )


@pytest.fixture
def singleton_graph() -> ProofGraph:
    return ProofGraph.build({"a": _node("a", p=0.5)})


@pytest.fixture
def chain_graph() -> ProofGraph:
    """a → b → c (each node depends on the previous)."""
    return ProofGraph.build(
        {
            "a": _node("a", p=0.6),
            "b": _node("b", p=0.5, deps=("a",)),
            "c": _node("c", p=0.4, deps=("b",)),
        }
    )


@pytest.fixture
def cluster_pair_graph() -> ProofGraph:
    """Two highly-similar nodes (cosine = 1) — cluster propagation target."""
    return ProofGraph.build(
        {
            "u": _node("u", p=0.6, emb=(1.0, 0.0, 0.0)),
            "v": _node("v", p=0.6, emb=(1.0, 0.0, 0.0)),
            "w": _node("w", p=0.6, emb=(0.0, 1.0, 0.0)),
        }
    )


@pytest.fixture
def cluster_index(cluster_pair_graph: ProofGraph) -> ClusterIndex:
    return ClusterIndex().build(cluster_pair_graph)


@pytest.fixture
def make_node():
    return _node
