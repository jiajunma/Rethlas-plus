"""Value-of-Information (VOI) — DESIGN §4.

Direct computation of ``H(Z) − E[H(Z|r)]`` is #P-hard on a DAG (equivalent
to SAT model counting), so we Monte-Carlo it with N=200 truth assignments
sampled from the per-node posteriors. See DESIGN §4.2 for the full
algorithm.

Pure stdlib — uses ``random.Random`` so callers can seed deterministically
for tests.

Invariants enforced (DESIGN §4.3, tested in ``tests/scoring/test_voi.py``):

- ``voi_node ≥ 0`` always (clamped at 0 to absorb MC noise).
- ``voi_node → 0`` as ``posterior_p → 0`` or ``→ 1``.
- ``voi_node = 0`` when ``p_tpr == p_fpr`` (verifier is useless).
- With perfect verifier and ``posterior_p = 0.5``, ``voi_node`` recovers
  the binary entropy of the marginal Z prediction.
"""

from __future__ import annotations

import math
import random
from typing import Iterable, Sequence

from .calibration import VerifierROC
from .data import DEFAULTS, ProofGraph


# ---------------------------------------------------------------------------
# Binary entropy.
# ---------------------------------------------------------------------------
def binary_entropy(p: float) -> float:
    """h(p) = −p log p − (1−p) log(1−p), with the standard 0-extension."""
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -(p * math.log(p) + (1.0 - p) * math.log(1.0 - p))


# ---------------------------------------------------------------------------
# Truth-assignment sampler.
# ---------------------------------------------------------------------------
def _sample_truth_assignments(
    graph: ProofGraph,
    relevant_ids: Iterable[str],
    *,
    n_samples: int,
    rng: random.Random,
) -> list[dict[str, bool]]:
    """Draw N i.i.d. assignments from per-node posteriors restricted to
    ``relevant_ids``."""
    rel = list(relevant_ids)
    posteriors = {nid: graph.get(nid).posterior_p for nid in rel}
    out: list[dict[str, bool]] = []
    for _ in range(n_samples):
        sample: dict[str, bool] = {}
        for nid in rel:
            sample[nid] = rng.random() < posteriors[nid]
        out.append(sample)
    return out


def _z_under(sample: dict[str, bool]) -> bool:
    """Z := AND of all truth values in the relevance cone."""
    return all(sample.values())


# ---------------------------------------------------------------------------
# VOI of verifying a single node.
# ---------------------------------------------------------------------------
def voi_node(
    node_id: str,
    graph: ProofGraph,
    roc: VerifierROC,
    *,
    n_samples: int | None = None,
    rng: random.Random | None = None,
) -> float:
    """Estimate VOI(v) = H(Z) − E_r[H(Z | Verify(v) = r)] via Monte Carlo.

    The relevance cone (DESIGN §4.2) is the union of ``v`` plus its
    ancestors and descendants. Z is conjunction over that cone.
    """

    n = n_samples or DEFAULTS.voi_mc_samples
    rng = rng or random.Random(0xC0FFEE ^ hash(node_id))
    cone = graph.relevance_cone(node_id)
    if not cone:
        return 0.0

    samples = _sample_truth_assignments(graph, cone, n_samples=n, rng=rng)
    if not samples:
        return 0.0

    z_vals = [_z_under(s) for s in samples]
    p_z = sum(1 for z in z_vals if z) / len(z_vals)
    h_before = binary_entropy(p_z)

    node = graph.get(node_id)
    bucket = roc.bucket(node.claim_text, node.hardness)

    # Per-sample weights for each possible verifier outcome r.
    h_after = 0.0
    for observed_ok in (True, False):
        weights: list[float] = []
        for s in samples:
            t_v = s.get(node_id, False)
            weights.append(roc.likelihood(bucket, observed_ok=observed_ok, truth=t_v))
        w_total = sum(weights)
        if w_total <= 0.0:
            continue
        p_r = w_total / len(samples)
        num = sum(w for w, z in zip(weights, z_vals) if z)
        p_z_given_r = num / w_total if w_total > 0 else 0.0
        h_after += p_r * binary_entropy(p_z_given_r)

    voi = h_before - h_after
    # Clamp at 0 — MC variance can briefly push H_after slightly above
    # H_before; the population-level inequality H(Z) ≥ E[H(Z|R)] holds
    # (information never hurts).
    return max(0.0, voi)


# ---------------------------------------------------------------------------
# Semantic blast radius — DESIGN §4.4.
# ---------------------------------------------------------------------------
def sem_blast(
    node_id: str,
    graph: ProofGraph,
    *,
    sem_relevance: dict[tuple[str, str], float] | None = None,
) -> float:
    """Sum over downstream nodes of (relevance × speculative_load)."""
    sem_relevance = sem_relevance or {}
    total = 0.0
    for u in graph.descendants(node_id):
        rel = sem_relevance.get((node_id, u), 1.0)
        total += rel * abs(graph.get(u).speculative_load)
    return total


# ---------------------------------------------------------------------------
# Joint Z probability (used by ``scheduler.certifying_set``).
# ---------------------------------------------------------------------------
def estimate_p_z(
    graph: ProofGraph,
    relevant_ids: Sequence[str] | None = None,
    *,
    n_samples: int | None = None,
    rng: random.Random | None = None,
) -> float:
    """Estimate P(Z = 1) over the supplied relevance set (or all nodes)."""
    n = n_samples or DEFAULTS.voi_mc_samples
    rng = rng or random.Random(0xBADC0DE)
    rel = list(relevant_ids) if relevant_ids is not None else list(graph.nodes.keys())
    if not rel:
        return 1.0
    samples = _sample_truth_assignments(graph, rel, n_samples=n, rng=rng)
    if not samples:
        return 1.0
    return sum(1 for s in samples if _z_under(s)) / len(samples)


__all__ = [
    "binary_entropy",
    "estimate_p_z",
    "sem_blast",
    "voi_node",
]
