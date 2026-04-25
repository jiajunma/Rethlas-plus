"""M4 — workspace-corruption paths surface as ``status=degraded``.

Two scenarios:

1. **Producers.toml replay-time enforcement**: an event file whose
   ``(actor, type)`` pair is not registered in producers.toml has
   bypassed admission (manual file drop, git revert). On startup
   replay the librarian halts as workspace corruption.

2. **Cross-batch cycle introduction caught at apply time**: a
   generator.batch_committed batch whose *internal* shape is acyclic
   but which closes a cycle through *existing* KB edges must be
   apply_failed with reason ``cycle``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from common.events.filenames import format_filename
from common.events.io import atomic_write_event
from librarian.heartbeat import (
    PHASE_READY,
    STATUS_DEGRADED,
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


def _publish(ws: Path, *args: str) -> None:
    r = subprocess.run(
        [PYTHON, "-m", "cli.main", "--workspace", str(ws), *args],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr


def _drop_canonical_event(ws: Path, body: dict, *, iso_ms: str = "20260424T010000.000",
                          seq: int = 1, uid: str = "f" * 16) -> Path:
    """Hand-drop a fully formed event under events/ bypassing the CLI."""
    date_dir = ws / "events" / "2026-04-24"
    date_dir.mkdir(parents=True, exist_ok=True)
    fname = format_filename(
        iso_ms=iso_ms,
        event_type=body["type"],
        target=body.get("target"),
        actor=body["actor"],
        seq=seq,
        uid=uid,
    )
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return atomic_write_event(date_dir / fname, raw)


def test_unregistered_actor_at_replay_marks_degraded(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    # Hand-drop an event with an actor that producers.toml does not allow.
    body = {
        "event_id": "20260424T010000.000-0001-ffffffffffffffff",
        "type": "user.node_added",
        "actor": "rogue:ghost",  # not matched by any producer
        "ts": "2026-04-24T01:00:00.000+00:00",
        "target": "def:x",
        "payload": {
            "kind": "definition",
            "statement": "Define X.",
            "remark": "",
            "source_note": "",
        },
    }
    _drop_canonical_event(tmp_path, body)

    with librarian(tmp_path) as lp:
        # Daemon should still walk through phases (it doesn't crash on
        # corruption — it sets status=degraded and continues).
        lp.wait_for_phase(PHASE_READY, timeout=20.0)
        hb = read_heartbeat(tmp_path / "runtime" / "state" / "librarian.json")
        assert hb is not None
        assert hb["status"] == STATUS_DEGRADED, hb
        assert "corruption" in hb["last_error"].lower() or "rogue" in hb["last_error"].lower()


def test_cross_batch_cycle_marks_apply_failed(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    # Build chain: lem:a -> lem:b -> lem:c (each refs the next via \ref).
    _publish(
        tmp_path, "add-node", "--label", "lem:a", "--kind", "lemma",
        "--statement", r"a depends on \ref{lem:b}", "--proof", "p",
        "--actor", "user:alice",
    )
    # lem:b before lem:a fails the order — we want lem:a's deps available.
    # Add reverse order: c, b, a.
    # Actually each must be added BEFORE its target ref exists. Use add
    # placeholder lemmas first, then revise in-order. Simplest: just add
    # lem:c then lem:b then lem:a.
    # ...except the first add already failed. Reset and redo.
    import shutil
    shutil.rmtree(tmp_path / "events")
    shutil.rmtree(tmp_path / "knowledge_base", ignore_errors=True)
    (tmp_path / "events").mkdir()
    (tmp_path / "knowledge_base").mkdir()
    (tmp_path / "knowledge_base" / "nodes").mkdir()

    _publish(
        tmp_path, "add-node", "--label", "lem:c", "--kind", "lemma",
        "--statement", "leaf", "--proof", "p",
        "--actor", "user:alice",
    )
    _publish(
        tmp_path, "add-node", "--label", "lem:b", "--kind", "lemma",
        "--statement", r"b uses \ref{lem:c}", "--proof", "p",
        "--actor", "user:alice",
    )
    _publish(
        tmp_path, "add-node", "--label", "lem:a", "--kind", "lemma",
        "--statement", r"a uses \ref{lem:b}", "--proof", "p",
        "--actor", "user:alice",
    )

    # Apply the chain via a librarian session.
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)

        # Now hand-drop a generator batch that revises lem:c to ref lem:a,
        # closing a -> b -> c -> a. Decoder cannot see this — only lem:c is
        # in the batch. Librarian apply must catch it via Kuzu.
        body = {
            "event_id": "20260424T020000.000-0001-aaaaaaaaaaaaaaaa",
            "type": "generator.batch_committed",
            "actor": "generator:test",
            "ts": "2026-04-24T02:00:00.000+00:00",
            "target": "lem:c",
            "payload": {
                "target": "lem:c",
                "mode": "fresh",
                "nodes": [
                    {
                        "label": "lem:c",
                        "kind": "lemma",
                        "statement": r"c now uses \ref{lem:a}",
                        "proof": "p",
                        "remark": "",
                        "source_note": "",
                    }
                ],
            },
        }
        path = _drop_canonical_event(
            tmp_path, body,
            iso_ms="20260424T020000.000",
            seq=1, uid="a" * 16,
        )
        lp.send({"cmd": "APPLY", "event_id": body["event_id"], "path": str(path)})
        reply = lp.recv(timeout=15.0)
        assert reply["reply"] == "APPLY_FAILED", reply
        assert reply["reason"] == "cycle", reply
        assert "lem:" in reply["detail"], reply
