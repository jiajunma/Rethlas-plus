"""policy.py — deterministic L5 state machine (DESIGN §7).

Covers every classify() rule, the escalation ladder in next_action(),
and the worker-dedup behaviour of pass_count_from_evidence().
"""

from __future__ import annotations

import pytest

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


# ---------------------------------------------------------------------------
# Tiny factory helpers — keep tests dense and readable.
# ---------------------------------------------------------------------------
def _ev(
    kind: EvidenceKind,
    *,
    worker: str = "w",
    verdict: VerdictKind = VerdictKind.OK,
    counterex: str | None = None,
    ts: str = "2026-05-06T00:00:00Z",
) -> Evidence:
    return Evidence(
        kind=kind,
        worker_id=worker,
        verdict=verdict,
        ts_iso=ts,
        counterexample=counterex,
    )


def _default_ok(worker: str) -> Evidence:
    return _ev(EvidenceKind.DEFAULT, worker=worker, verdict=VerdictKind.OK)


def _default_crit(worker: str) -> Evidence:
    return _ev(EvidenceKind.DEFAULT, worker=worker, verdict=VerdictKind.CRITICAL)


# ===========================================================================
# pass_count_from_evidence
# ===========================================================================
def test_pass_count_zero_for_empty_evidence() -> None:
    assert pass_count_from_evidence([]) == 0


def test_pass_count_dedupes_same_worker() -> None:
    evs = [_default_ok("w1"), _default_ok("w1"), _default_ok("w1")]
    assert pass_count_from_evidence(evs) == 1


def test_pass_count_counts_distinct_workers() -> None:
    evs = [_default_ok("w1"), _default_ok("w2"), _default_ok("w3")]
    assert pass_count_from_evidence(evs) == 3


def test_pass_count_excludes_non_default_kind() -> None:
    evs = [
        _default_ok("w1"),
        _ev(EvidenceKind.STRONG, worker="s1", verdict=VerdictKind.OK),
        _ev(EvidenceKind.REFUTE, worker="r1", verdict=VerdictKind.OK),
    ]
    assert pass_count_from_evidence(evs) == 1


def test_pass_count_excludes_non_ok_verdict() -> None:
    evs = [
        _default_ok("w1"),
        _default_crit("w2"),
        _ev(EvidenceKind.DEFAULT, worker="w3", verdict=VerdictKind.GAP),
        _ev(EvidenceKind.DEFAULT, worker="w4", verdict=VerdictKind.ABSTAIN),
    ]
    assert pass_count_from_evidence(evs) == 1


# ===========================================================================
# classify — rule 1: REFUTED beats everything
# ===========================================================================
def test_classify_refuted_when_counterexample_present() -> None:
    evs = [
        _default_ok("w1"),
        _default_ok("w2"),
        _default_ok("w3"),
        _ev(EvidenceKind.STRONG, worker="s1", verdict=VerdictKind.OK),
        _ev(
            EvidenceKind.REFUTE,
            worker="r1",
            verdict=VerdictKind.CRITICAL,
            counterex="x=0 violates the bound",
        ),
    ]
    assert classify(evs) is NodeState.REFUTED


def test_classify_refute_without_counterexample_does_not_refute() -> None:
    evs = [
        _ev(
            EvidenceKind.REFUTE,
            worker="r1",
            verdict=VerdictKind.OK,
            counterex=None,
        ),
    ]
    assert classify(evs) is NodeState.PENDING


# ===========================================================================
# classify — rule 2: STRONG OK → VERIFIED
# ===========================================================================
def test_classify_verified_by_single_strong_ok() -> None:
    evs = [_ev(EvidenceKind.STRONG, worker="s1", verdict=VerdictKind.OK)]
    assert classify(evs) is NodeState.VERIFIED


def test_classify_strong_ok_overrides_low_default_count() -> None:
    evs = [
        _default_ok("w1"),
        _ev(EvidenceKind.STRONG, worker="s1", verdict=VerdictKind.OK),
    ]
    assert classify(evs) is NodeState.VERIFIED


