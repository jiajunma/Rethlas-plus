"""M10 — linter category coverage on real workspaces.

Most tests seed a workspace, drive librarian to ``ready`` so Kuzu has
state, then plant a specific class of drift and assert the linter
detects it.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from cli.workspace import workspace_paths
from common.kb.markdown_backend import MarkdownBackend
from librarian.heartbeat import PHASE_READY
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


def _seed_def_and_theorem(ws: Path) -> None:
    _publish(
        ws, "add-node", "--label", "def:x", "--kind", "definition",
        "--statement", "Define X.", "--actor", "user:alice",
    )
    _publish(
        ws, "add-node", "--label", "thm:t", "--kind", "theorem",
        "--statement", r"T about \ref{def:x}.", "--proof", "p.",
        "--actor", "user:alice",
    )


def _drive_to_ready(ws: Path) -> None:
    with librarian(ws) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)


def _read_report(ws: Path) -> dict:
    p = ws / "runtime" / "state" / "linter_report.json"
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Clean workspace.
# ---------------------------------------------------------------------------
def test_clean_workspace_passes(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_def_and_theorem(tmp_path)
    _drive_to_ready(tmp_path)
    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report = _read_report(tmp_path)
    assert rc == 0, report
    for cat in ("a", "b", "c", "d", "e", "f"):
        assert report[cat]["count"] == 0, (cat, report[cat])
    assert "0 violations" in report["summary"]


# ---------------------------------------------------------------------------
# Category A.
# ---------------------------------------------------------------------------
def test_category_a_envelope_invalid(tmp_path: Path) -> None:
    """§3.4 envelope-level validation surfaces unknown type / bad actor."""
    _init(tmp_path)
    _seed_def_and_theorem(tmp_path)
    files = sorted((tmp_path / "events").rglob("*.json"))
    target = files[0]
    body = json.loads(target.read_text(encoding="utf-8"))
    body["type"] = "rogue.event_type"  # not in §3.4 allowlist
    target.write_text(json.dumps(body), encoding="utf-8")

    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report = _read_report(tmp_path)
    codes = [v["code"] for v in report["a"]["violations"]]
    assert "A_envelope_invalid" in codes
    assert rc == 5


def test_category_a_filename_body_event_id_mismatch(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_def_and_theorem(tmp_path)
    # Tamper one event body so its event_id no longer matches the filename.
    files = sorted((tmp_path / "events").rglob("*.json"))
    target = files[0]
    body = json.loads(target.read_text(encoding="utf-8"))
    body["event_id"] = "20990101T000000.000-9999-deadbeefdeadbeef"
    target.write_text(json.dumps(body), encoding="utf-8")

    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    assert rc == 5
    report = _read_report(tmp_path)
    codes = [v["code"] for v in report["a"]["violations"]]
    assert "A_event_id_mismatch" in codes


def test_category_a_duplicate_event_id(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_def_and_theorem(tmp_path)
    files = sorted((tmp_path / "events").rglob("*.json"))
    src = files[0]
    dup = src.parent / ("dup--" + src.name)
    dup.write_bytes(src.read_bytes())

    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report = _read_report(tmp_path)
    # Duplicate has a malformed name; both violations may surface but the
    # duplicate-id one is the contract.
    codes = [v["code"] for v in report["a"]["violations"]]
    assert any(c in {"A_event_id_duplicate", "A_filename_invalid"} for c in codes)
    assert rc == 5


# ---------------------------------------------------------------------------
# Category B.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Category C / D — drift on stored counts.
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# Category E — nodes/ rendering.
# ---------------------------------------------------------------------------
def _bump_def_to_pass_one(ws: Path) -> None:
    backend = MarkdownBackend(str(ws / "knowledge_base" / "nodes"))
    try:
        backend._conn.execute(
            "MATCH (n:Node {label: 'def:x'}) SET n.pass_count = 1"
        )
    finally:
        backend.close()


    # B/C/D drift may persist (we bumped pass_count manually) — but E is clean.
    # rc2 may still be 5 because of C drift; that's expected and not an E concern.


# ---------------------------------------------------------------------------
# Category F.
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Aggregation + concurrency lock.
# ---------------------------------------------------------------------------


def test_refuses_when_supervise_lock_held(tmp_path: Path) -> None:
    _init(tmp_path)
    lock_path = tmp_path / "runtime" / "locks" / "supervise.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
        assert rc == 2
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def test_allow_concurrent_overrides_lock(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_def_and_theorem(tmp_path)
    _drive_to_ready(tmp_path)

    lock_path = tmp_path / "runtime" / "locks" / "supervise.lock"
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc = run_linter_on_workspace(
            workspace_paths(str(tmp_path)), allow_concurrent=True
        )
        # No drift planted, so 0; the report carries a "note".
        assert rc == 0
        report = _read_report(tmp_path)
        assert "note" in report
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ---------------------------------------------------------------------------
# Category E — on-disk nodes/*.md vs the canonical render of the events replay
# (§13: the only durable projection is markdown; E is the sole content audit).
# ---------------------------------------------------------------------------
def test_category_e_content_and_orphan_drift(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_def_and_theorem(tmp_path)
    _drive_to_ready(tmp_path)

    nodes_dir = tmp_path / "knowledge_base" / "nodes"
    files = sorted(nodes_dir.glob("*.md"))
    assert files, "librarian should have rendered node markdown"

    # 1. content drift: tamper a rendered node file.
    tampered = files[0]
    tampered.write_text(tampered.read_text() + "\n# tampered\n", encoding="utf-8")
    # 2. orphan: a markdown file with no corresponding replayed node.
    (nodes_dir / "def_orphan.md").write_text(
        "---\nlabel: def:orphan\nkind: definition\npass_count: 1\n"
        "statement_hash: x\nverification_hash: y\ndepends_on: []\n---\n\n"
        "**Statement.**\n\nGhost.\n",
        encoding="utf-8",
    )

    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report = _read_report(tmp_path)
    codes = [v["code"] for v in report["e"]["violations"]]
    assert "E_content_drift" in codes
    assert "E_orphan_file" in codes
    assert rc == 5

    # --repair-nodes heals both; a follow-up run is clean.
    run_linter_on_workspace(workspace_paths(str(tmp_path)), repair_nodes=True)
    rc2 = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report2 = _read_report(tmp_path)
    assert report2["e"]["count"] == 0
    assert rc2 == 0
