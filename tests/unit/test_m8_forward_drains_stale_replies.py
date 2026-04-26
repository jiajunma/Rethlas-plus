"""Coordinator must validate ``reply.event_id`` against the APPLY it sent.

ARCHITECTURE §6.5 frames the librarian channel as request/response, but
the request side has a finite timeout (30 s). When a single APPLY runs
longer than that, ``LibrarianChild.recv`` returns ``None`` and
``_forward_new_events`` returns early without acking the event. The
late reply still arrives in the stdout buffer.

On the next tick coordinator re-sends APPLY for the same event_id —
idempotent at the projector — so the librarian replies a *second* time.
That extra reply now sits in the pipe and would be misattributed to the
*next* APPLY (a different event_id) under naive request/recv pairing.
This test pins the post-fix behaviour: a reply whose ``event_id`` does
not match the APPLY's ``event_id`` is drained, and the next reply is
consumed instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from common.events.filenames import format_filename
from coordinator.events_watcher import EventsWatcher
from coordinator.main import _forward_new_events


_EID1 = "20260426T000000.000-0001-aaaaaaaaaaaaaaaa"
_EID2 = "20260426T000000.000-0002-bbbbbbbbbbbbbbbb"


class _FakeChannel:
    """Minimal duck-typed stand-in for :class:`coordinator.children.LibrarianChild`."""

    def __init__(self, scripted_replies: list[dict]) -> None:
        self.sent: list[dict] = []
        self.replies: list[dict] = list(scripted_replies)

    def request(self, payload: dict, *, timeout: float = 30.0) -> dict | None:
        self.sent.append(payload)
        return self.recv(timeout=timeout)

    def recv(self, timeout: float = 30.0) -> dict | None:
        if not self.replies:
            return None
        return self.replies.pop(0)


def _write_event(events_root: Path, *, iso_ms: str, seq: int, uid: str, target: str) -> Path:
    shard = events_root / "2026-04-26"
    shard.mkdir(parents=True, exist_ok=True)
    name = format_filename(
        iso_ms=iso_ms,
        event_type="user.node_added",
        target=target,
        actor="user:alice",
        seq=seq,
        uid=uid,
    )
    body = {
        "event_id": f"{iso_ms}-{seq:04d}-{uid}",
        "type": "user.node_added",
        "actor": "user:alice",
        "ts": "2026-04-26T00:00:00.000+00:00",
        "target": target,
        "payload": {"kind": "definition", "statement": "x.", "remark": "", "source_note": ""},
    }
    path = shard / name
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _make_state(watcher: EventsWatcher, channel: _FakeChannel) -> SimpleNamespace:
    return SimpleNamespace(
        watcher=watcher,
        librarian=channel,
        pending_corruption=False,
        last_corruption_detail="",
    )


def test_stale_reply_with_mismatched_event_id_is_drained(tmp_path: Path) -> None:
    events_root = tmp_path / "events"
    _write_event(events_root, iso_ms="20260426T000000.000", seq=1, uid="a" * 16, target="def:x")
    _write_event(events_root, iso_ms="20260426T000000.000", seq=2, uid="b" * 16, target="def:y")

    watcher = EventsWatcher(events_root)
    fake = _FakeChannel([
        # Late reply from a prior tick's APPLY ev1 (timed out at coord side).
        {"ok": True, "reply": "APPLIED", "event_id": _EID1},
        # Idempotent re-apply reply for ev1 (also event_id=ev1) — must be
        # drained when coordinator's APPLY ev2 reads from the pipe.
        {"ok": True, "reply": "APPLIED", "event_id": _EID1},
        # The actual reply for APPLY ev2.
        {"ok": True, "reply": "APPLIED", "event_id": _EID2},
    ])
    state = _make_state(watcher, fake)

    _forward_new_events(state)

    # Both events fully acked.
    assert watcher.poll() == []
    # Coordinator sent exactly one APPLY per event.
    assert [p["event_id"] for p in fake.sent] == [_EID1, _EID2]
    # All three scripted replies were consumed (stale ev1 reply drained).
    assert fake.replies == []
    assert state.pending_corruption is False


def test_only_matching_reply_acks_event(tmp_path: Path) -> None:
    """If the only reply for the second APPLY has a wrong event_id and no
    follow-up arrives, the event is NOT acked — coordinator returns
    early so the watcher will retry on the next tick."""
    events_root = tmp_path / "events"
    _write_event(events_root, iso_ms="20260426T000000.000", seq=1, uid="a" * 16, target="def:x")
    _write_event(events_root, iso_ms="20260426T000000.000", seq=2, uid="b" * 16, target="def:y")

    watcher = EventsWatcher(events_root)
    fake = _FakeChannel([
        {"ok": True, "reply": "APPLIED", "event_id": _EID1},
        # Mismatched + no follow-up. recv() will return None after this.
        {"ok": True, "reply": "APPLIED", "event_id": _EID1},
    ])
    state = _make_state(watcher, fake)

    _forward_new_events(state)

    # ev1 was acked (matching reply). ev2 was NOT — its only reply was
    # the stale ev1 one, which the coordinator must reject.
    pending = watcher.poll()
    assert len(pending) == 1
    assert pending[0].event_id == _EID2
    assert state.pending_corruption is False


def test_corruption_reply_propagates_with_matching_event_id(tmp_path: Path) -> None:
    events_root = tmp_path / "events"
    _write_event(events_root, iso_ms="20260426T000000.000", seq=1, uid="a" * 16, target="def:x")

    watcher = EventsWatcher(events_root)
    fake = _FakeChannel([
        {"ok": True, "reply": "CORRUPTION", "event_id": _EID1, "detail": "boom"},
    ])
    state = _make_state(watcher, fake)

    _forward_new_events(state)

    assert state.pending_corruption is True
    assert state.last_corruption_detail == "boom"
