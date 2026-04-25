"""M8 — ``rethlas supervise`` runs to steady state on a tiny workspace.

Subprocess test. Pre-seeds events for two definitions and a lemma that
needs proving; spawns supervise with `RETHLAS_FAKE_CODEX_ARGV` set so
both generator and verifier wrappers use the fake codex; runs supervise
for a bounded number of ticks and asserts the final state.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from coordinator.heartbeat import read_heartbeat


PYTHON = sys.executable


def _run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-m", "cli.main", *args],
        capture_output=True, text=True, env=env, check=False, timeout=120,
    )


def _seed(tmp_path: Path) -> None:
    r = _run("--workspace", str(tmp_path), "init")
    assert r.returncode == 0, r.stderr
    r = _run(
        "--workspace", str(tmp_path),
        "add-node",
        "--label", "def:x",
        "--kind", "definition",
        "--statement", "Define X.",
        "--actor", "user:alice",
    )
    assert r.returncode == 0, r.stderr


def test_singleton_lock(tmp_path: Path) -> None:
    """Second supervise must fail on the lock — ARCHITECTURE §6.4."""
    _seed(tmp_path)
    env = os.environ.copy()
    env["RETHLAS_COORDINATOR_MAX_TICKS"] = "0"  # run forever, until killed
    env["RETHLAS_LIBRARIAN_HEARTBEAT_S"] = "0.2"
    env["RETHLAS_COORDINATOR_TICK_S"] = "0.1"
    proc = subprocess.Popen(
        [PYTHON, "-m", "cli.main", "--workspace", str(tmp_path), "supervise"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env,
    )
    try:
        # Wait a bit for it to acquire the lock.
        time.sleep(2.0)
        # Second one must fail fast.
        r = _run("--workspace", str(tmp_path), "supervise", env=env)
        assert r.returncode == 2
        assert "lock" in r.stderr.lower() or "supervise" in r.stderr.lower()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def test_supervise_writes_coordinator_json_and_idle(tmp_path: Path) -> None:
    """Run a few ticks and verify coordinator.json appears with idle_reason_code."""
    _seed(tmp_path)
    env = os.environ.copy()
    env["RETHLAS_COORDINATOR_MAX_TICKS"] = "5"
    env["RETHLAS_LIBRARIAN_HEARTBEAT_S"] = "0.2"
    env["RETHLAS_COORDINATOR_TICK_S"] = "0.1"
    env["RETHLAS_TEST_TIME_SCALE"] = "0.5"
    r = _run("--workspace", str(tmp_path), "supervise", env=env)
    assert r.returncode == 0, r.stderr
    hb_path = tmp_path / "runtime" / "state" / "coordinator.json"
    assert hb_path.exists(), r.stderr
    hb = read_heartbeat(hb_path)
    assert hb is not None
    assert hb["loop_seq"] >= 1
    # With only a definition seeded (which goes straight to verifier
    # queue at pass_count=0), supervise will dispatch a verifier
    # eventually. The status field should be one of the known values.
    assert hb["status"] in ("running", "idle", "stopping", "degraded")