# ===========================================================================
# classify — rule 3: STRONG CRITICAL → USER_BLOCKED
# ===========================================================================
def test_classify_user_blocked_on_strong_critical() -> None:
    evs = [
        _ev(EvidenceKind.STRONG, worker="s1", verdict=VerdictKind.CRITICAL),
    ]
    assert classify(evs) is NodeState.USER_BLOCKED


def test_classify_strong_critical_overrides_default_ok() -> None:
    evs = [
        _default_ok("w1"),
        _default_ok("w2"),
        _default_ok("w3"),
        _default_ok("w4"),
        _default_ok("w5"),
        _ev(EvidenceKind.STRONG, worker="s1", verdict=VerdictKind.CRITICAL),
    ]
    assert classify(evs) is NodeState.USER_BLOCKED


def test_classify_strong_ok_and_strong_critical_routes_to_user_blocked() -> None:
    evs = [
        _ev(EvidenceKind.STRONG, worker="s1", verdict=VerdictKind.OK),
        _ev(EvidenceKind.STRONG, worker="s2", verdict=VerdictKind.CRITICAL),
    ]
    assert classify(evs) is NodeState.USER_BLOCKED


# ===========================================================================
# classify — rule 4: enough DEFAULT OK → VERIFIED
# ===========================================================================
def test_classify_verified_by_three_default_ok_default_threshold() -> None:
    evs = [_default_ok(f"w{i}") for i in range(3)]
    assert classify(evs) is NodeState.VERIFIED


def test_classify_verified_threshold_respects_desired_pass_arg() -> None:
    evs = [_default_ok(f"w{i}") for i in range(2)]
    assert classify(evs, desired_pass=3) is NodeState.PENDING
    assert classify(evs, desired_pass=2) is NodeState.VERIFIED


def test_classify_dedup_means_same_worker_three_times_is_not_verified() -> None:
    evs = [_default_ok("w1") for _ in range(3)]
    assert classify(evs, desired_pass=3) is NodeState.PENDING


# ===========================================================================
# classify — rule 5: mixed DEFAULT → DISAGREEMENT
# ===========================================================================
def test_classify_disagreement_on_mixed_default_verdicts() -> None:
    evs = [_default_ok("w1"), _default_crit("w2")]
    assert classify(evs) is NodeState.DISAGREEMENT


def test_classify_disagreement_even_with_full_pass_count_if_critical_present() -> None:
    evs = [
        _default_ok("w1"),
        _default_ok("w2"),
        _default_ok("w3"),
        _default_crit("w4"),
    ]
    assert classify(evs) is NodeState.DISAGREEMENT


# ===========================================================================
# classify — rule 6: nothing decisive → PENDING
# ===========================================================================
def test_classify_pending_for_empty_evidence() -> None:
    assert classify([]) is NodeState.PENDING


def test_classify_pending_for_only_gap_or_abstain() -> None:
    evs = [
        _ev(EvidenceKind.DEFAULT, worker="w1", verdict=VerdictKind.GAP),
        _ev(EvidenceKind.DEFAULT, worker="w2", verdict=VerdictKind.ABSTAIN),
    ]
    assert classify(evs) is NodeState.PENDING


# ===========================================================================
# next_action — terminal states
# ===========================================================================
@pytest.mark.parametrize(
    "terminal_state",
    [NodeState.VERIFIED, NodeState.REFUTED, NodeState.USER_BLOCKED],
)
def test_next_action_terminal_states_return_none(terminal_state: NodeState) -> None:
    assert next_action(terminal_state, []) is Action.NONE


# ===========================================================================
# next_action — PENDING ladder rung
# ===========================================================================
def test_next_action_pending_returns_default_verify() -> None:
    assert next_action(NodeState.PENDING, []) is Action.DEFAULT_VERIFY


def test_next_action_pending_with_partial_default_evidence_still_defaults() -> None:
    evs = [_default_ok("w1")]
    assert next_action(NodeState.PENDING, evs) is Action.DEFAULT_VERIFY


# ===========================================================================
# next_action — DISAGREEMENT escalation ladder
# ===========================================================================
def test_next_action_disagreement_first_escalates_to_refute() -> None:
    evs = [_default_ok("w1"), _default_crit("w2")]
    assert next_action(NodeState.DISAGREEMENT, evs) is Action.REFUTE


