"""M9 — file-polling state watcher emits the six SSE envelope kinds.

Covers ARCHITECTURE §6.7.1 typed envelope contract:

    {"type": <kind>, "ts": <utc-iso-Z>, "payload": ...}

Six envelope kinds in scope: ``truth_event``, ``applied_event``,
``job_change``, ``coordinator_tick``, ``librarian_tick``, ``alert``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from common.runtime.jobs import JobRecord, write_job_file
from coordinator.heartbeat import (
    CoordinatorHeartbeat,
    utc_now_iso,
    write_heartbeat as write_coordinator_hb,
)
from dashboard.server import SseBroker
from dashboard.state_watcher import StateWatcher
from librarian.heartbeat import (
    LibrarianHeartbeat,
    write_heartbeat as write_librarian_hb,
)


PYTHON = sys.executable
_ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


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


def _envelope_keys_ok(env: dict) -> None:
    assert set(env.keys()) >= {"type", "ts", "payload"}
    assert _ISO_Z_RE.match(env["ts"]), env["ts"]


def test_envelope_schema_all_six_kinds(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    broker = SseBroker()
    watcher = StateWatcher(tmp_path, broker, poll_interval_s=10.0)
    watcher.tick(prime=True)  # baseline; nothing should be published

    state_dir = tmp_path / "runtime" / "state"

    # 1. truth_event — write an event file.
    _publish(
        tmp_path, "add-node", "--label", "def:x", "--kind", "definition",
        "--statement", "Define X.", "--actor", "user:alice",
    )

    # 2. job_change — drop a runtime/jobs/*.json.
    rec = JobRecord(
        job_id="gen-20260424T120000.000-cccccccccccccccc",
        kind="generator", target="lem:y", mode="fresh",
        dispatch_hash="ef" * 32,
        pid=1, pgid=1,
        started_at="2026-04-24T12:00:00.000Z",
        updated_at="2026-04-24T12:00:01.000Z",
        status="running",
        log_path=str(tmp_path / "runtime" / "logs" / "x.codex.log"),
    )
    write_job_file(tmp_path / "runtime" / "jobs" / f"{rec.job_id}.json", rec)

    # 3. coordinator_tick.
    chb = CoordinatorHeartbeat(
        pid=1, started_at=utc_now_iso(), updated_at=utc_now_iso(),
    )
    write_coordinator_hb(state_dir / "coordinator.json", chb)

    # 4. librarian_tick.
    lhb = LibrarianHeartbeat(
        pid=2, started_at=utc_now_iso(), updated_at=utc_now_iso(),
    )
    write_librarian_hb(state_dir / "librarian.json", lhb)

    # 5. alert — append a line to rejected_writes.jsonl.
    rej = state_dir / "rejected_writes.jsonl"
    rej.write_text(
        json.dumps({"reason": "schema_invalid", "actor": "x"}) + "\n",
        encoding="utf-8",
    )

    envelopes = watcher.tick()
    types = sorted({e["type"] for e in envelopes})
    assert "truth_event" in types
    assert "job_change" in types
    assert "coordinator_tick" in types
    assert "librarian_tick" in types
    assert "alert" in types
    for env in envelopes:
        _envelope_keys_ok(env)

    # 6. applied_event — emitted by external caller hook.
    received: list[dict] = []
    sub = broker.subscribe()
    watcher.emit_applied_event(event_id="abc123", status="applied")
    received.append(sub.get(timeout=2.0))
    assert received[0]["type"] == "applied_event"
    _envelope_keys_ok(received[0])
    assert received[0]["payload"]["event_id"] == "abc123"


def test_applied_event_envelope_polls_kuzu(tmp_path: Path) -> None:
    """``applied_event`` envelope fires after librarian commits, without
    any external hook (regression for the bug where the watcher only
    accepted manual ``emit_applied_event`` calls).
    """
    _init_ws(tmp_path)
    broker = SseBroker()
    watcher = StateWatcher(tmp_path, broker, poll_interval_s=10.0)
    watcher.tick(prime=True)  # baseline; no events

    # Drive a real librarian to commit one user.node_added event.
    _publish(
        tmp_path, "add-node", "--label", "def:x", "--kind", "definition",
        "--statement", "Define X.", "--actor", "user:alice",
    )
    from tests.fixtures.librarian_proc import librarian as _librarian
    from librarian.heartbeat import PHASE_READY
    with _librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)

    envs = watcher.tick()
    applied = [e for e in envs if e["type"] == "applied_event"]
    assert applied, [e["type"] for e in envs]
    assert applied[0]["payload"]["status"] == "applied"


def test_truncated_jsonl_resets_offset(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    broker = SseBroker()
    watcher = StateWatcher(tmp_path, broker, poll_interval_s=10.0)
    state_dir = tmp_path / "runtime" / "state"
    rej = state_dir / "rejected_writes.jsonl"
    rej.write_text(json.dumps({"reason": "a"}) + "\n", encoding="utf-8")
    watcher.tick(prime=True)
    rej.write_text("", encoding="utf-8")  # truncate
    watcher.tick()  # offset reset
    rej.write_text(json.dumps({"reason": "b"}) + "\n", encoding="utf-8")
    envs = watcher.tick()
    assert any(
        e["type"] == "alert" and e["payload"]["body"]["reason"] == "b"
        for e in envs
    )


def test_job_terminated_envelope_on_delete(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    broker = SseBroker()
    watcher = StateWatcher(tmp_path, broker, poll_interval_s=10.0)
    rec = JobRecord(
        job_id="ver-x-y", kind="verifier", target="thm:z", mode="single",
        dispatch_hash="aa" * 32, pid=1, pgid=1,
        started_at="2026-04-24T12:00:00.000Z",
        updated_at="2026-04-24T12:00:01.000Z",
        status="running",
        log_path=str(tmp_path / "runtime" / "logs" / "x.codex.log"),
    )
    job_path = tmp_path / "runtime" / "jobs" / f"{rec.job_id}.json"
    write_job_file(job_path, rec)
    watcher.tick(prime=True)
    job_path.unlink()
    envs = watcher.tick()
    assert any(
        e["type"] == "job_change" and e["payload"].get("status") == "terminated"
        for e in envs
    )
