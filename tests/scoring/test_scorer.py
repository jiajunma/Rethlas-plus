"""scorer.py — Score, Pareto front, Thompson, anneal_lambda (I4)."""

from __future__ import annotations

import random

from rethlas_scoring.scorer import (
    Score,
    anneal_lambda,
    anneal_temperature,
    compute_score,
    pareto_front,
    priority,
    thompson_sample,
)


def _s(node_id: str, voi=0.0, risk=0.0, cluster=0.0, bridge=0.0, dis=0.0, cost=1.0) -> Score:
    return Score(
        node_id=node_id,
        voi=voi,
        risk=risk,
        cluster_susp=cluster,
        bridge_bonus=bridge,
        disagreement=dis,
        cost=cost,
    )


# I4 — λ_t monotone non-increasing in verified_fraction.
def test_anneal_lambda_monotone() -> None:
    fractions = [i / 20 for i in range(21)]
    lams = [anneal_lambda(f) for f in fractions]
    assert all(b <= a + 1e-12 for a, b in zip(lams, lams[1:])), lams
    assert all(0.1 <= x <= 1.0 for x in lams)


def test_anneal_temperature_monotone() -> None:
    fractions = [i / 20 for i in range(21)]
    temps = [anneal_temperature(f) for f in fractions]
    assert all(b <= a + 1e-12 for a, b in zip(temps, temps[1:])), temps


def test_pareto_front_drops_dominated() -> None:
    a = _s("a", voi=1.0, risk=1.0, dis=1.0, cost=1.0)  # dominates b
    b = _s("b", voi=0.5, risk=0.5, dis=0.5, cost=2.0)
    c = _s("c", voi=1.0, risk=0.0, dis=0.0, cost=0.5)
    front = pareto_front([a, b, c])
    ids = sorted(s.node_id for s in front)
    assert ids == ["a", "c"]


def test_priority_uses_cost_in_denominator() -> None:
    cheap = _s("c1", voi=1.0, cost=1.0)
    pricey = _s("c2", voi=1.0, cost=10.0)
    assert priority(cheap, lambda_t=1.0) > priority(pricey, lambda_t=1.0)
    big = _s("c3", voi=2.0, cost=1.0)
    assert abs(priority(big, lambda_t=1.0) - 2 * priority(cheap, lambda_t=1.0)) < 1e-12


def test_thompson_sample_picks_distinct() -> None:
    rng = random.Random(0)
    scores = [_s(f"n{i}", voi=float(i)) for i in range(5)]
    picked = thompson_sample(scores, verified_fraction=0.0, rng=rng, k=3)
    assert len({s.node_id for s in picked}) == len(picked)
    assert len(picked) <= 3


def test_thompson_sample_empty_returns_empty() -> None:
    assert thompson_sample([], verified_fraction=0.0, rng=random.Random(0), k=2) == []


def test_compute_score_reflects_cost_floor(singleton_graph, perfect_roc, rng) -> None:
    s = compute_score("a", singleton_graph, perfect_roc, n_voi_samples=100, rng=rng)
    assert s.node_id == "a"
    assert s.cost > 0.0
