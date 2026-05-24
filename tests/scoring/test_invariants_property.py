"""Property-based invariant checks (Hypothesis).

Strengthens the example-based invariant tests in ``test_invariants.py``
by exercising the same properties across hundreds of randomly
generated graphs / posteriors / evidence ledgers. Picks up edge cases
that hand-written cases miss (degenerate graphs, extreme probability
boundaries, large evidence lists, etc).

Covers four of the six invariants from ``docs/SCORING_DESIGN.md §12``:

- I1: ``voi_node ≥ 0`` always
- I2: ``posterior_p ∈ [0, 1]`` after every op
- I3: cluster propagation only decreases posteriors
- I4: ``anneal_lambda`` monotone non-increasing in verified_fraction

Plus S6-E/F coordinator heuristic invariants (posterior clamping,
monotonicity, tier-strict dispatcher).
"""

from __future__ import annotations

import math
import random as _random
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from rethlas_scoring.calibration import (
    BetaPosterior,
    VerifierROC,
    perfect_verifier_roc,
)
from rethlas_scoring.cluster import ClusterIndex, propagate_failure
from rethlas_scoring.data import ProofGraph, ScoredNode
from rethlas_scoring.scorer import anneal_lambda
from rethlas_scoring.voi import voi_node


# Keep MC sample counts modest so property tests stay snappy (~ms each).
_MC_SAMPLES = 64

# Cap example counts; default 80 examples × MC sampling is enough for
# regression and keeps the suite under a couple of seconds.
_PROFILE = settings(max_examples=80, deadline=None)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
_label_strat = st.text(
    alphabet=st.sampled_from(string.ascii_lowercase + "_"),
    min_size=1,
    max_size=6,
).filter(lambda s: s.isidentifier())

_unit_float = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


@st.composite
def _proof_graph_strategy(draw, *, max_nodes: int = 6, embed_dim: int = 0):
    n = draw(st.integers(min_value=1, max_value=max_nodes))
    ids = [f"n{i}" for i in range(n)]
    nodes: dict[str, ScoredNode] = {}
    for i, nid in enumerate(ids):
        # Restrict deps to *prior* ids to guarantee acyclicity.
        deps_pool = ids[:i]
        deps = (
            draw(
                st.lists(
                    st.sampled_from(deps_pool),
                    max_size=min(2, i),
                    unique=True,
                )
            )
            if deps_pool
            else []
        )
        emb = (
            tuple(
                draw(
                    st.lists(
                        st.floats(-1.0, 1.0, allow_nan=False),
                        min_size=embed_dim,
                        max_size=embed_dim,
                    )
                )
            )
            if embed_dim
            else ()
        )
        nodes[nid] = ScoredNode(
            id=nid,
            claim_text=f"claim_{nid}",
            prior_p=draw(_unit_float),
            posterior_p=draw(_unit_float),
            depends_on=tuple(sorted(deps)),
            embedding=emb,
        )
    return ProofGraph.build(nodes)


# ---------------------------------------------------------------------------
# I1 — VOI ≥ 0 over random graphs
# ---------------------------------------------------------------------------
@_PROFILE
@given(graph=_proof_graph_strategy(max_nodes=4), seed=st.integers(0, 2**31 - 1))
def test_voi_nonneg_property(graph: ProofGraph, seed: int) -> None:
    roc = perfect_verifier_roc()
    rng = _random.Random(seed)
    for nid in graph.nodes:
        v = voi_node(nid, graph, roc, n_samples=_MC_SAMPLES, rng=rng)
        assert v >= 0.0
        assert math.isfinite(v)


@_PROFILE
@given(
    graph=_proof_graph_strategy(max_nodes=3),
    seed=st.integers(0, 2**31 - 1),
)
def test_voi_zero_for_useless_verifier_property(graph: ProofGraph, seed: int) -> None:
    """When p_tpr == p_fpr the verifier carries no information; VOI must
    collapse to (numerical) zero for every node, regardless of priors."""
    useless = VerifierROC()
    for b in range(useless.n_buckets):
        useless.tpr_priors[b] = BetaPosterior(alpha=1.0, beta=1.0)
        useless.fpr_priors[b] = BetaPosterior(alpha=1.0, beta=1.0)
    rng = _random.Random(seed)
    for nid in graph.nodes:
        v = voi_node(nid, graph, useless, n_samples=_MC_SAMPLES, rng=rng)
        assert v < 1e-6


# ---------------------------------------------------------------------------
# I2 — posterior_p stays in [0, 1] after with_posterior
# ---------------------------------------------------------------------------
@_PROFILE
@given(
    initial=_unit_float,
    target=st.floats(min_value=-100.0, max_value=100.0, allow_nan=False),
)
def test_with_posterior_clamps_to_unit_interval(initial: float, target: float) -> None:
    n = ScoredNode(id="x", claim_text="x", posterior_p=initial)
    n2 = n.with_posterior(target)
    assert 0.0 <= n2.posterior_p <= 1.0


