"""M11 — system fault matrix + replay determinism + golden snapshots.

These tests stitch M0–M10 components into the end-to-end paths
PHASE1 §M11 calls out. They use the ``fault`` and ``golden`` markers
so CI can run them in dedicated stages.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cli.workspace import workspace_paths
from common.kb.kuzu_backend import KuzuBackend
from dashboard.server import DashboardCore
from librarian.heartbeat import (
    LIBRARIAN_JSON_SCHEMA,
    PHASE_READY,
    LibrarianHeartbeat,
    read_heartbeat as read_librarian_hb,
    write_heartbeat as write_librarian_hb,
    utc_now_iso,
)
from librarian.rebuild import rebuild_from_events
from linter.main import run_linter_on_workspace
from tests.fixtures.librarian_proc import librarian


PYTHON = sys.executable


def _init(ws: Path) -> None:
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


def _drive_to_ready(ws: Path) -> None:
    with librarian(ws) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=30.0)


# ---------------------------------------------------------------------------
# Scenario 11 — replay determinism across two fresh projections.
# ---------------------------------------------------------------------------
@pytest.mark.golden
def test_two_replays_produce_identical_kb_state(tmp_path: Path) -> None:
    """Same event stream → identical Kuzu state + nodes/ bytes.

    PHASE1 §M11.11. We seed events once, then replay them into two
    independent workspaces and compare the projected state.
    """
    seed = tmp_path / "seed"
    _init(seed)
    _publish(
        seed, "add-node", "--label", "def:x", "--kind", "definition",
        "--statement", "Define X.", "--actor", "user:alice",
    )
    _publish(
        seed, "add-node", "--label", "thm:t", "--kind", "theorem",
        "--statement", r"T about \ref{def:x}.", "--proof", "trivial.",
        "--actor", "user:alice",
    )

    def _replay_to(target: Path) -> dict:
        target.mkdir()
        # Manually create the workspace skeleton without rethlas init —
        # otherwise init's gitignore / config / etc. introduce extra
        # files we don't want compared.
        (target / "events").mkdir()
        (target / "knowledge_base").mkdir()
        (target / "knowledge_base" / "nodes").mkdir()
        (target / "runtime").mkdir()
        # Copy the seeded events.
        seed_events = seed / "events"
        for shard in seed_events.iterdir():
            if not shard.is_dir():
                continue
            (target / "events" / shard.name).mkdir(exist_ok=True)
            for f in shard.glob("*.json"):
                (target / "events" / shard.name / f.name).write_bytes(
                    f.read_bytes()
                )
        # Replay.
        backend = KuzuBackend(str(target / "knowledge_base" / "dag.kz"))
        try:
            rebuild_from_events(
                backend=backend,
                events_root=target / "events",
                nodes_dir=target / "knowledge_base" / "nodes",
            )
            labels = sorted(backend.node_labels())
            snapshot = {
                "labels": labels,
                "nodes": [],
            }
            for lbl in labels:
                row = backend.node_by_label(lbl)
                deps = sorted(backend.dependencies_of(lbl))
                snapshot["nodes"].append(
                    {
                        "label": row.label,
                        "kind": row.kind,
                        "pass_count": row.pass_count,
                        "repair_count": row.repair_count,
                        "statement_hash": row.statement_hash,
                        "verification_hash": row.verification_hash,
                        "deps": deps,
                    }
                )
            applied_count = 0
            res = backend._conn.execute("MATCH (a:AppliedEvent) RETURN count(*)")
            if res.has_next():
                applied_count = int(res.get_next()[0])
            snapshot["applied_event_count"] = applied_count
        finally:
            backend.close()
        # Hash all rendered node files.
        files = sorted((target / "knowledge_base" / "nodes").glob("*.md"))
        snapshot["nodes_md"] = {f.name: f.read_bytes().hex() for f in files}
        return snapshot

    a_snap = _replay_to(tmp_path / "replay_a")
    b_snap = _replay_to(tmp_path / "replay_b")
    assert a_snap == b_snap


# ---------------------------------------------------------------------------
# Scenario 12 — dashboard golden snapshots.
# ---------------------------------------------------------------------------
@pytest.mark.golden
def test_dashboard_golden_overview_and_theorems(tmp_path: Path) -> None:
    """``/api/overview`` and ``/api/theorems`` shape stable on a fixture."""
    _init(tmp_path)
    _publish(
        tmp_path, "add-node", "--label", "def:x", "--kind", "definition",
        "--statement", "Define X.", "--actor", "user:alice",
    )
    _publish(
        tmp_path, "add-node", "--label", "thm:proven", "--kind", "theorem",
        "--statement", r"Proven about \ref{def:x}.", "--proof", "p.",
        "--actor", "user:alice",
    )
    _publish(
        tmp_path, "add-node", "--label", "thm:open", "--kind", "theorem",
        "--statement", r"Open about \ref{def:x}.",
        "--actor", "user:alice",
    )
    _drive_to_ready(tmp_path)

    # Bump def:x and thm:proven into the upper status bands so the
    # snapshot exercises status diversity.
    backend = KuzuBackend(str(tmp_path / "knowledge_base" / "dag.kz"))
    try:
        backend._conn.execute("MATCH (n:Node {label: 'def:x'}) SET n.pass_count = 1")
        backend._conn.execute("MATCH (n:Node {label: 'thm:proven'}) SET n.pass_count = 3")
    finally:
        backend.close()

    core = DashboardCore(tmp_path, desired_pass_count=3)
    overview = core.overview()
    theorems = core.theorems()

    # Overview shape must contain the documented fields.
    assert {"coordinator", "librarian", "kb", "in_flight_target_count"} <= overview.keys()
    assert overview["kb"]["theorem_count"] == 2
    assert overview["kb"]["done_count"] == 1
    assert overview["kb"]["unfinished_count"] >= 1

    # Theorems golden: status set is deterministic.
    by_label = {t["label"]: t for t in theorems["theorems"]}
    assert by_label["thm:proven"]["status"] == "done"
    # thm:open has no proof, so it sits in the generation band.
    assert by_label["thm:open"]["status"] in {
        "needs_generation",
        "generation_blocked_on_dependency",
    }


@pytest.mark.golden
def test_dashboard_node_detail_golden(tmp_path: Path) -> None:
    _init(tmp_path)
    _publish(
        tmp_path, "add-node", "--label", "def:x", "--kind", "definition",
        "--statement", "Define X.", "--actor", "user:alice",
    )
    _drive_to_ready(tmp_path)
    core = DashboardCore(tmp_path)
    detail = core.node_detail("def:x")
    assert detail is not None
    # Every documented field is present (ARCHITECTURE §6.7 per-node detail).
    expected = {
        "label", "kind", "statement", "proof", "pass_count", "repair_count",
        "statement_hash", "verification_hash", "repair_hint",
        "verification_report", "deps", "dependents", "status",
        "active_job", "recent_events",
    }
    assert expected <= set(detail)
    assert detail["label"] == "def:x"
    assert detail["statement"] == "Define X."
    # statement_hash is content-derived → stable across runs.
    sh = detail["statement_hash"]
    assert isinstance(sh, str) and len(sh) == 64


# ---------------------------------------------------------------------------
# Scenario 13 — cross-generator label race.
# ---------------------------------------------------------------------------
@pytest.mark.fault
def test_cross_generator_label_race_yields_apply_failed(tmp_path: Path) -> None:
    """Two generator batches race on the same brand-new aux label.

    First-to-apply wins; second is rejected with ``label_conflict``.
    """
    _init(tmp_path)
    _publish(
        tmp_path, "add-node", "--label", "def:x", "--kind", "definition",
        "--statement", "Define X.", "--actor", "user:alice",
    )
    _publish(
        tmp_path, "add-node", "--label", "thm:goal_a", "--kind", "theorem",
        "--statement", r"A about \ref{def:x}.", "--actor", "user:alice",
    )
    _publish(
        tmp_path, "add-node", "--label", "thm:goal_b", "--kind", "theorem",
        "--statement", r"B about \ref{def:x}.", "--actor", "user:alice",
    )

    # Drive the user events into Kuzu so the projector has predecessors.
    _drive_to_ready(tmp_path)

    # Construct two generator.batch_committed events that both invent
    # the same aux label ``lem:helper``.  The projector recomputes
    # statement/verification hashes from scratch, so we don't bother
    # supplying them.
    from common.events.ids import allocate_event_id
    from common.events.io import atomic_write_event

    def _build_event(target: str, actor: str) -> tuple[Path, dict]:
        eid = allocate_event_id()
        helper_stmt = r"Helper lemma about \ref{def:x}."
        target_stmt = (
            r"A about \ref{def:x}." if target == "thm:goal_a"
            else r"B about \ref{def:x}."
        )
        body = {
            "event_id": eid.event_id,
            "type": "generator.batch_committed",
            "actor": actor,
            "ts": "2026-04-25T00:00:00.000Z",
            "target": target,
            "payload": {
                "attempt_id": f"gen-{eid.event_id}",
                "target": target,
                "mode": "fresh",
                "h_target_dispatch": "00" * 32,
                "h_rejected": "",
                "nodes": [
                    {
                        "label": "lem:helper",
                        "kind": "lemma",
                        "statement": helper_stmt,
                        "proof": "trivial.",
                        "remark": "",
                        "source_note": "",
                    },
                    {
                        "label": target,
                        "kind": "theorem",
                        "statement": target_stmt,
                        "proof": r"By \ref{lem:helper}.",
                        "remark": "",
                        "source_note": "",
                    },
                ],
            },
            "cost": {"prompt_tokens": 0, "completion_tokens": 0},
        }
        shard = tmp_path / "events" / "2026-04-25"
        shard.mkdir(parents=True, exist_ok=True)
        from common.events.filenames import format_filename, escape_label
        fname = format_filename(
            iso_ms=eid.iso_ms,
            event_type="generator.batch_committed",
            target=target,
            actor=actor,
            seq=eid.seq,
            uid=eid.uid,
        )
        path = shard / fname
        atomic_write_event(path, json.dumps(body).encode("utf-8"))
        return path, body

    a_path, a_body = _build_event("thm:goal_a", "generator:race-a")
    b_path, b_body = _build_event("thm:goal_b", "generator:race-b")

    # Restart librarian — replay handles a then b in event_id order.
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=30.0)

    # Inspect AppliedEvent for both: a applied, b apply_failed(label_conflict).
    backend = KuzuBackend(str(tmp_path / "knowledge_base" / "dag.kz"))
    try:
        a_row = backend.applied_event(a_body["event_id"])
        b_row = backend.applied_event(b_body["event_id"])
    finally:
        backend.close()

    assert a_row is not None and b_row is not None
    # Whichever event_id lexicographically lands first wins.
    winner_id = min(a_body["event_id"], b_body["event_id"])
    loser_id = max(a_body["event_id"], b_body["event_id"])
    winner = a_row if a_body["event_id"] == winner_id else b_row
    loser = a_row if a_body["event_id"] == loser_id else b_row
    assert winner.status.value == "applied", (
        winner.status.value, winner.reason, winner.detail
    )
    assert loser.status.value == "apply_failed", (
        loser.status.value, loser.reason, loser.detail
    )
    assert loser.reason == "label_conflict", (loser.reason, loser.detail)


# ---------------------------------------------------------------------------
# Scenario 8 — interrupted rebuild flag forces fresh rebuild on next start.
# ---------------------------------------------------------------------------
@pytest.mark.fault
def test_interrupted_rebuild_flag_forces_rebuild_on_next_startup(tmp_path: Path) -> None:
    _init(tmp_path)
    _publish(
        tmp_path, "add-node", "--label", "def:x", "--kind", "definition",
        "--statement", "Define X.", "--actor", "user:alice",
    )
    _drive_to_ready(tmp_path)

    # Stamp the interrupted-rebuild flag.
    flag = tmp_path / "runtime" / "state" / "rebuild_in_progress.flag"
    flag.write_text(json.dumps({"started_at": "2026-04-25T00:00:00.000Z"}), encoding="utf-8")

    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=30.0)
        hb = read_librarian_hb(tmp_path / "runtime" / "state" / "librarian.json")
        assert hb is not None
        assert hb["rebuild_in_progress"] is False

    # The flag is gone after the rebuild settled.
    assert not flag.is_file()


# ---------------------------------------------------------------------------
# Scenario 10 — inventory drift caught by linter category F (system-level).
# ---------------------------------------------------------------------------
@pytest.mark.fault
def test_inventory_drift_after_apply_caught_by_linter(tmp_path: Path) -> None:
    _init(tmp_path)
    _publish(
        tmp_path, "add-node", "--label", "def:x", "--kind", "definition",
        "--statement", "Define X.", "--actor", "user:alice",
    )
    _drive_to_ready(tmp_path)
    files = sorted((tmp_path / "events").rglob("*.json"))
    # Append junk after the JSON body — body still parses (with extra
    # whitespace) but the SHA-256 changes.
    files[0].write_text(files[0].read_text(encoding="utf-8") + "\n\n", encoding="utf-8")

    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    assert rc == 5
    report = json.loads(
        (tmp_path / "runtime" / "state" / "linter_report.json").read_text(encoding="utf-8")
    )
    assert any(
        v["code"] == "F_event_sha256_mismatch" for v in report["f"]["violations"]
    )
