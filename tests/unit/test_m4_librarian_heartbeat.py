"""M4 — librarian heartbeat resilience under apply-time lock contention.

A single dispatched APPLY can hold ``LibrarianDaemon._kb_lock`` for many
seconds (Merkle cascade on a deep dependency, BFS cycle check on a
large graph). The dashboard's 60s healthy / 300s down thresholds make
that look like a crashed daemon when the heartbeat shares a thread
with dispatch. The daemon now (a) writes heartbeats from a dedicated
pulse thread on ``heartbeat_interval`` and (b) acquires ``_kb_lock``
non-blockingly inside ``_heartbeat`` so the pulse never waits on
dispatch.
"""

from __future__ import annotations

import io
import threading
import time
from pathlib import Path

import pytest

from cli.workspace import WorkspacePaths
from librarian.daemon import LibrarianDaemon
from librarian.heartbeat import read_heartbeat


def _make_daemon(tmp_path: Path) -> LibrarianDaemon:
    """Build a daemon instance without calling ``run()``.

    Skips the lock + reader thread + Kuzu open so the test can poke the
    bare ``_heartbeat`` and ``_heartbeat_pulse`` paths.
    """
    (tmp_path / "runtime" / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "events").mkdir(parents=True, exist_ok=True)
    ws = WorkspacePaths(root=tmp_path)
    rx = io.BytesIO()
    tx = io.BytesIO()
    return LibrarianDaemon(ws, rx=rx, tx=tx, heartbeat_interval=0.05)


def test_heartbeat_does_not_block_when_kb_lock_held(tmp_path: Path) -> None:
    """``_heartbeat`` must use a non-blocking lock acquire so a slow
    apply holding ``_kb_lock`` cannot stall liveness reporting."""
    daemon = _make_daemon(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def _hold_lock() -> None:
        with daemon._kb_lock:
            started.set()
            release.wait(timeout=5.0)

    holder = threading.Thread(target=_hold_lock, daemon=True)
    holder.start()
    assert started.wait(timeout=2.0)

    t0 = time.monotonic()
    daemon._heartbeat()
    elapsed = time.monotonic() - t0

    release.set()
    holder.join(timeout=2.0)
    assert not holder.is_alive()

    assert elapsed < 1.0, f"_heartbeat blocked on kb_lock for {elapsed:.2f}s"
    hb = read_heartbeat(tmp_path / "runtime" / "state" / "librarian.json")
    assert hb is not None
    assert hb.get("updated_at")


def test_heartbeat_pulse_keeps_ticking_while_lock_held(tmp_path: Path) -> None:
    """The pulse thread keeps writing fresh ``updated_at`` values even
    when the dispatch path holds ``_kb_lock`` for the entire test."""
    daemon = _make_daemon(tmp_path)

    pulse_thread = threading.Thread(
        target=daemon._heartbeat_pulse, daemon=True
    )
    pulse_thread.start()

    try:
        with daemon._kb_lock:
            observed: set[str] = set()
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                hb = read_heartbeat(
                    tmp_path / "runtime" / "state" / "librarian.json"
                )
                if hb and hb.get("updated_at"):
                    observed.add(hb["updated_at"])
                time.sleep(0.05)
            # 50ms cadence over 1s should produce many distinct ticks
            # even though we never released the lock.
            assert len(observed) >= 3, observed
    finally:
        daemon._shutdown.set()
        pulse_thread.join(timeout=1.0)
        assert not pulse_thread.is_alive()
