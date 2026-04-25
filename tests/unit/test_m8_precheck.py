"""M8 — pre-dispatch precheck gate (§5.5.2)."""

from __future__ import annotations

from coordinator.precheck import (
    CandidateInput,
    precheck_generator,
    precheck_verifier,
)


def _gen_cand(**overrides) -> CandidateInput:
    base = dict(
        target="thm:goal",
        target_kind="theorem",
        statement="S",
        proof="",
        statement_hash="ab" * 32,
        verification_hash="cd" * 32,
        pass_count=-1,
        repair_count=0,
        repair_hint="",
        verification_report="",
        dep_statement_hashes={"def:x": "ef" * 32},
        dep_pass_counts={"def:x": 1},
        last_rejected_verification_hash="",
    )
    base.update(overrides)
    return CandidateInput(**base)


def test_generator_happy_path_fresh() -> None:
    cand = _gen_cand()
    ctx, fail = precheck_generator(cand, in_flight_targets=())
    assert fail is None
    assert ctx is not None
    assert ctx.h_rejected == ""
    assert ctx.dep_statement_hashes == {"def:x": "ef" * 32}


def test_generator_rejects_pool_mismatch() -> None:
    cand = _gen_cand(pass_count=0)  # not -1
    ctx, fail = precheck_generator(cand, in_flight_targets=())
    assert ctx is None
    assert fail is not None
    assert fail.reason == "pool_mismatch"


def test_generator_rejects_when_target_in_flight() -> None:
    cand = _gen_cand()
    ctx, fail = precheck_generator(cand, in_flight_targets=("thm:goal",))
    assert ctx is None
    assert fail.reason == "in_flight"


def test_generator_rejects_when_deps_not_ready() -> None:
    cand = _gen_cand(dep_statement_hashes={"def:x": ""})
    ctx, fail = precheck_generator(cand, in_flight_targets=())
    assert ctx is None
    assert fail.reason == "deps_not_ready"


def test_generator_rejects_on_hash_drift() -> None:
    cand = _gen_cand()
    ctx, fail = precheck_generator(
        cand,
        in_flight_targets=(),
        expected_hash_for_drift_check="ff" * 32,  # different
    )
    assert ctx is None
    assert fail.reason == "hash_drift"


def test_generator_repair_requires_h_rejected() -> None:
    cand = _gen_cand(repair_count=2)  # repair mode
    ctx, fail = precheck_generator(cand, in_flight_targets=())
    assert ctx is None
    assert fail.reason == "missing_h_rejected"


def test_generator_repair_h_rejected_must_match_current() -> None:
    cand = _gen_cand(
        repair_count=2,
        last_rejected_verification_hash="00" * 32,  # mismatch
    )
    ctx, fail = precheck_generator(cand, in_flight_targets=())
    assert ctx is None
    assert fail.reason == "h_rejected_stale"


def test_generator_repair_happy_path() -> None:
    vh = "cd" * 32
    cand = _gen_cand(
        repair_count=2,
        verification_hash=vh,
        last_rejected_verification_hash=vh,
    )
    ctx, fail = precheck_generator(cand, in_flight_targets=())
    assert fail is None
    assert ctx.h_rejected == vh


def test_verifier_happy_path() -> None:
    cand = _gen_cand(pass_count=0, dep_pass_counts={"def:x": 1})  # verifier band
    ctx, fail = precheck_verifier(cand, in_flight_targets=())
    assert fail is None
    assert ctx.h_rejected == ""


def test_verifier_rejects_negative_pass_count() -> None:
    cand = _gen_cand(pass_count=-1)
    ctx, fail = precheck_verifier(cand, in_flight_targets=())
    assert fail is not None
    assert fail.reason == "pool_mismatch"


def test_verifier_rejects_when_target_in_flight() -> None:
    cand = _gen_cand(pass_count=0, dep_pass_counts={"def:x": 1})
    ctx, fail = precheck_verifier(cand, in_flight_targets=("thm:goal",))
    assert fail.reason == "in_flight"


def test_verifier_rejects_when_dep_not_strictly_ahead() -> None:
    cand = _gen_cand(pass_count=1, dep_pass_counts={"def:x": 1})
    ctx, fail = precheck_verifier(cand, in_flight_targets=())
    assert ctx is None
    assert fail is not None
    assert fail.reason == "deps_not_strictly_ahead"
