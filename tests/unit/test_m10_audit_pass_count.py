"""M10 — linter pass_count audit must mirror projector reset semantics.

Per ARCHITECTURE §5.5.1 the projector treats a ``gap``/``critical``
verifier verdict as a hard reset: ``pass_count`` becomes ``-1`` and the
next ``accepted`` verdict sets it to ``1`` (not prior + 1). The linter's
audit must therefore count only the *trailing* run of ``accepted``
verdicts since the most recent gap/critical, not every accepted verdict
across the verification_hash.
"""

from __future__ import annotations

from common.kb.types import NodeKind
from linter.checks import _audit_pass_count


class _Row:
    """Stand-in for ``RawNodeRow``; the audit only reads a few fields."""

    def __init__(
        self, label: str, verification_hash: str, proof: str = "p."
    ) -> None:
        self.label = label
        self.proof = proof
        self.verification_hash = verification_hash


def _fact(label: str, vh: str, verdict: str) -> dict:
    return {
        "target": label,
        "payload": {"verification_hash": vh, "verdict": verdict},
    }


def test_no_matching_facts_returns_zero() -> None:
    row = _Row("thm:t", "vh1")
    assert _audit_pass_count(row, NodeKind.THEOREM, []) == 0


def test_single_accepted_returns_one() -> None:
    row = _Row("thm:t", "vh1")
    facts = [_fact("thm:t", "vh1", "accepted")]
    assert _audit_pass_count(row, NodeKind.THEOREM, facts) == 1


def test_two_consecutive_accepted_returns_two() -> None:
    row = _Row("thm:t", "vh1")
    facts = [
        _fact("thm:t", "vh1", "accepted"),
        _fact("thm:t", "vh1", "accepted"),
    ]
    assert _audit_pass_count(row, NodeKind.THEOREM, facts) == 2


def test_trailing_gap_returns_minus_one() -> None:
    row = _Row("thm:t", "vh1")
    facts = [
        _fact("thm:t", "vh1", "accepted"),
        _fact("thm:t", "vh1", "accepted"),
        _fact("thm:t", "vh1", "gap"),
    ]
    assert _audit_pass_count(row, NodeKind.THEOREM, facts) == -1


def test_accepted_after_gap_resets_pass_count() -> None:
    """Gap wipes the prior 2 accepteds; only the 1 accepted after counts."""
    row = _Row("thm:t", "vh1")
    facts = [
        _fact("thm:t", "vh1", "accepted"),
        _fact("thm:t", "vh1", "accepted"),
        _fact("thm:t", "vh1", "gap"),
        _fact("thm:t", "vh1", "accepted"),
    ]
    # Projector trace: 0 -> 1 -> 2 -> -1 -> 1 (pass_count after each step).
    assert _audit_pass_count(row, NodeKind.THEOREM, facts) == 1


def test_two_accepted_after_critical_returns_two() -> None:
    row = _Row("thm:t", "vh1")
    facts = [
        _fact("thm:t", "vh1", "accepted"),
        _fact("thm:t", "vh1", "critical"),
        _fact("thm:t", "vh1", "accepted"),
        _fact("thm:t", "vh1", "accepted"),
    ]
    # Projector trace: 0 -> 1 -> -1 -> 1 -> 2.
    assert _audit_pass_count(row, NodeKind.THEOREM, facts) == 2


def test_facts_for_other_targets_ignored() -> None:
    row = _Row("thm:t", "vh1")
    facts = [
        _fact("thm:other", "vh1", "accepted"),
        _fact("thm:t", "vh1", "accepted"),
    ]
    assert _audit_pass_count(row, NodeKind.THEOREM, facts) == 1


def test_facts_with_other_vh_ignored() -> None:
    row = _Row("thm:t", "vh_current")
    facts = [
        _fact("thm:t", "vh_old", "accepted"),
        _fact("thm:t", "vh_old", "accepted"),
        _fact("thm:t", "vh_current", "accepted"),
    ]
    assert _audit_pass_count(row, NodeKind.THEOREM, facts) == 1


def test_proof_required_but_empty_returns_minus_one() -> None:
    row = _Row("thm:t", "vh1", proof="")
    facts = [_fact("thm:t", "vh1", "accepted")]
    assert _audit_pass_count(row, NodeKind.THEOREM, facts) == -1
