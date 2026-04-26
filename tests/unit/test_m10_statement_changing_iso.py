"""M10 — _statement_changing_iso_ms must filter by event type.

The function is used by category D (``repair_count`` audit) as a
boundary: gap/critical verdicts at or before this iso_ms belong to the
*previous* statement_hash and should not count toward current
``repair_count``.

If verifier verdicts (which never touch statement_hash) are treated as
boundaries, the audit silently skips legitimate gap/critical events
that came after the last statement change, producing false-positive
``D_repair_count_drift`` violations.
"""

from __future__ import annotations

import json
from pathlib import Path

from common.events.filenames import format_filename
from linter.checks import _statement_changing_iso_ms


def _write_event(events_dir: Path, *, iso_ms: str, body: dict, seq: int = 1, uid: str = "0" * 16) -> Path:
    shard_name = f"{iso_ms[0:4]}-{iso_ms[4:6]}-{iso_ms[6:8]}"
    shard = events_dir / shard_name
    shard.mkdir(parents=True, exist_ok=True)
    fname = format_filename(
        iso_ms=iso_ms,
        event_type=body["type"],
        target=body.get("target"),
        actor=body["actor"],
        seq=seq,
        uid=uid,
    )
    path = shard / fname
    body["event_id"] = f"{iso_ms}-{seq:04d}-{uid}"
    path.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
    return path


def test_returns_none_when_no_events(tmp_path: Path) -> None:
    assert _statement_changing_iso_ms(tmp_path, "thm:t") is None


def test_returns_node_added_iso_ms(tmp_path: Path) -> None:
    _write_event(
        tmp_path,
        iso_ms="20260426T010000.000",
        body={
            "type": "user.node_added",
            "actor": "user:alice",
            "ts": "2026-04-26T01:00:00.000+08:00",
            "target": "thm:t",
            "payload": {"kind": "theorem", "statement": "T.", "proof": "p.", "remark": "", "source_note": ""},
        },
    )
    assert (
        _statement_changing_iso_ms(tmp_path, "thm:t") == "20260426T010000.000"
    )


def test_verifier_verdicts_do_not_advance_boundary(tmp_path: Path) -> None:
    """Reproduces the false-positive D_repair_count_drift scenario.

    A user.node_added at t1, then a verifier.run_completed at t2. The
    boundary should be t1 (statement-changing), NOT t2 (verifier — does
    not change statement). Without the fix, gap/critical events between
    t1 and t2 would be skipped by the category D audit.
    """
    _write_event(
        tmp_path,
        iso_ms="20260426T010000.000",
        body={
            "type": "user.node_added",
            "actor": "user:alice",
            "ts": "2026-04-26T01:00:00.000+08:00",
            "target": "thm:t",
            "payload": {"kind": "theorem", "statement": "T.", "proof": "p.", "remark": "", "source_note": ""},
        },
        seq=1,
        uid="a" * 16,
    )
    _write_event(
        tmp_path,
        iso_ms="20260426T020000.000",
        body={
            "type": "verifier.run_completed",
            "actor": "verifier:codex",
            "ts": "2026-04-26T02:00:00.000+08:00",
            "target": "thm:t",
            "payload": {
                "verdict": "gap",
                "verification_hash": "h" * 64,
                "verification_report": "",
                "repair_hint": "",
            },
        },
        seq=2,
        uid="b" * 16,
    )

    boundary = _statement_changing_iso_ms(tmp_path, "thm:t")
    assert boundary == "20260426T010000.000"


def test_hint_attached_does_not_advance_boundary(tmp_path: Path) -> None:
    """user.hint_attached touches the same target but never changes statement."""
    _write_event(
        tmp_path,
        iso_ms="20260426T010000.000",
        body={
            "type": "user.node_added",
            "actor": "user:alice",
            "ts": "2026-04-26T01:00:00.000+08:00",
            "target": "thm:t",
            "payload": {"kind": "theorem", "statement": "T.", "proof": "p.", "remark": "", "source_note": ""},
        },
        seq=1,
        uid="a" * 16,
    )
    _write_event(
        tmp_path,
        iso_ms="20260426T030000.000",
        body={
            "type": "user.hint_attached",
            "actor": "user:alice",
            "ts": "2026-04-26T03:00:00.000+08:00",
            "target": "thm:t",
            "payload": {"hint": "Try induction."},
        },
        seq=3,
        uid="c" * 16,
    )

    boundary = _statement_changing_iso_ms(tmp_path, "thm:t")
    assert boundary == "20260426T010000.000"


def test_node_revised_advances_boundary(tmp_path: Path) -> None:
    _write_event(
        tmp_path,
        iso_ms="20260426T010000.000",
        body={
            "type": "user.node_added",
            "actor": "user:alice",
            "ts": "2026-04-26T01:00:00.000+08:00",
            "target": "thm:t",
            "payload": {"kind": "theorem", "statement": "T.", "proof": "p.", "remark": "", "source_note": ""},
        },
        seq=1,
        uid="a" * 16,
    )
    _write_event(
        tmp_path,
        iso_ms="20260426T040000.000",
        body={
            "type": "user.node_revised",
            "actor": "user:alice",
            "ts": "2026-04-26T04:00:00.000+08:00",
            "target": "thm:t",
            "payload": {"kind": "theorem", "statement": "T2.", "remark": "", "source_note": ""},
        },
        seq=4,
        uid="d" * 16,
    )

    boundary = _statement_changing_iso_ms(tmp_path, "thm:t")
    assert boundary == "20260426T040000.000"


def test_generator_batch_advances_boundary_when_label_in_batch(tmp_path: Path) -> None:
    _write_event(
        tmp_path,
        iso_ms="20260426T010000.000",
        body={
            "type": "user.node_added",
            "actor": "user:alice",
            "ts": "2026-04-26T01:00:00.000+08:00",
            "target": "thm:t",
            "payload": {"kind": "theorem", "statement": "T.", "proof": "", "remark": "", "source_note": ""},
        },
        seq=1,
        uid="a" * 16,
    )
    _write_event(
        tmp_path,
        iso_ms="20260426T050000.000",
        body={
            "type": "generator.batch_committed",
            "actor": "generator:codex",
            "ts": "2026-04-26T05:00:00.000+08:00",
            "target": "thm:t",
            "payload": {
                "attempt_id": "gen-x",
                "target": "thm:t",
                "mode": "fresh",
                "nodes": [
                    {
                        "label": "thm:t",
                        "kind": "theorem",
                        "statement": "T.",
                        "proof": "p.",
                        "remark": "",
                        "source_note": "",
                    },
                ],
            },
        },
        seq=5,
        uid="e" * 16,
    )

    boundary = _statement_changing_iso_ms(tmp_path, "thm:t")
    assert boundary == "20260426T050000.000"
