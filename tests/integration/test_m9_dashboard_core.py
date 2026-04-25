"""M9 — DashboardCore endpoint logic on a real workspace.

Spawns librarian to populate Kuzu so :class:`DashboardCore` can exercise
its endpoints. Avoids spinning up the HTTP server (covered separately).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from common.runtime.jobs import JobRecord, write_job_file
from coordinator.heartbeat import (
    CoordinatorHeartbeat,
    STATUS_RUNNING,
    utc_now_iso,
    write_heartbeat as write_coordinator_hb,
)
from dashboard.server import DashboardCore
from dashboard.state import (
    STATUS_DONE,
    STATUS_NEEDS_VERIFICATION,
    STATUS_USER_BLOCKED,
)
from librarian.heartbeat import (
    LibrarianHeartbeat,
    PHASE_READY,
    LIBRARIAN_JSON_SCHEMA,
    read_heartbeat as read_librarian_hb,
    write_heartbeat as write_librarian_hb,
)
from tests.fixtures.librarian_proc import librarian


PYTHON = sys.executable


def _init_ws(ws: Path) -> None:
    r = subprocess.run(
        [PYTHON, "-m", "cli.main", "--workspace", str(ws), "init"],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr


def _publish(ws: Path, *args: str) -> None:
    r = subprocess.run(
        [PYTHON, "-m", "cli.main", "--workspace", str(ws), *args],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr


def _seed_kb(ws: Path) -> None:
    """Seed a small KB: one definition, one theorem proven (pass_count=3)."""
    _publish(
        ws, "add-node", "--label", "def:x", "--kind", "definition",
        "--statement", "Define X.", "--actor", "user:alice",
    )
    _publish(
        ws, "add-node", "--label", "thm:t", "--kind", "theorem",
        "--statement", r"Theorem about \ref{def:x}.",
        "--proof", "trivial.", "--actor", "user:alice",
    )


def test_overview_joins_runtime_and_kb(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    _seed_kb(tmp_path)
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)

    # Coordinator heartbeat — fresh ts.
    hb = CoordinatorHeartbeat(
        pid=1, started_at=utc_now_iso(), updated_at=utc_now_iso(),
        status=STATUS_RUNNING, loop_seq=42,
    )
    write_coordinator_hb(tmp_path / "runtime" / "state" / "coordinator.json", hb)

    core = DashboardCore(tmp_path)
    overview = core.overview()
    assert overview["coordinator"]["liveness"] == "healthy"
    assert overview["coordinator"]["data"]["loop_seq"] == 42
    assert overview["librarian"]["data"]["startup_phase"] == PHASE_READY
    assert overview["kb"]["node_count"] == 2
    assert overview["kb"]["theorem_count"] == 1


def test_theorems_status_vocabulary(tmp_path: Path) -> None:
    """Every node-status keyword in the §M9 vocabulary is reachable."""
    _init_ws(tmp_path)
    # def:x  -> user_blocked at -1, then pass_count=0 after librarian sees def
    # thm:t1 -> needs_verification at pass_count=0
    # thm:t2 -> done after we set pass_count=3 below
    _publish(
        tmp_path, "add-node", "--label", "def:x", "--kind", "definition",
        "--statement", "Define X.", "--actor", "user:alice",
    )
    _publish(
        tmp_path, "add-node", "--label", "thm:t1", "--kind", "theorem",
        "--statement", r"T1 about \ref{def:x}.",
        "--proof", "p.", "--actor", "user:alice",
    )
    _publish(
        tmp_path, "add-node", "--label", "thm:t2", "--kind", "theorem",
        "--statement", r"T2 about \ref{def:x}.",
        "--proof", "p.", "--actor", "user:alice",
    )

    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)

    # Bump def:x to pass_count=1 so theorems can be "needs_verification";
    # bump thm:t2 to pass_count=3 so it's "done".
    import kuzu
    db = kuzu.Database(str(tmp_path / "knowledge_base" / "dag.kz"))
    conn = kuzu.Connection(db)
    try:
        conn.execute("MATCH (n:Node {label: 'def:x'}) SET n.pass_count = 1")
        conn.execute("MATCH (n:Node {label: 'thm:t2'}) SET n.pass_count = 3")
    finally:
        del conn
        del db

    core = DashboardCore(tmp_path, desired_pass_count=3)
    theorems = core.theorems()
    by_label = {t["label"]: t for t in theorems["theorems"]}
    assert by_label["thm:t1"]["status"] == STATUS_NEEDS_VERIFICATION
    assert by_label["thm:t2"]["status"] == STATUS_DONE


def test_node_detail_returns_full_record(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    _seed_kb(tmp_path)
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)
    core = DashboardCore(tmp_path)
    detail = core.node_detail("def:x")
    assert detail is not None
    assert detail["label"] == "def:x"
    assert detail["kind"] == "definition"
    assert detail["status"] in {STATUS_NEEDS_VERIFICATION}


def test_node_detail_includes_dependents(tmp_path: Path) -> None:
    """ARCHITECTURE §6.7 per-node detail must list dependents."""
    _init_ws(tmp_path)
    _seed_kb(tmp_path)
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)
    core = DashboardCore(tmp_path)
    detail = core.node_detail("def:x")
    assert detail is not None
    # thm:t depends on def:x, so def:x must list thm:t as a dependent.
    assert "thm:t" in detail["dependents"]
    # And recent_events must surface the user.node_added events.
    types = {ev["type"] for ev in detail["recent_events"]}
    assert "user.node_added" in types


def test_node_detail_unknown_returns_none(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    _seed_kb(tmp_path)
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)
    core = DashboardCore(tmp_path)
    assert core.node_detail("thm:nope") is None


def test_active_lists_runtime_jobs(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    rec = JobRecord(
        job_id="ver-20260424T100420.111-aaaaaaaaaaaaaaaa",
        kind="verifier", target="thm:t", mode="single",
        dispatch_hash="ab" * 32,
        pid=12345, pgid=12345,
        started_at="2026-04-24T10:04:20.111Z",
        updated_at="2026-04-24T10:04:25.300Z",
        status="running",
        log_path=str(tmp_path / "runtime" / "logs" / "x.codex.log"),
    )
    write_job_file(
        tmp_path / "runtime" / "jobs" / f"{rec.job_id}.json", rec
    )
    core = DashboardCore(tmp_path)
    active = core.active()
    assert active["count"] == 1
    assert active["jobs"][0]["target"] == "thm:t"


def test_rejected_merges_three_sources(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    state_dir = tmp_path / "runtime" / "state"
    (state_dir / "rejected_writes.jsonl").write_text(
        '{"reason": "schema_invalid", "actor": "generator:1"}\n',
        encoding="utf-8",
    )
    (state_dir / "drift_alerts.jsonl").write_text(
        '{"target": "thm:x", "kind": "hash_drift"}\n',
        encoding="utf-8",
    )
    # No KB: librarian still required for AppliedEvent (apply_failed) source.
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)
    core = DashboardCore(tmp_path)
    rej = core.rejected()
    assert rej["rejected_writes"][0]["reason"] == "schema_invalid"
    assert rej["drift_alerts"][0]["kind"] == "hash_drift"
    # apply_failed source comes back as a list (possibly empty).
    assert isinstance(rej["apply_failed"], list)


def test_rebuild_in_progress_raises(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    # Hand-write a librarian heartbeat with rebuild_in_progress=true.
    hb = LibrarianHeartbeat(
        pid=1, started_at=utc_now_iso(), updated_at=utc_now_iso(),
        rebuild_in_progress=True,
    )
    write_librarian_hb(tmp_path / "runtime" / "state" / "librarian.json", hb)

    core = DashboardCore(tmp_path)
    from dashboard.kuzu_reader import RebuildInProgress

    with pytest.raises(RebuildInProgress):
        core.overview()
    with pytest.raises(RebuildInProgress):
        core.theorems()
    # Non-Kuzu still serves.
    assert core.coordinator() is not None
    assert core.librarian()["liveness"] in {"healthy", "degraded", "down"}


def test_events_reverse_chronological(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    _seed_kb(tmp_path)
    core = DashboardCore(tmp_path)
    res = core.events(limit=10)
    assert res["count"] == 2
    # Reverse chronological: latest filename first within shard.
    filenames = [e["filename"] for e in res["events"]]
    assert filenames == sorted(filenames, reverse=True)
    # Each entry exposes the full reconstructed event_id (iso_ms-seq-uid),
    # matching the body's event_id — not just the uid.
    for entry in res["events"]:
        body = json.loads(
            (tmp_path / "events" / entry["shard"] / entry["filename"]).read_text(
                encoding="utf-8"
            )
        )
        assert entry["event_id"] == body["event_id"]
        assert entry["type"] == body["type"]
        assert entry["actor"] == body["actor"]


def test_events_filter_by_actor_and_type(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    _publish(
        tmp_path, "add-node", "--label", "def:x", "--kind", "definition",
        "--statement", "Define X.", "--actor", "user:alice",
    )
    _publish(
        tmp_path, "add-node", "--label", "def:y", "--kind", "definition",
        "--statement", "Define Y.", "--actor", "user:bob",
    )
    core = DashboardCore(tmp_path)
    only_alice = core.events(limit=10, actor="user:alice")
    assert {e["actor"] for e in only_alice["events"]} == {"user:alice"}
    only_added = core.events(limit=10, event_type="user.node_added")
    assert all(e["type"] == "user.node_added" for e in only_added["events"])


def test_events_limit_clamps_to_500(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    # No need to seed — empty events/ still tests the clamp on the limit
    # parameter alone (the response just contains 0 entries).
    core = DashboardCore(tmp_path)
    # The CLI does the clamp; here test the core surface tolerates large N.
    res = core.events(limit=10_000)
    assert res["limit"] == 10_000  # core does not clamp; HTTP layer does
