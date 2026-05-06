"""verifier_ensemble.aggregate — property-based invariants (S7-math).

Strengthens the example-based ``test_ensemble.py`` cases with
Hypothesis-driven coverage of the Dawid-Skene aggregator's
mathematical properties:

- **Determinism**: same input → same output
- **Confidence range**: returned confidence ∈ [0.5, 1.0]
- **Empty observations** collapse to the prior label
- **Symmetry**: flipping every vote produces the symmetric outcome
- **Single perfect worker dominates**
- **Disagreement is bounded in [0, 1]**
"""

from __future__ import annotations

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from rethlas_scoring.calibration import BetaPosterior
from rethlas_scoring.verifier_ensemble import (
    WorkerConfusion,
    aggregate,
    disagreement,
)


_PROFILE = settings(max_examples=80, deadline=None)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
def _confusion_strat():
    """Generate a WorkerConfusion with mildly-informative ROC values."""
    return st.builds(
        lambda wid, tpr_a, tpr_b, fpr_a, fpr_b: WorkerConfusion(
            worker_id=wid,
            tpr=BetaPosterior(alpha=tpr_a, beta=tpr_b),
            fpr=BetaPosterior(alpha=fpr_a, beta=fpr_b),
        ),
        wid=st.sampled_from([f"w{i}" for i in range(5)]),
        tpr_a=st.floats(min_value=1.0, max_value=20.0, allow_nan=False),
        tpr_b=st.floats(min_value=1.0, max_value=20.0, allow_nan=False),
        fpr_a=st.floats(min_value=1.0, max_value=20.0, allow_nan=False),
        fpr_b=st.floats(min_value=1.0, max_value=20.0, allow_nan=False),
    )


@st.composite
def _results_and_confusions(draw, *, max_results: int = 6):
    n = draw(st.integers(min_value=0, max_value=max_results))
    confusions: dict[str, WorkerConfusion] = {}
    results = []
    for i in range(n):
        c = draw(_confusion_strat())
        # Force unique worker IDs in a single ensemble call.
        wid = f"w{i}"
        confusions[wid] = WorkerConfusion(
            worker_id=wid, tpr=c.tpr, fpr=c.fpr
        )
        predicted = draw(st.booleans())
        conf = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
        results.append((wid, predicted, conf))
    prior = draw(st.floats(min_value=0.001, max_value=0.999, allow_nan=False))
    return results, confusions, prior


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------
@_PROFILE
@given(_results_and_confusions())
def test_aggregate_is_deterministic(case) -> None:
    results, confusions, prior = case
    a = aggregate(results, confusions, prior_p_true=prior)
    b = aggregate(results, confusions, prior_p_true=prior)
    assert a == b


@_PROFILE
@given(_results_and_confusions())
def test_aggregate_confidence_is_at_least_one_half(case) -> None:
    """`label` is the **majority** side, so its posterior must be ≥ 0.5."""
    results, confusions, prior = case
    _label, conf = aggregate(results, confusions, prior_p_true=prior)
    assert 0.5 - 1e-9 <= conf <= 1.0 + 1e-9


@_PROFILE
@given(prior=st.floats(min_value=0.001, max_value=0.999, allow_nan=False))
def test_aggregate_empty_collapses_to_prior(prior: float) -> None:
    label, conf = aggregate([], confusions={}, prior_p_true=prior)
    if prior >= 0.5:
        assert label is True
        assert abs(conf - prior) < 1e-9
    else:
        assert label is False
        assert abs(conf - (1.0 - prior)) < 1e-9


@_PROFILE
@given(_results_and_confusions())
def test_aggregate_vote_symmetry(case) -> None:
    """Truth-relabelling symmetry: if we re-label ``T`` ↔ ``1−T``
    everywhere (swap each worker's TPR↔FPR, flip the prior), the
    posterior over the "new" T=1 must equal the original posterior
    over T=0 — so the output label flips.

    Worker votes are **not** flipped: a worker's reported observation
    doesn't depend on our labelling convention.

    Skip the degenerate boundary where ``confidence ≈ 0.5`` — the
    ``>=`` tie-break artificially returns True on both sides there.
    """
    results, confusions, prior = case
    label_orig, conf_orig = aggregate(results, confusions, prior_p_true=prior)
    assume(conf_orig > 0.5 + 1e-9)

    # Same votes; per-worker TPR↔FPR swap; prior 1-π.
    swapped_confusions = {
        wid: WorkerConfusion(worker_id=wid, tpr=c.fpr, fpr=c.tpr)
        for wid, c in confusions.items()
    }
    label_flip, _ = aggregate(
        results, swapped_confusions, prior_p_true=1.0 - prior
    )
    assert label_orig != label_flip


@_PROFILE
@given(
    n_other_workers=st.integers(min_value=0, max_value=4),
    perfect_says_ok=st.booleans(),
)
def test_aggregate_single_near_perfect_worker_dominates(
    n_other_workers: int, perfect_says_ok: bool
) -> None:
    """A worker with very informative ROC (TPR≈1, FPR≈0) should override
    a handful of uninformative workers (TPR≈FPR≈0.5)."""
    big = 1e6
    confusions: dict[str, WorkerConfusion] = {
        "perfect": WorkerConfusion(
            worker_id="perfect",
            tpr=BetaPosterior(alpha=big, beta=1.0),
            fpr=BetaPosterior(alpha=1.0, beta=big),
        )
    }
    results = [("perfect", perfect_says_ok, 1.0)]
    for i in range(n_other_workers):
        wid = f"noise{i}"
        confusions[wid] = WorkerConfusion(
            worker_id=wid,
            tpr=BetaPosterior(alpha=1.0, beta=1.0),
            fpr=BetaPosterior(alpha=1.0, beta=1.0),
        )
        results.append((wid, not perfect_says_ok, 0.5))

    label, conf = aggregate(results, confusions, prior_p_true=0.5)
    assert label is perfect_says_ok
    assert conf > 0.99


@_PROFILE
@given(disagreement_size=st.integers(min_value=0, max_value=8))
def test_disagreement_is_in_unit_interval(disagreement_size: int) -> None:
    """Whatever the votes, ``disagreement`` is bounded in [0, 1]."""
    results = [(f"w{i}", i % 2 == 0, 0.9) for i in range(disagreement_size)]
    d = disagreement(results)
    assert 0.0 <= d <= 1.0


@_PROFILE
@given(n_workers=st.integers(min_value=1, max_value=8))
def test_disagreement_zero_for_unanimous(n_workers: int) -> None:
    results = [(f"w{i}", True, 0.9) for i in range(n_workers)]
    assert disagreement(results) == 0.0
