"""M4 — librarian daemon integration tests.

These tests spawn ``rethlas librarian`` as a subprocess and drive it
through its JSON-line stdio protocol. They cover:

- Startup phase progression (replaying -> reconciling -> ready).
- Idempotent startup replay (already-decided events skipped).
- APPLY command handling at runtime.
- APPLY-during-startup queuing (commands sent before ``ready``).
- ``nodes/`` reconciliation (crash window healing + orphan deletion).
- ``projection_backlog`` accounting.
- Idle heartbeat cadence.
- ``rebuild_in_progress.flag`` handling on startup.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cli.workspace import workspace_paths
from common.events.io import atomic_write_event
from librarian.heartbeat import (
    PHASE_READY,
    PHASE_RECONCILING,
    PHASE_REPLAYING,
    read_heartbeat,
)
from tests.fixtures.librarian_proc import librarian


PYTHON = sys.executable


def _init_workspace(ws: Path) -> None:
    r = subprocess.run(
        [PYTHON, "-m", "cli.main", "--workspace", str(ws), "init"],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr


def _publish_user_event(ws: Path, *args: str) -> None:
    r = subprocess.run(
        [PYTHON, "-m", "cli.main", "--workspace", str(ws), *args],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr


def _seed_events(ws: Path) -> None:
    """Drop two simple events on disk: a definition then a lemma using it."""
    _publish_user_event(
        ws, "add-node", "--label", "def:x", "--kind", "definition",
        "--statement", "Define X.", "--actor", "user:alice",
    )
    _publish_user_event(
        ws, "add-node", "--label", "lem:y", "--kind", "lemma",
        "--statement", r"Y depends on \ref{def:x}.",
        "--proof", "trivial.", "--actor", "user:alice",
    )


# ---------------------------------------------------------------------------
# Phase progression + replay
# ---------------------------------------------------------------------------
def test_startup_phase_transitions_to_ready(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=15.0)
        # Sanity: heartbeat fields look reasonable.
        hb = read_heartbeat(tmp_path / "runtime" / "state" / "librarian.json")
        assert hb is not None
        assert hb["startup_phase"] == PHASE_READY
        assert hb["pid"] > 0


def test_replay_processes_events_already_on_disk(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    _seed_events(tmp_path)

    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)
        hb = read_heartbeat(tmp_path / "runtime" / "state" / "librarian.json")
        assert hb is not None
        assert hb["events_applied_total"] == 2
        assert hb["projection_backlog"] == 0


def test_replay_skips_already_decided_events(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    _seed_events(tmp_path)
    # First run applies events.
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)

    # Second run must see them as already decided — the AppliedEvent table
    # must still hold exactly one row per event_id (idempotency).
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)
        hb2 = read_heartbeat(tmp_path / "runtime" / "state" / "librarian.json")
        assert hb2 is not None
        assert hb2["events_applied_total"] == 2
        assert hb2["events_apply_failed_total"] == 0
        assert hb2["projection_backlog"] == 0

    # After both runs: open a read-only Kuzu connection and assert the
    # AppliedEvent count equals the number of event files on disk.
    import kuzu
    db = kuzu.Database(str(tmp_path / "knowledge_base" / "dag.kz"), read_only=True)
    conn = kuzu.Connection(db)
    try:
        res = conn.execute("MATCH (a:AppliedEvent) RETURN count(*)")
        assert res.has_next()
        count = int(res.get_next()[0])
    finally:
        del conn
        del db
    files = list((tmp_path / "events").rglob("*.json"))
    assert count == len(files), (count, len(files))


# ---------------------------------------------------------------------------
# APPLY at runtime
# ---------------------------------------------------------------------------
def test_apply_command_at_runtime_returns_applied(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    # Spawn librarian with an empty events/ so it goes ready quickly.
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=15.0)

        # Drop a fresh user event onto disk.
        _publish_user_event(
            tmp_path, "add-node", "--label", "def:k", "--kind", "definition",
            "--statement", "Define K.", "--actor", "user:alice",
        )
        events = sorted((tmp_path / "events").rglob("*.json"))
        assert events, "expected a single event file"
        body = json.loads(events[-1].read_text(encoding="utf-8"))

        lp.send({"cmd": "APPLY", "event_id": body["event_id"], "path": str(events[-1])})
        reply = lp.recv(timeout=15.0)
        assert reply["ok"] is True
        assert reply["reply"] == "APPLIED"
        assert reply["event_id"] == body["event_id"]


def test_apply_command_renders_node_md(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=15.0)
        # Add an axiom (definition) — pass_count starts at 0, so no
        # nodes/*.md is rendered.
        _publish_user_event(
            tmp_path, "add-node", "--label", "def:x", "--kind", "definition",
            "--statement", "Define X.", "--actor", "user:alice",
        )
        events = sorted((tmp_path / "events").rglob("*.json"))
        body = json.loads(events[-1].read_text(encoding="utf-8"))
        lp.send({"cmd": "APPLY", "event_id": body["event_id"], "path": str(events[-1])})
        assert lp.recv()["reply"] == "APPLIED"
        # No render for unverified node.
        assert not (tmp_path / "knowledge_base" / "nodes" / "def_x.md").exists()


def test_ping_replies_pong(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=15.0)
        lp.send({"cmd": "PING"})
        reply = lp.recv()
        assert reply["reply"] == "PONG"
        assert reply["phase"] == PHASE_READY


def test_unknown_command_returns_error(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=15.0)
        lp.send({"cmd": "FOO"})
        reply = lp.recv()
        assert reply["ok"] is False
        assert "FOO" in reply["error"] or "unknown" in reply["error"].lower()


# ---------------------------------------------------------------------------
# APPLY-during-startup queuing
# ---------------------------------------------------------------------------
def test_apply_received_during_startup_is_processed_after_ready(tmp_path: Path) -> None:
    """Coordinator may send APPLY before librarian reaches ``ready``.

    The reader thread enqueues all messages; the steady-state loop only
    starts dequeuing after phase == ready. This test creates ~10 events
    so the replay phase takes a measurable amount of time, then sends
    an APPLY for an 11th fresh event before ready.
    """
    _init_workspace(tmp_path)
    # Seed so replay has something to chew on.
    for i in range(8):
        _publish_user_event(
            tmp_path, "add-node",
            "--label", f"def:n{i}", "--kind", "definition",
            "--statement", f"Define n{i}.", "--actor", "user:alice",
        )
    # Add one extra event the daemon will see after we send an APPLY.
    _publish_user_event(
        tmp_path, "add-node", "--label", "def:k", "--kind", "definition",
        "--statement", "Define K.", "--actor", "user:alice",
    )
    events = sorted((tmp_path / "events").rglob("*.json"))
    last_event = events[-1]
    body = json.loads(last_event.read_text(encoding="utf-8"))

    with librarian(tmp_path) as lp:
        # Send APPLY immediately, before waiting for ready.
        lp.send(
            {"cmd": "APPLY", "event_id": body["event_id"], "path": str(last_event)}
        )
        # Wait for the reply (queued through replay -> reconcile -> ready).
        reply = lp.recv(timeout=30.0)
        assert reply["reply"] in {"APPLIED", "APPLY_FAILED"}
        # Idempotent re-apply path: replay already applied this event so
        # the runtime APPLY sees the existing AppliedEvent row and short-
        # circuits to "APPLIED".
        assert reply["reply"] == "APPLIED"


# ---------------------------------------------------------------------------
# nodes/ reconciliation
# ---------------------------------------------------------------------------
def test_orphan_node_md_files_are_deleted(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    nodes_dir = tmp_path / "knowledge_base" / "nodes"
    nodes_dir.mkdir(parents=True, exist_ok=True)
    # Stale file with a known prefix that maps to no Kuzu row.
    (nodes_dir / "lem_ghost.md").write_text("stale", encoding="utf-8")
    # Operator note (unknown prefix) — must NOT be deleted.
    (nodes_dir / "operator_notes.md").write_text("keep", encoding="utf-8")

    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=15.0)
        assert not (nodes_dir / "lem_ghost.md").exists()
        assert (nodes_dir / "operator_notes.md").exists()


def test_stale_tmp_file_in_nodes_dir_is_cleaned_up(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    nodes_dir = tmp_path / "knowledge_base" / "nodes"
    nodes_dir.mkdir(parents=True, exist_ok=True)
    (nodes_dir / "lem_orphan.md.tmp").write_text("partial", encoding="utf-8")
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=15.0)
        assert not (nodes_dir / "lem_orphan.md.tmp").exists()


# ---------------------------------------------------------------------------
# Backlog
# ---------------------------------------------------------------------------
def test_projection_backlog_reaches_zero_after_replay(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    _seed_events(tmp_path)
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)
        hb = read_heartbeat(tmp_path / "runtime" / "state" / "librarian.json")
        assert hb is not None
        assert hb["projection_backlog"] == 0


# ---------------------------------------------------------------------------
# Heartbeat cadence
# ---------------------------------------------------------------------------
def test_idle_heartbeat_updates_periodically(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    # Use a very short cadence for the test (200 ms default in fixture).
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=15.0)
        path = tmp_path / "runtime" / "state" / "librarian.json"
        observed: list[str] = []
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            hb = read_heartbeat(path)
            if hb and hb.get("updated_at") and hb["updated_at"] not in observed:
                observed.append(hb["updated_at"])
            time.sleep(0.1)
        # With 200 ms cadence over 3 s we expect well over 3 unique heartbeats.
        assert len(observed) >= 3, observed


# ---------------------------------------------------------------------------
# rebuild_in_progress.flag handling
# ---------------------------------------------------------------------------
def test_interrupted_rebuild_flag_triggers_clean_rebuild(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    _seed_events(tmp_path)
    # Simulate a crashed `rethlas rebuild`: write the flag with no Kuzu yet.
    flag = tmp_path / "runtime" / "state" / "rebuild_in_progress.flag"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(json.dumps({"started_at": "2026-04-25T00:00:00.000Z"}), encoding="utf-8")

    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)
        # Flag must be cleared after recovery.
        assert not flag.exists()
        hb = read_heartbeat(tmp_path / "runtime" / "state" / "librarian.json")
        assert hb is not None
        assert hb["rebuild_in_progress"] is False
        assert hb["last_rebuild_at"] is not None


# ---------------------------------------------------------------------------
# Lock contention
# ---------------------------------------------------------------------------
def test_two_librarians_on_same_workspace_second_exits(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=15.0)
        # Now try to start a second one — it should fail fast.
        env = os.environ.copy()
        env["RETHLAS_LIBRARIAN_HEARTBEAT_S"] = "0.2"
        r = subprocess.run(
            [PYTHON, "-m", "cli.main", "--workspace", str(tmp_path), "librarian"],
            capture_output=True, text=True, env=env, timeout=15,
            input="",  # close stdin immediately
            check=False,
        )
        assert r.returncode != 0
        assert "librarian.lock" in r.stderr.lower()
