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

Plus deterministic-state-machine properties for ``policy.py``:

- ``classify`` is order-independent over the evidence ledger
- ``pass_count_from_evidence`` equals the cardinality of the
  ``{worker_id : DEFAULT-OK}`` set
- ``next_action`` never raises for any valid (state, evidence, budget)
- terminal states always yield ``Action.NONE``
- ``classify`` always returns a valid ``NodeState``
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
from rethlas_scoring.policy import (
    Action,
    Evidence,
    EvidenceKind,
    NodeState,
    PolicyBudget,
    VerdictKind,
    classify,
    next_action,
    pass_count_from_evidence,
)
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


_evidence_kind_strat = st.sampled_from(list(EvidenceKind))
_verdict_kind_strat = st.sampled_from(list(VerdictKind))


@st.composite
def _evidence_strategy(draw):
    kind = draw(_evidence_kind_strat)
    verdict = draw(_verdict_kind_strat)
    counterex = (
        draw(st.one_of(st.none(), st.text(min_size=1, max_size=10)))
        if kind is EvidenceKind.REFUTE
        else None
    )
    return Evidence(
        kind=kind,
        worker_id=draw(st.sampled_from([f"w{i}" for i in range(5)])),
        verdict=verdict,
        ts_iso="2026-05-06T00:00:00Z",
        counterexample=counterex,
    )


_evidence_list_strat = st.lists(_evidence_strategy(), min_size=0, max_size=8)


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
# policy — classify is order-independent
# ---------------------------------------------------------------------------
@_PROFILE
@given(evidence=_evidence_list_strat, seed=st.integers(0, 2**31 - 1))
def test_classify_order_independent_property(
    evidence: list[Evidence], seed: int
) -> None:
    rng = _random.Random(seed)
    shuffled = evidence[:]
    rng.shuffle(shuffled)
    s1 = classify(evidence)
    s2 = classify(shuffled)
    assert s1 is s2


# ---------------------------------------------------------------------------
# policy — pass_count == |{worker_id : DEFAULT && OK}|
# ---------------------------------------------------------------------------
@_PROFILE
@given(evidence=_evidence_list_strat)
def test_pass_count_matches_dedup_set_property(evidence: list[Evidence]) -> None:
    expected = len(
        {
            e.worker_id
            for e in evidence
            if e.kind is EvidenceKind.DEFAULT and e.verdict is VerdictKind.OK
        }
    )
    assert pass_count_from_evidence(evidence) == expected


# ---------------------------------------------------------------------------
# policy — next_action never raises and returns a valid Action
# ---------------------------------------------------------------------------
@_PROFILE
@given(
    evidence=_evidence_list_strat,
    state=st.sampled_from(list(NodeState)),
    max_refute=st.integers(min_value=0, max_value=5),
    max_strong=st.integers(min_value=0, max_value=5),
)
def test_next_action_total_function_property(
    evidence: list[Evidence],
    state: NodeState,
    max_refute: int,
    max_strong: int,
) -> None:
    budget = PolicyBudget(max_refute=max_refute, max_strong=max_strong)
    action = next_action(state, evidence, budget)
    assert isinstance(action, Action)


# ---------------------------------------------------------------------------
# policy — terminal states always yield NONE
# ---------------------------------------------------------------------------
@_PROFILE
@given(
    evidence=_evidence_list_strat,
    state=st.sampled_from(
        [NodeState.VERIFIED, NodeState.REFUTED, NodeState.USER_BLOCKED]
    ),
)
def test_next_action_terminal_property(
    evidence: list[Evidence], state: NodeState
) -> None:
    assert next_action(state, evidence) is Action.NONE


# ---------------------------------------------------------------------------
# Sanity — classify returns one of the documented states
# ---------------------------------------------------------------------------
@_PROFILE
@given(evidence=_evidence_list_strat)
def test_classify_returns_documented_state_property(
    evidence: list[Evidence],
) -> None:
    s = classify(evidence)
    assert isinstance(s, NodeState)
