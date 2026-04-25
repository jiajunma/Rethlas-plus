"""M5 — sliding-window outcome bookkeeping (§7.5 F5, §10.2 priority)."""

from __future__ import annotations

from common.runtime.reaper import OutcomeWindow


def test_three_consecutive_crashes_detected() -> None:
    w = OutcomeWindow()
    for _ in range(3):
        w.record(target="lem:x", kind="generator", status="crashed")
    assert w.consecutive_status(target="lem:x", kind="generator", status="crashed") == 3


def test_crashed_streak_breaks_on_success() -> None:
    w = OutcomeWindow()
    w.record(target="lem:x", kind="generator", status="crashed")
    w.record(target="lem:x", kind="generator", status="crashed")
    w.record(target="lem:x", kind="generator", status="applied")
    assert w.consecutive_status(target="lem:x", kind="generator", status="crashed") == 0


def test_consecutive_timeouts_per_target() -> None:
    w = OutcomeWindow()
    for _ in range(3):
        w.record(target="lem:y", kind="verifier", status="timed_out")
    assert w.consecutive_status(target="lem:y", kind="verifier", status="timed_out") == 3
    # other target untouched
    assert w.consecutive_status(target="lem:x", kind="verifier", status="timed_out") == 0


def test_apply_failed_same_reason_streak() -> None:
    w = OutcomeWindow()
    for _ in range(2):
        w.record(target="lem:z", kind="generator", status="apply_failed", reason="cycle")
    w.record(target="lem:z", kind="generator", status="apply_failed", reason="ref_missing")
    assert (
        w.consecutive_apply_failed_reason(target="lem:z", kind="generator", reason="ref_missing")
        == 1
    )
    # The cycle streak was broken by the ref_missing entry.
    assert (
        w.consecutive_apply_failed_reason(target="lem:z", kind="generator", reason="cycle")
        == 0
    )


def test_separate_target_kind_pairs_isolated() -> None:
    w = OutcomeWindow()
    w.record(target="lem:a", kind="generator", status="crashed")
    w.record(target="lem:a", kind="verifier", status="crashed")
    assert w.consecutive_status(target="lem:a", kind="generator", status="crashed") == 1
    assert w.consecutive_status(target="lem:a", kind="verifier", status="crashed") == 1


def test_window_is_bounded() -> None:
    w = OutcomeWindow(capacity=3)
    for _ in range(5):
        w.record(target="lem:b", kind="verifier", status="crashed")
    # Even after 5 entries with capacity 3, the consecutive count
    # reflects the buffered entries, capped at 3.
    assert w.consecutive_status(target="lem:b", kind="verifier", status="crashed") == 3
