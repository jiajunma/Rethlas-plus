"""M9 — `rethlas dashboard` CLI when supervise.lock is held.

Per ARCHITECTURE §6.7.1: standalone dashboard checks for the
supervise lock; if held, it prints the documented informational
message and exits 0.
"""

from __future__ import annotations

import errno
import fcntl
import os
import subprocess
import sys
from pathlib import Path

import pytest


PYTHON = sys.executable


def _init_ws(ws: Path) -> None:
    r = subprocess.run(
        [PYTHON, "-m", "cli.main", "--workspace", str(ws), "init"],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr


def test_dashboard_exits_when_supervise_lock_held(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    lock_path = tmp_path / "runtime" / "locks" / "supervise.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = subprocess.run(
            [PYTHON, "-m", "cli.main", "--workspace", str(tmp_path), "dashboard"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        assert r.returncode == 0, r.stderr
        assert "supervise is running" in r.stdout
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def test_coordinator_spawned_dashboard_bypasses_lock_check(tmp_path: Path) -> None:
    """PHASE1 M9: when coordinator spawns the dashboard child, the
    `RETHLAS_COORDINATOR_DASHBOARD_CHILD` env var instructs the CLI to
    skip the "supervise.lock held -> exit" early return so the
    supervisor is not stuck in a restart loop. Run the child briefly
    against a free port; expect it to be alive (i.e. NOT printing the
    informational `supervise is running` line and exiting 0)."""
    _init_ws(tmp_path)
    lock_path = tmp_path / "runtime" / "locks" / "supervise.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    proc = None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        env = os.environ.copy()
        env["RETHLAS_COORDINATOR_DASHBOARD_CHILD"] = "1"
        # Bind a known-free port (let OS pick by binding 0 manually first).
        import socket as _s
        with _s.socket(_s.AF_INET, _s.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        proc = subprocess.Popen(
            [
                PYTHON, "-m", "cli.main", "--workspace", str(tmp_path),
                "dashboard", "--bind", f"127.0.0.1:{port}",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        # Give the heartbeat publisher a moment to write.
        try:
            proc.wait(timeout=1.5)
            # If the process exited within 1.5s, that's the failure mode
            # we're guarding against (lock-held early-exit). Surface the
            # output so the failure is informative.
            assert False, (
                f"dashboard child exited prematurely: rc={proc.returncode} "
                f"stdout={proc.stdout.read() if proc.stdout else b''!r} "
                f"stderr={proc.stderr.read() if proc.stderr else b''!r}"
            )
        except subprocess.TimeoutExpired:
            # Good — the dashboard is still running.
            pass
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
