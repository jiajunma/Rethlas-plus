"""Multi-dimensional scoring + Pareto front + Thompson sampling — DESIGN §9.

Avoids H7 (cost monotonicity bug) by composing priority as
``info_value / max(cost, ε)`` rather than ``info_value − δ·cost``.

Avoids H8 (single scalar) by exposing a 6-D ``Score`` dataclass; selection
operates on the Pareto front of ``(voi, risk, disagreement, −cost)``.

Pure stdlib. Random sampling uses ``random.Random`` so callers can seed
for tests.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence

from .calibration import VerifierROC
from .cluster import ClusterIndex, cluster_suspicion
from .data import DEFAULTS, ProofGraph
from .voi import sem_blast, voi_node


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Score:
    """Six-dimensional scoring vector (DESIGN §9.1).

    All fields are floats; ``cost`` is the only one whose contribution to
    priority is **divisive** rather than additive.
    """

    node_id: str
    voi: float
    risk: float
    cluster_susp: float
    bridge_bonus: float
    disagreement: float
    cost: float

    def to_pareto_tuple(self) -> tuple[float, float, float, float]:
        """Tuple to maximise: ``(voi, risk, disagreement, −cost)``."""
        return (self.voi, self.risk, self.disagreement, -self.cost)


# ---------------------------------------------------------------------------
# compute_score
# ---------------------------------------------------------------------------
def compute_score(
    node_id: str,
    graph: ProofGraph,
    roc: VerifierROC,
    *,
    cluster: ClusterIndex | None = None,
    bridge_bonus: float = 0.0,
    disagreement: float = 0.0,
    n_voi_samples: int | None = None,
    rng: random.Random | None = None,
    sem_relevance: dict[tuple[str, str], float] | None = None,
) -> Score:
    """Build a ``Score`` for a single node."""
    node = graph.get(node_id)
    voi = voi_node(node_id, graph, roc, n_samples=n_voi_samples, rng=rng)
    blast = sem_blast(node_id, graph, sem_relevance=sem_relevance)
    # H2 fix: risk explicitly weights blast radius by failure-prob proxy.
    risk = (1.0 - node.posterior_p) * blast * max(1.0, node.speculative_load)
    susp = cluster_suspicion(node_id, graph, cluster) if cluster is not None else 0.0
    return Score(
        node_id=node_id,
        voi=voi,
        risk=risk,
        cluster_susp=susp,
        bridge_bonus=bridge_bonus,
        disagreement=disagreement,
        cost=max(node.cost, DEFAULTS.prob_eps),
    )


# ---------------------------------------------------------------------------
# Pareto front (maximisation over to_pareto_tuple).
# ---------------------------------------------------------------------------
def pareto_front(scores: Sequence[Score]) -> list[Score]:
    """Return the non-dominated subset of ``scores``."""
    front: list[Score] = []
    keys = [s.to_pareto_tuple() for s in scores]
    for i, s in enumerate(scores):
        ki = keys[i]
        dominated = False
        for j, _ in enumerate(scores):
            if i == j:
                continue
            kj = keys[j]
            if all(kj[d] >= ki[d] for d in range(len(ki))) and any(
                kj[d] > ki[d] for d in range(len(ki))
            ):
                dominated = True
                break
        if not dominated:
            front.append(s)
    return front


# ---------------------------------------------------------------------------
# Annealed priority + Thompson sampling.
# ---------------------------------------------------------------------------
def anneal_lambda(verified_fraction: float) -> float:
    """``λ_t = max(0.1, 1 − verified_fraction)`` — DESIGN §9.3 + I4."""
    f = max(0.0, min(1.0, verified_fraction))
    return max(0.1, 1.0 - f)


def anneal_temperature(verified_fraction: float) -> float:
    """Linearly cool from ``thompson_temperature_start`` to ``..._end``."""
    f = max(0.0, min(1.0, verified_fraction))
    t0 = DEFAULTS.thompson_temperature_start
    t1 = DEFAULTS.thompson_temperature_end
    return t0 + (t1 - t0) * f


def priority(
    score: Score,
    *,
    lambda_t: float,
    mu_t: float = 0.5,
    nu_t: float = 0.5,
    xi_t: float = 0.5,
) -> float:
    """``priority = (1/cost) · [voi + λ·risk + μ·cluster + ν·bridge + ξ·disagree]``.

    H7 fix: cost is in the denominator, not subtracted.
    """

    info = (
        score.voi
        + lambda_t * score.risk
        + mu_t * score.cluster_susp
        + nu_t * score.bridge_bonus
        + xi_t * score.disagreement
    )
    return info / max(score.cost, DEFAULTS.prob_eps)


def thompson_sample(
    scores: Sequence[Score],
    *,
    verified_fraction: float,
    rng: random.Random,
    k: int = 1,
    weights_overrides: dict[str, float] | None = None,
) -> list[Score]:
    """Softmax-temperature sample ``k`` distinct scores from the Pareto front."""
    if not scores:
        return []
    front = pareto_front(scores)
    if not front:
        return []
    lambda_t = anneal_lambda(verified_fraction)
    temp = max(DEFAULTS.prob_eps, anneal_temperature(verified_fraction))
    raw = [priority(s, lambda_t=lambda_t) for s in front]
    if weights_overrides:
        raw = [r * weights_overrides.get(s.node_id, 1.0) for r, s in zip(raw, front)]
    m = max(raw)
    exps = [math.exp((r - m) / temp) for r in raw]
    total = sum(exps)
    if total <= 0.0:
        return list(front[:k])
    probs = [e / total for e in exps]
    picked: list[Score] = []
    remaining = list(zip(front, probs))
    for _ in range(min(k, len(remaining))):
        s_total = sum(p for _, p in remaining)
        if s_total <= 0.0:
            break
        r = rng.random() * s_total
        acc = 0.0
        for i, (s, p) in enumerate(remaining):
            acc += p
            if r <= acc:
                picked.append(s)
                remaining.pop(i)
                break
    return picked


__all__ = [
    "Score",
    "anneal_lambda",
    "anneal_temperature",
    "compute_score",
    "pareto_front",
    "priority",
    "thompson_sample",
]
