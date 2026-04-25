"""M8 — events watcher surfaces malformed canonical files as corruption."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from common.events.filenames import format_filename
from coordinator.events_watcher import EventsWatcher, WatcherCorruption


def _event_path(root: Path, *, iso_ms: str, seq: int, uid: str) -> Path:
    shard = root / "2026-04-25"
    shard.mkdir(parents=True, exist_ok=True)
    name = format_filename(
        iso_ms=iso_ms,
        event_type="user.node_added",
        target="def:x",
        actor="user:alice",
        seq=seq,
        uid=uid,
    )
    return shard / name


def test_invalid_json_raises_corruption(tmp_path: Path) -> None:
    path = _event_path(
        tmp_path, iso_ms="20260425T010000.000", seq=1, uid="a" * 16
    )
    path.write_text("{not json", encoding="utf-8")

    watcher = EventsWatcher(tmp_path)
    with pytest.raises(WatcherCorruption, match="body unreadable"):
        watcher.poll()


def test_event_id_mismatch_raises_corruption(tmp_path: Path) -> None:
    path = _event_path(
        tmp_path, iso_ms="20260425T010000.000", seq=1, uid="b" * 16
    )
    body = {
        "event_id": "20260425T010000.000-0002-bbbbbbbbbbbbbbbb",
        "type": "user.node_added",
        "actor": "user:alice",
        "ts": "2026-04-25T01:00:00.000+08:00",
        "target": "def:x",
        "payload": {
            "kind": "definition",
            "statement": "Define X.",
            "remark": "",
            "source_note": "",
        },
    }
    path.write_text(json.dumps(body), encoding="utf-8")

    watcher = EventsWatcher(tmp_path)
    with pytest.raises(WatcherCorruption, match="event_id mismatch"):
        watcher.poll()
