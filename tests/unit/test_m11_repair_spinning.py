"""M11 — repair_spinning_count surfaces 3x consecutive same-reason apply_failed.

ARCHITECTURE §6.4.2 / §7.4 / §7.5: when a (target, kind) hits 3
consecutive ``apply_failed`` outcomes with the same reason, or 3
consecutive ``crashed`` / ``timed_out`` outcomes, the count of stuck
targets is reported in ``coordinator.json.repair_spinning_count``.

This test exercises the OutcomeWindow → heartbeat helper path
directly, without spinning up the full supervise loop.
"""

from __future__ import annotations

from common.runtime.reaper import OutcomeWindow


def _spinning(window: OutcomeWindow) -> int:
    """Mirror the heartbeat helper's repair_spinning calculation."""
    spinning = 0
    seen: set[tuple[str, str]] = set()
    for (target, kind), dq in window._buf.items():
        if (target, kind) in seen:
            continue
        seen.add((target, kind))
        triggered = False
        for status_marker in ("crashed", "timed_out"):
            if window.consecutive_status(
                target=target, kind=kind, status=status_marker
            ) >= 3:
                spinning += 1
                triggered = True
                break
        if triggered:
            continue
        last_reasons = {r for s, r in dq if s == "apply_failed" and r}
        for reason in last_reasons:
            if window.consecutive_apply_failed_reason(
                target=target, kind=kind, reason=reason
            ) >= 3:
                spinning += 1
                break
    return spinning


def test_three_consecutive_apply_failed_same_reason_increments() -> None:
    w = OutcomeWindow()
    for _ in range(3):
        w.record(
            target="thm:t", kind="generator",
            status="apply_failed", reason="label_conflict",
        )
    assert _spinning(w) == 1


def test_three_consecutive_crashes_increments() -> None:
    w = OutcomeWindow()
    for _ in range(3):
        w.record(target="thm:t", kind="generator", status="crashed")
    assert _spinning(w) == 1


def test_three_consecutive_timed_out_increments() -> None:
    w = OutcomeWindow()
    for _ in range(3):
        w.record(target="thm:t", kind="verifier", status="timed_out")
    assert _spinning(w) == 1


def test_two_consecutive_does_not_increment() -> None:
    w = OutcomeWindow()
    for _ in range(2):
        w.record(
            target="thm:t", kind="generator",
            status="apply_failed", reason="cycle",
        )
    assert _spinning(w) == 0


def test_mixed_reasons_does_not_increment() -> None:
    w = OutcomeWindow()
    w.record(target="thm:t", kind="generator", status="apply_failed", reason="cycle")
    w.record(target="thm:t", kind="generator", status="apply_failed", reason="label_conflict")
    w.record(target="thm:t", kind="generator", status="apply_failed", reason="cycle")
    assert _spinning(w) == 0


def test_independent_targets_count_independently() -> None:
    w = OutcomeWindow()
    for _ in range(3):
        w.record(target="thm:a", kind="generator", status="crashed")
    for _ in range(3):
        w.record(target="thm:b", kind="verifier", status="timed_out")
    assert _spinning(w) == 2


def test_recovery_resets_consecutive_counter() -> None:
    w = OutcomeWindow()
    for _ in range(3):
        w.record(
            target="thm:t", kind="generator",
            status="apply_failed", reason="cycle",
        )
    assert _spinning(w) == 1
    # An applied success in the middle resets the run.
    w.record(target="thm:t", kind="generator", status="applied")
    assert _spinning(w) == 0
