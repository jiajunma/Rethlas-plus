"""Cluster propagation — DESIGN §5.

When verifier (or refute) reports failure on a node, push down the
posterior of every neighbour whose embedding cosine exceeds the
threshold τ. The update ``p̂ ← p̂·(1 − γ·sim)`` is monotone-decreasing
(I3) by construction since ``γ·sim ∈ [0, 1)``.

Pure stdlib — cosine over ``tuple[float, ...]`` embeddings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .data import DEFAULTS, ProofGraph, ScoredNode


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Cosine similarity in [-1, 1]; returns 0 for any zero vector."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    # Compute as product of sqrts (not sqrt of product) so the
    # intermediate ``na * nb`` doesn't underflow to 0.0 for very small
    # embeddings (e.g. ``(0, 0, 1e-156)``) and trip ZeroDivisionError.
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom <= 0.0:
        return 0.0
    return dot / denom


@dataclass
class ClusterIndex:
    """Caches pairwise cosine similarity for the current ProofGraph.

    Treat it as ephemeral — rebuild whenever node embeddings change. The
    cache is keyed by ``(min_id, max_id)`` so the lookup is symmetric.
    """

    threshold: float = DEFAULTS.embedding_threshold
    decay: float = DEFAULTS.cluster_decay
    _sims: dict[tuple[str, str], float] = field(default_factory=dict)

    @staticmethod
    def _pair(a: str, b: str) -> tuple[str, str]:
        return (a, b) if a <= b else (b, a)

    def build(self, graph: ProofGraph) -> "ClusterIndex":
        """Recompute pairwise sims for all nodes whose embeddings are non-empty."""
        self._sims.clear()
        ids = sorted(graph.nodes.keys())
        for i, u in enumerate(ids):
            emb_u = graph.get(u).embedding
            if not emb_u:
                continue
            for v in ids[i + 1 :]:
                emb_v = graph.get(v).embedding
                if not emb_v:
                    continue
                self._sims[self._pair(u, v)] = cosine(emb_u, emb_v)
        return self

    def sim(self, u: str, v: str) -> float:
        if u == v:
            return 1.0
        return self._sims.get(self._pair(u, v), 0.0)

    def neighbours(self, u: str) -> list[tuple[str, float]]:
        """Nodes whose adjusted similarity ``max(0, sim − τ)`` is positive."""
        out: list[tuple[str, float]] = []
        for (a, b), s in self._sims.items():
            other = b if a == u else (a if b == u else None)
            if other is None:
                continue
            adj = max(0.0, s - self.threshold)
            if adj > 0.0:
                out.append((other, adj))
        return out


def propagate_failure(
    graph: ProofGraph,
    failed_id: str,
    cluster: ClusterIndex,
    *,
    severity: float = 1.0,
) -> ProofGraph:
    """Apply DESIGN §5.2 update: ``p̂(v) ← p̂(v)·(1 − γ·sim·severity)``.

    ``severity`` defaults to 1.0 (a hard verifier failure). ``refute.py``
    feeds in a value in [0, 1] from the refute task verdict.

    Returns a new ProofGraph (the input is left untouched).
    """

    severity = max(0.0, min(1.0, severity))
    if severity == 0.0:
        return graph

    # I3: build the new node table without ever increasing posterior_p.
    new_nodes: dict[str, ScoredNode] = dict(graph.nodes)
    for v_id, sim_adj in cluster.neighbours(failed_id):
        decay_factor = 1.0 - cluster.decay * sim_adj * severity
        # decay_factor ∈ [0, 1] ⇒ posterior is monotone non-increasing.
        decay_factor = max(0.0, min(1.0, decay_factor))
        v = new_nodes[v_id]
        new_nodes[v_id] = v.with_posterior(v.posterior_p * decay_factor)

    return ProofGraph(nodes=new_nodes, children=graph.children)


def cluster_suspicion(
    node_id: str,
    graph: ProofGraph,
    cluster: ClusterIndex,
) -> float:
    """How much downward pressure the cluster puts on this node.

    ``Σ_{u: sim(u,v) > τ} (1 − p̂(u)) · sim_adj(u, v)`` — consumed as
    ``Score.cluster_susp``.
    """

    total = 0.0
    for u_id, sim_adj in cluster.neighbours(node_id):
        u = graph.get(u_id)
        signal = 1.0 - u.posterior_p
        if signal <= 0.0:
            continue
        total += signal * sim_adj
    return total


__all__ = [
    "ClusterIndex",
    "cluster_suspicion",
    "cosine",
    "propagate_failure",
]
