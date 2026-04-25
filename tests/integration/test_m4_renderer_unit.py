"""M4 — heartbeat IO + renderer integration helpers.

These tests live under integration/ because they touch the filesystem
and exercise multiple modules together; they are intentionally fast.
"""

from __future__ import annotations

import json
from pathlib import Path

from librarian.heartbeat import (
    LIBRARIAN_JSON_SCHEMA,
    LibrarianHeartbeat,
    PHASE_READY,
    PHASE_REPLAYING,
    read_heartbeat,
    utc_now_iso,
    write_heartbeat,
)


def test_heartbeat_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "librarian.json"
    hb = LibrarianHeartbeat(
        pid=12345,
        started_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        startup_phase=PHASE_REPLAYING,
        events_applied_total=3,
        projection_backlog=2,
    )
    write_heartbeat(path, hb)
    parsed = read_heartbeat(path)
    assert parsed is not None
    assert parsed["schema"] == LIBRARIAN_JSON_SCHEMA
    assert parsed["pid"] == 12345
    assert parsed["startup_phase"] == PHASE_REPLAYING
    assert parsed["events_applied_total"] == 3
    assert parsed["projection_backlog"] == 2


def test_heartbeat_atomic_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "librarian.json"
    hb1 = LibrarianHeartbeat(pid=1, started_at="A", updated_at="A")
    hb2 = LibrarianHeartbeat(
        pid=2, started_at="A", updated_at="B", startup_phase=PHASE_READY
    )
    write_heartbeat(path, hb1)
    write_heartbeat(path, hb2)
    parsed = read_heartbeat(path)
    assert parsed is not None
    assert parsed["pid"] == 2
    assert parsed["startup_phase"] == PHASE_READY
    # No leftover tmp file:
    assert not (tmp_path / "librarian.json.tmp").exists()


def test_heartbeat_missing_returns_none(tmp_path: Path) -> None:
    assert read_heartbeat(tmp_path / "absent.json") is None


def test_heartbeat_malformed_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_heartbeat(p) is None
