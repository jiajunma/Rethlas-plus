"""M1 — atomic event write fsync dance (§9.1 G3).

The write helper MUST fsync both the file fd and the parent directory
fd, in that order, separated by the rename. Without the directory fsync
a kernel / power crash leaves the file bytes on disk but its directory
entry invisible.
"""

from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from common.events.io import atomic_write_event, event_sha256, read_event


def _sample_event_bytes() -> bytes:
    body = {
        "event_id": "20260423T143015.123-0001-a7b2c912d4f1e380",
        "type": "user.node_added",
        "actor": "user:alice",
        "ts": "2026-04-23T14:30:15.123+08:00",
        "target": "def:primary_object",
        "payload": {
            "kind": "definition",
            "statement": "A primary object is ...",
            "remark": "",
            "source_note": "",
        },
    }
    return json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")


def test_atomic_write_round_trip(tmp_path: Path) -> None:
    body = _sample_event_bytes()
    target = tmp_path / "events" / "2026-04-23" / "sample.json"
    target.parent.mkdir(parents=True)

    written = atomic_write_event(target, body)
    assert written == target
    assert target.read_bytes() == body

    # A leftover ``.tmp`` file must not remain.
    assert not target.with_name(target.name + ".tmp").exists()

    raw, parsed = read_event(target)
    assert raw == body
    assert parsed["event_id"] == "20260423T143015.123-0001-a7b2c912d4f1e380"


def test_fsync_sequence_is_file_then_rename_then_dir(tmp_path: Path) -> None:
    body = _sample_event_bytes()
    date_dir = tmp_path / "events" / "2026-04-23"
    date_dir.mkdir(parents=True)
    target = date_dir / "sample.json"

    fsync_calls: list[int] = []
    rename_calls: list[tuple[str, str]] = []

    real_fsync = os.fsync
    real_rename = os.rename

    def tracking_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    def tracking_rename(src: str | os.PathLike, dst: str | os.PathLike) -> None:
        rename_calls.append((str(src), str(dst)))
        real_rename(src, dst)

    with patch("common.events.io.os.fsync", side_effect=tracking_fsync), patch(
        "common.events.io.os.rename", side_effect=tracking_rename
    ):
        atomic_write_event(target, body)

    assert len(fsync_calls) == 2, (
        f"expected exactly two fsync() calls (file + dir), got {len(fsync_calls)}"
    )
    assert len(rename_calls) == 1, (
        f"expected exactly one rename() call, got {len(rename_calls)}"
    )
    # Both distinct fd values — file fd freed before dir fd opens, so they
    # may happen to reuse the same number; that's fine. What matters is
    # the order: file fsync, rename, dir fsync.

    # Assert ordering — file fsync BEFORE rename BEFORE dir fsync.
    # We check against `rename`'s position in an interleaved trace.
    events: list[tuple[str, object]] = []

    def event_trace_fsync(fd: int) -> None:
        events.append(("fsync", fd))
        real_fsync(fd)

    def event_trace_rename(src: str | os.PathLike, dst: str | os.PathLike) -> None:
        events.append(("rename", (str(src), str(dst))))
        real_rename(src, dst)

    # Re-run against a new target so state is clean.
    target2 = date_dir / "sample2.json"
    with patch("common.events.io.os.fsync", side_effect=event_trace_fsync), patch(
        "common.events.io.os.rename", side_effect=event_trace_rename
    ):
        atomic_write_event(target2, body)

    kinds = [k for k, _ in events]
    assert kinds == ["fsync", "rename", "fsync"], (
        f"expected [fsync, rename, fsync] sequence, got {kinds}"
    )


def test_atomic_write_rejects_existing_target(tmp_path: Path) -> None:
    body = _sample_event_bytes()
    date_dir = tmp_path / "events" / "2026-04-23"
    date_dir.mkdir(parents=True)
    target = date_dir / "sample.json"
    target.write_bytes(b"{}")
    with pytest.raises(FileExistsError):
        atomic_write_event(target, body)


def test_atomic_write_requires_existing_parent(tmp_path: Path) -> None:
    target = tmp_path / "events" / "nope" / "sample.json"
    with pytest.raises(FileNotFoundError):
        atomic_write_event(target, b"{}")


def test_event_sha256_matches_on_disk_bytes(tmp_path: Path) -> None:
    body = b'{"event_id":"x","tampered":"no"}'
    target = tmp_path / "events" / "2026-04-23" / "s.json"
    target.parent.mkdir(parents=True)
    atomic_write_event(target, body)

    expected = hashlib.sha256(body).hexdigest()
    assert event_sha256(body) == expected
    # Tampering should change the hash.
    tampered = body.replace(b"no", b"yes")
    assert event_sha256(tampered) != expected