def test_next_action_disagreement_after_refute_escalates_to_strong() -> None:
    evs = [
        _default_ok("w1"),
        _default_crit("w2"),
        _ev(
            EvidenceKind.REFUTE,
            worker="r1",
            verdict=VerdictKind.OK,
            counterex=None,
        ),
    ]
    assert next_action(NodeState.DISAGREEMENT, evs) is Action.STRONG_VERIFY


def test_next_action_disagreement_after_refute_and_strong_falls_to_user_blocked() -> None:
    evs = [
        _default_ok("w1"),
        _default_crit("w2"),
        _ev(EvidenceKind.REFUTE, worker="r1", verdict=VerdictKind.OK, counterex=None),
        _ev(EvidenceKind.STRONG, worker="s1", verdict=VerdictKind.GAP),
    ]
    assert next_action(NodeState.DISAGREEMENT, evs) is Action.USER_BLOCKED


def test_next_action_respects_custom_budget_caps() -> None:
    evs = [_default_ok("w1"), _default_crit("w2")]
    budget = PolicyBudget(max_refute=0, max_strong=1)
    assert next_action(NodeState.DISAGREEMENT, evs, budget) is Action.STRONG_VERIFY

    budget_zero = PolicyBudget(max_refute=0, max_strong=0)
    assert next_action(NodeState.DISAGREEMENT, evs, budget_zero) is Action.USER_BLOCKED


# ===========================================================================
# Integration — classify + next_action across an evolving evidence ledger
# ===========================================================================
def test_lifecycle_pending_to_disagreement_to_strong_to_verified() -> None:
    """Walk a node from empty evidence to terminal state."""
    evs: list[Evidence] = []

    assert classify(evs) is NodeState.PENDING
    assert next_action(classify(evs), evs) is Action.DEFAULT_VERIFY

    evs.append(_default_ok("w1"))
    assert classify(evs) is NodeState.PENDING
    assert next_action(classify(evs), evs) is Action.DEFAULT_VERIFY

    evs.append(_default_crit("w2"))
    assert classify(evs) is NodeState.DISAGREEMENT
    assert next_action(classify(evs), evs) is Action.REFUTE

    evs.append(_ev(EvidenceKind.REFUTE, worker="r1", verdict=VerdictKind.OK, counterex=None))
    assert classify(evs) is NodeState.DISAGREEMENT
    assert next_action(classify(evs), evs) is Action.STRONG_VERIFY

    evs.append(_ev(EvidenceKind.STRONG, worker="s1", verdict=VerdictKind.OK))
    assert classify(evs) is NodeState.VERIFIED
    assert next_action(classify(evs), evs) is Action.NONE


def test_lifecycle_refute_finds_counterexample_short_circuits_to_refuted() -> None:
    evs: list[Evidence] = [
        _default_ok("w1"),
        _default_crit("w2"),
    ]
    assert classify(evs) is NodeState.DISAGREEMENT
    evs.append(
        _ev(
            EvidenceKind.REFUTE,
            worker="r1",
            verdict=VerdictKind.CRITICAL,
            counterex="counterexample at boundary",
        )
    )
    assert classify(evs) is NodeState.REFUTED
    evs.append(_default_ok("w3"))
    evs.append(_default_ok("w4"))
    assert classify(evs) is NodeState.REFUTED
    assert next_action(NodeState.REFUTED, evs) is Action.NONE


# ===========================================================================
# Determinism — same input always yields same output (no hidden state)
# ===========================================================================
def test_classify_is_deterministic_under_evidence_reordering() -> None:
    evs = [
        _default_ok("w1"),
        _default_ok("w2"),
        _default_ok("w3"),
    ]
    s1 = classify(evs)
    s2 = classify(list(reversed(evs)))
    assert s1 is s2 is NodeState.VERIFIED


def test_classify_accepts_iterable_not_just_list() -> None:
    """`classify` is documented as accepting any Iterable[Evidence]."""

    def gen():
        yield _default_ok("w1")
        yield _default_ok("w2")
        yield _default_ok("w3")

    assert classify(gen()) is NodeState.VERIFIED
