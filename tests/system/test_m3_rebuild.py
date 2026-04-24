"""M3 — `rethlas rebuild` system tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON = sys.executable


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-m", "cli.main", *args],
        capture_output=True, text=True, check=False,
    )


def _init_with_events(tmp: Path) -> None:
    r = _run("--workspace", str(tmp), "init")
    assert r.returncode == 0, r.stderr
    _run(
        "--workspace", str(tmp),
        "add-node",
        "--label", "def:a",
        "--kind", "definition",
        "--statement", "A",
        "--actor", "user:alice",
    )
    _run(
        "--workspace", str(tmp),
        "add-node",
        "--label", "lem:b",
        "--kind", "lemma",
        "--statement", r"uses \ref{def:a}",
        "--proof", "proof",
        "--actor", "user:alice",
    )


def _events_snapshot(events_root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(events_root)): p.read_bytes()
        for p in events_root.rglob("*.json")
    }


def test_rebuild_takes_lock_and_produces_projection(tmp_path: Path) -> None:
    _init_with_events(tmp_path)
    r = _run("--workspace", str(tmp_path), "rebuild")
    assert r.returncode == 0, r.stderr
    assert "rebuild complete" in r.stdout
    # dag.kz exists under knowledge_base/
    assert (tmp_path / "knowledge_base" / "dag.kz").exists()
    # rebuild flag cleared on clean exit
    assert not (tmp_path / "runtime/state/rebuild_in_progress.flag").exists()


def test_rebuild_never_touches_events(tmp_path: Path) -> None:
    _init_with_events(tmp_path)
    before = _events_snapshot(tmp_path / "events")
    r = _run("--workspace", str(tmp_path), "rebuild")
    assert r.returncode == 0, r.stderr
    after = _events_snapshot(tmp_path / "events")
    assert before == after, "rebuild mutated events/ — truth must be append-only"


def test_rebuild_refuses_while_supervise_lock_held(tmp_path: Path) -> None:
    """An external flock holder simulates a running supervise."""
    import fcntl

    _init_with_events(tmp_path)
    lock_path = tmp_path / "runtime" / "locks" / "supervise.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        r = _run("--workspace", str(tmp_path), "rebuild")
        assert r.returncode != 0
        assert "supervise" in r.stderr.lower()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_rebuild_wipes_knowledge_base_projection(tmp_path: Path) -> None:
    _init_with_events(tmp_path)
    r1 = _run("--workspace", str(tmp_path), "rebuild")
    assert r1.returncode == 0
    # Drop a spurious file under knowledge_base/ — rebuild should wipe it.
    ghost = tmp_path / "knowledge_base" / "ghost.txt"
    ghost.write_text("stale", encoding="utf-8")
    r2 = _run("--workspace", str(tmp_path), "rebuild")
    assert r2.returncode == 0
    assert not ghost.exists(), "rebuild must wipe stale files under knowledge_base/"