# ---------------------------------------------------------------------------
# I3 — cluster.propagate_failure is monotone non-increasing
# ---------------------------------------------------------------------------
@_PROFILE
@given(
    graph=_proof_graph_strategy(max_nodes=5, embed_dim=3),
    severity=_unit_float,
)
def test_propagate_failure_monotone_decreasing_property(
    graph: ProofGraph, severity: float
) -> None:
    if not graph.nodes:
        return
    cluster = ClusterIndex().build(graph)
    failed_id = next(iter(graph.nodes))
    before = {nid: n.posterior_p for nid, n in graph.nodes.items()}
    after = propagate_failure(graph, failed_id, cluster, severity=severity)
    for nid, n in after.nodes.items():
        # Floating-point: allow a tiny epsilon for clamp ops.
        assert n.posterior_p <= before[nid] + 1e-12, (nid, before[nid], n.posterior_p)
        assert 0.0 <= n.posterior_p <= 1.0  # I2 again


# ---------------------------------------------------------------------------
# I4 — anneal_lambda monotone non-increasing in verified_fraction
# ---------------------------------------------------------------------------
@_PROFILE
@given(
    fractions=st.lists(_unit_float, min_size=2, max_size=20).map(sorted),
)
def test_anneal_lambda_monotone_property(fractions: list[float]) -> None:
    lams = [anneal_lambda(f) for f in fractions]
    for prev, nxt in zip(lams, lams[1:]):
        assert nxt <= prev + 1e-12
    assert all(0.1 <= x <= 1.0 for x in lams)


# ---------------------------------------------------------------------------
# S6-E + S6-F — coordinator heuristic invariants.
# ---------------------------------------------------------------------------
from coordinator.main import _posterior_from_kb_signals
from coordinator.precheck import CandidateInput


def _candidate(pass_count: int, repair_count: int) -> CandidateInput:
    return CandidateInput(
        target="lem:x",
        target_kind="lemma",
        statement="claim x",
        proof="",
        statement_hash="0" * 64,
        verification_hash="0" * 64,
        pass_count=pass_count,
        repair_count=repair_count,
        repair_hint="",
        verification_report="",
        dep_statement_hashes={},
        dep_pass_counts={},
    )


@_PROFILE
@given(
    pass_count=st.integers(min_value=-5, max_value=200),
    repair_count=st.integers(min_value=0, max_value=200),
)
def test_posterior_heuristic_clamped_to_unit_interval(
    pass_count: int, repair_count: int
) -> None:
    """``_posterior_from_kb_signals`` must return a value in
    ``[0.10, 0.95]`` for **any** integer inputs (including negative
    pass_count and absurdly large repair counts)."""
    p = _posterior_from_kb_signals(_candidate(pass_count, repair_count))
    assert 0.10 - 1e-12 <= p <= 0.95 + 1e-12


@_PROFILE
@given(
    pc_a=st.integers(min_value=0, max_value=10),
    pc_b=st.integers(min_value=0, max_value=10),
)
def test_posterior_heuristic_monotone_in_pass_count(
    pc_a: int, pc_b: int
) -> None:
    """More passes → no lower posterior (monotone non-decreasing in
    ``pass_count`` when ``repair_count`` is fixed)."""
    p_a = _posterior_from_kb_signals(_candidate(pc_a, repair_count=0))
    p_b = _posterior_from_kb_signals(_candidate(pc_b, repair_count=0))
    if pc_a <= pc_b:
        assert p_a <= p_b + 1e-12


@_PROFILE
@given(
    rc_a=st.integers(min_value=0, max_value=10),
    rc_b=st.integers(min_value=0, max_value=10),
)
def test_posterior_heuristic_monotone_in_repair_count(
    rc_a: int, rc_b: int
) -> None:
    """More rejections → no higher posterior (monotone non-increasing
    in ``repair_count`` when ``pass_count`` is fixed)."""
    p_a = _posterior_from_kb_signals(_candidate(0, rc_a))
    p_b = _posterior_from_kb_signals(_candidate(0, rc_b))
    if rc_a <= rc_b:
        assert p_a >= p_b - 1e-12


# ---------------------------------------------------------------------------
# Dispatcher tier-strict property — never spill to higher tier while a
# lower-tier candidate is non-busy.
# ---------------------------------------------------------------------------
from coordinator.dispatcher import VerifierCandidate, select_verifier_targets


@_PROFILE
@given(
    pass_counts=st.lists(
        st.integers(min_value=0, max_value=3),
        min_size=1,
        max_size=10,
    ),
    capacity=st.integers(min_value=1, max_value=5),
)
def test_dispatcher_tier_strict_property(
    pass_counts: list[int], capacity: int
) -> None:
    """Every selected label must come from the lowest tier with
    available (non-busy) candidates. Equivalently: the maximum
    pass_count returned is no larger than the minimum pass_count among
    not-yet-returned candidates that aren't in_flight."""
    candidates = [
        VerifierCandidate(label=f"lem:l{i}", pass_count=pc)
        for i, pc in enumerate(pass_counts)
    ]
    out = select_verifier_targets(
        candidates, capacity=capacity, in_flight_targets=set()
    )
    if not out:
        return
    by_label = {c.label: c.pass_count for c in candidates}
    selected_pcs = [by_label[lbl] for lbl in out]
    not_selected = {lbl for lbl in by_label if lbl not in out}
    if not_selected:
        leftover_min_pc = min(by_label[lbl] for lbl in not_selected)
        # Anything we returned must be ≤ that leftover minimum (tier-strict).
        assert max(selected_pcs) <= leftover_min_pc
