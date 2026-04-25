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
