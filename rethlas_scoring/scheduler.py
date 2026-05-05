"""Online scheduler — DESIGN §9.4 + §10.

Glues together :mod:`scorer`, :mod:`cluster`, and :mod:`voi`. Same
primitives are re-used by the offline ``make_priority_fn`` adapter that
``coordinator/dispatcher.py`` calls when ``use_voi_scoring=True`` (see
``docs/SCORING_INTEGRATION.md``).

Pure stdlib. Concurrency / process management lives in ``coordinator/`` —
this module is single-threaded scheduling math only.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .calibration import VerifierROC
from .cluster import ClusterIndex, propagate_failure
from .data import DEFAULTS, NodeStatus, ProofGraph, ScoredNode
from .scorer import Score, compute_score, thompson_sample
from .voi import estimate_p_z


# ---------------------------------------------------------------------------
# Scheduler state.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ScheduleStep:
    tick: int
    picked: tuple[str, ...]
    verified_fraction: float


@dataclass
class Scheduler:
    """Single-process scheduler over a ProofGraph.

    The graph is held by reference and replaced via the immutable
    ``ProofGraph.replace_node`` helper to keep I2 (`p̂ ∈ [0,1]`).
    ``dispatched`` enforces I6 (no node leaves the Pareto front twice
    until its status changes).
    """

    graph: ProofGraph
    roc: VerifierROC
    cluster: ClusterIndex
    rng: random.Random = field(default_factory=lambda: random.Random(0xDEADBEEF))
    dispatched: set[str] = field(default_factory=set)
    history: list[ScheduleStep] = field(default_factory=list)
    n_voi_samples: int | None = None

    # ---- core loop ------------------------------------------------------
    def verified_fraction(self) -> float:
        nodes = self.graph.nodes
        if not nodes:
            return 1.0
        n_done = sum(1 for n in nodes.values() if n.status is NodeStatus.VERIFIED)
        return n_done / len(nodes)

    def _candidate_ids(self) -> list[str]:
        return [
            nid
            for nid, n in self.graph.nodes.items()
            if n.status in (NodeStatus.OPEN, NodeStatus.PROPOSED)
            and nid not in self.dispatched
        ]

    def step(self, *, capacity: int = 1) -> list[Score]:
        """Pick ``capacity`` next nodes to verify; return chosen Scores."""
        cand_ids = self._candidate_ids()
        if not cand_ids:
            return []
        scores = [
            compute_score(
                nid,
                self.graph,
                self.roc,
                cluster=self.cluster,
                n_voi_samples=self.n_voi_samples,
                rng=self.rng,
            )
            for nid in cand_ids
        ]
        picked = thompson_sample(
            scores,
            verified_fraction=self.verified_fraction(),
            rng=self.rng,
            k=capacity,
        )
        for s in picked:
            self.dispatched.add(s.node_id)
        self.history.append(
            ScheduleStep(
                tick=len(self.history),
                picked=tuple(s.node_id for s in picked),
                verified_fraction=self.verified_fraction(),
            )
        )
        return picked

    # ---- outcome handlers ------------------------------------------------
    def observe_outcome(
        self,
        node_id: str,
        *,
        verifier_says_ok: bool,
        confidence: float,
        cancel_descendants: bool = True,
    ) -> set[str]:
        """Apply one verifier outcome; return descendant ids to preempt."""
        node = self.graph.get(node_id)
        new_status = NodeStatus.VERIFIED if verifier_says_ok else NodeStatus.REFUTED
        new_post = (
            max(0.0, min(1.0, confidence))
            if verifier_says_ok
            else max(0.0, 1.0 - confidence)
        )
        updated = node.with_status(new_status).with_posterior(new_post)
        self.graph = self.graph.replace_node(updated)

        cancel: set[str] = set()
        if not verifier_says_ok:
            self.graph = propagate_failure(self.graph, node_id, self.cluster)
        if cancel_descendants:
            cancel = self.graph.descendants(node_id)
        return cancel

    # ---- termination ----------------------------------------------------
    def certifying_set_done(self, *, epsilon: float | None = None) -> bool:
        """DESIGN §10 termination: ``P(Z=1 | S verified) ≥ 1 − ε``."""
        eps = epsilon or DEFAULTS.certify_epsilon
        snapshot_nodes: dict[str, ScoredNode] = {}
        for nid, n in self.graph.nodes.items():
            snapshot_nodes[nid] = (
                n.with_posterior(1.0) if n.status is NodeStatus.VERIFIED else n
            )
        snapshot = ProofGraph(nodes=snapshot_nodes, children=self.graph.children)
        p_z = estimate_p_z(snapshot, n_samples=self.n_voi_samples, rng=self.rng)
        return p_z >= (1.0 - eps)


# ---------------------------------------------------------------------------
# Coordinator integration adapter — DESIGN §9 + AUDIT §4.2.
# ---------------------------------------------------------------------------
def make_priority_fn(
    graph: ProofGraph,
    roc: VerifierROC,
    cluster: ClusterIndex,
    *,
    rng: random.Random | None = None,
    n_voi_samples: int | None = None,
) -> Callable[[Sequence[str], int], list[str]]:
    """Return a priority function compatible with ``coordinator.dispatcher``.

    The returned callable takes ``(candidate_labels, capacity)`` and
    yields up to ``capacity`` labels in priority order. On any error /
    missing node it falls back to the input order so a bad scoring layer
    can never starve the dispatcher.
    """

    rng = rng or random.Random(0x5EED5)

    def _priority_fn(candidates: Sequence[str], capacity: int) -> list[str]:
        if capacity <= 0:
            return []
        valid = [c for c in candidates if c in graph.nodes]
        if not valid:
            return list(candidates[:capacity])
        scores = [
            compute_score(
                nid,
                graph,
                roc,
                cluster=cluster,
                n_voi_samples=n_voi_samples,
                rng=rng,
            )
            for nid in valid
        ]
        n_total = len(graph.nodes)
        n_done = sum(
            1 for n in graph.nodes.values() if n.status is NodeStatus.VERIFIED
        )
        vf = n_done / n_total if n_total else 1.0
        picked = thompson_sample(scores, verified_fraction=vf, rng=rng, k=capacity)
        return [s.node_id for s in picked]

    return _priority_fn


__all__ = [
    "ScheduleStep",
    "Scheduler",
    "make_priority_fn",
]
