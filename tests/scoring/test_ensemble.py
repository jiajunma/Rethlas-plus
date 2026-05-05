"""verifier_ensemble.py — Dawid-Skene aggregation + disagreement."""

from __future__ import annotations

from rethlas_scoring.verifier_ensemble import (
    EnsembleVerifier,
    StubVerifier,
    WorkerConfusion,
    aggregate,
    disagreement,
)


def test_ensemble_unanimous_passes_through() -> None:
    workers = [StubVerifier(worker_id=f"w{i}", answer=True) for i in range(3)]
    ens = EnsembleVerifier(workers=workers)
    results = ens.vote("hello")
    label, conf = aggregate(results, ens.confusions)
    assert label is True
    assert 0.0 <= conf <= 1.0


def test_ensemble_split_two_one_majority_wins_after_truth_updates() -> None:
    workers = [
        StubVerifier(worker_id="good1", answer=True),
        StubVerifier(worker_id="good2", answer=True),
        StubVerifier(worker_id="bad", answer=False),
    ]
    ens = EnsembleVerifier(workers=workers)
    for _ in range(20):
        ens.update_with_truth(
            results=[("good1", True, 0.9), ("good2", True, 0.9), ("bad", False, 0.9)],
            ground_truth_ok=True,
        )
    results = ens.vote("hello")
    label, conf = aggregate(results, ens.confusions)
    assert label is True
    assert conf > 0.5


def test_disagreement_is_one_at_split_zero_at_unanimous() -> None:
    unanimous = [("a", True, 0.9), ("b", True, 0.9), ("c", True, 0.9)]
    split = [("a", True, 0.9), ("b", False, 0.9)]
    assert disagreement(unanimous) == 0.0
    assert disagreement(split) == 1.0


def test_aggregate_reduces_to_prior_when_no_evidence() -> None:
    label, conf = aggregate([], confusions={}, prior_p_true=0.7)
    assert label is True
    assert abs(conf - 0.7) < 1e-9


def test_worker_confusion_tracks_per_truth_branch() -> None:
    wc = WorkerConfusion(worker_id="w")
    wc = wc.update(predicted_ok=True, ground_truth_ok=True)
    wc = wc.update(predicted_ok=True, ground_truth_ok=False)
    assert wc.tpr.alpha == 2.0
    assert wc.fpr.alpha == 2.0
    assert wc.tpr.beta == 1.0
    assert wc.fpr.beta == 1.0
