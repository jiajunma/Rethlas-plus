"""M8 — pool-based dispatch ordering (§10.2)."""

from __future__ import annotations

from coordinator.dispatcher import (
    GeneratorCandidate,
    VerifierCandidate,
    select_generator_targets,
    select_verifier_targets,
)


def test_generator_pool_label_ascending() -> None:
    """§10.2.2: strict label asc, no repair_count deprio."""
    pool = [
        GeneratorCandidate("lem:c"),
        GeneratorCandidate("lem:a"),
        GeneratorCandidate("lem:b"),
    ]
    out = select_generator_targets(pool, capacity=3, in_flight_targets=())
    assert out == ["lem:a", "lem:b", "lem:c"]


def test_generator_pool_skips_in_flight() -> None:
    pool = [GeneratorCandidate("lem:a"), GeneratorCandidate("lem:b")]
    out = select_generator_targets(pool, capacity=2, in_flight_targets=("lem:a",))
    assert out == ["lem:b"]


def test_generator_pool_capacity_cap() -> None:
    pool = [GeneratorCandidate("lem:a"), GeneratorCandidate("lem:b"), GeneratorCandidate("lem:c")]
    out = select_generator_targets(pool, capacity=1, in_flight_targets=())
    assert out == ["lem:a"]


def test_verifier_pool_pass_count_then_label() -> None:
    """§10.2.1: pass_count asc, label asc tiebreak."""
    pool = [
        VerifierCandidate(label="thm:x", pass_count=2),
        VerifierCandidate(label="def:y", pass_count=0),
        VerifierCandidate(label="lem:z", pass_count=1),
    ]
    out = select_verifier_targets(pool, capacity=3, in_flight_targets=())
    assert out == ["def:y", "lem:z", "thm:x"]


def test_verifier_pool_label_tiebreak_within_count() -> None:
    pool = [
        VerifierCandidate(label="lem:b", pass_count=0),
        VerifierCandidate(label="lem:a", pass_count=0),
    ]
    out = select_verifier_targets(pool, capacity=2, in_flight_targets=())
    assert out == ["lem:a", "lem:b"]


def test_verifier_pool_skips_in_flight() -> None:
    pool = [
        VerifierCandidate(label="lem:a", pass_count=0),
        VerifierCandidate(label="lem:b", pass_count=0),
    ]
    out = select_verifier_targets(pool, capacity=2, in_flight_targets=("lem:a",))
    assert out == ["lem:b"]


def test_zero_capacity_returns_empty() -> None:
    pool = [GeneratorCandidate("lem:a")]
    assert select_generator_targets(pool, capacity=0, in_flight_targets=()) == []
    pool2 = [VerifierCandidate("lem:a", 0)]
    assert select_verifier_targets(pool2, capacity=0, in_flight_targets=()) == []
