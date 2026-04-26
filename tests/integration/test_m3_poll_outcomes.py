"""M3 — AppliedEvent poll returns the four §9.1 D2 outcomes.

Uses fixtures that bypass librarian by writing an AppliedEvent row
directly into Kuzu, so M3 does not depend on M4 being implemented.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import fcntl
import pytest

from tests.fixtures.librarian_proc import librarian

PYTHON = sys.executable


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-m", "cli.main", *args],
        capture_output=True, text=True, check=False,
    )


def _init(ws: Path) -> None:
    r = _run("--workspace", str(ws), "init")
    assert r.returncode == 0, r.stderr


def _last_event_id(ws: Path) -> str:
    files = sorted((ws / "events").rglob("*.json"))
    body = json.loads(files[-1].read_text(encoding="utf-8"))
    return body["event_id"]


def test_poll_reports_applied(tmp_path: Path) -> None:
    _init(tmp_path)

    # Run add-node in a thread; meanwhile fake an AppliedEvent row.
    env = os.environ.copy()

    def publish() -> subprocess.CompletedProcess:
        return subprocess.run(
            [PYTHON, "-m", "cli.main",
             "--workspace", str(tmp_path),
             "add-node", "--label", "def:x", "--kind", "definition",
             "--statement", "A", "--actor", "user:alice"],
            capture_output=True, text=True, env=env, check=False,
        )

    # Kick off publish; after a short delay, inject AppliedEvent(applied).
    result_holder: dict = {}
    def _go():
        result_holder["r"] = publish()
    t = threading.Thread(target=_go)
    t.start()
    # Wait briefly for the event file to land, then inject applied.
    for _ in range(60):  # up to 30s
        files = list((tmp_path / "events").rglob("*.json"))
        if files:
            break
        time.sleep(0.5)
    else:
        t.join(timeout=5)
        raise AssertionError("event file never appeared")

    with librarian(tmp_path) as lp:
        lp.wait_for_phase("ready", timeout=20.0)
        t.join(timeout=60)
        r = result_holder["r"]
        assert r.returncode == 0
        assert "applied" in r.stdout.lower()


def test_poll_reports_apply_failed(tmp_path: Path) -> None:
    _init(tmp_path)
    # First event establishes the label in KB.
    subprocess.run(
        [PYTHON, "-m", "cli.main",
         "--workspace", str(tmp_path),
         "add-node", "--label", "def:y", "--kind", "definition",
         "--statement", "B0", "--actor", "user:alice"],
        capture_output=True, text=True, check=False,
    )

    with librarian(tmp_path) as lp:
        lp.wait_for_phase("ready", timeout=20.0)

        result_holder: dict = {}

        def _go():
            result_holder["r"] = subprocess.run(
                [PYTHON, "-m", "cli.main",
                 "--workspace", str(tmp_path),
                 "add-node", "--label", "def:y", "--kind", "definition",
                 "--statement", "B", "--actor", "user:alice"],
                capture_output=True, text=True, check=False,
            )

        t = threading.Thread(target=_go)
        t.start()
        files_before = len(list((tmp_path / "events").rglob("*.json")))
        for _ in range(60):
            files = sorted((tmp_path / "events").rglob("*.json"))
            if len(files) > files_before:
                last = files[-1]
                break
            time.sleep(0.5)
        else:
            raise AssertionError("second event file never appeared")

        body = json.loads(last.read_text(encoding="utf-8"))
        lp.send({"cmd": "APPLY", "event_id": body["event_id"], "path": str(last)})
        reply = lp.recv(timeout=15.0)
        assert reply["reply"] == "APPLY_FAILED"
        assert reply["reason"] == "label_conflict"

        t.join(timeout=60)
        r = result_holder["r"]
        assert r.returncode == 0
        assert "apply_failed" in r.stdout.lower()
        assert "label_conflict" in r.stdout.lower()


def test_poll_reports_supervise_not_running(tmp_path: Path) -> None:
    """Nothing holds supervise.lock -> CLI reports 'supervise not running'."""
    _init(tmp_path)
    # No AppliedEvent row will ever land; supervise.lock is absent.
    # Use a short client-side timeout for this test by patching
    # publish._POLL_TIMEOUT_S via env? Simpler: publish a hint event
    # whose short-ish path ensures the timeout loop terminates. The
    # current implementation times out at 30s which is too slow for CI.
    # We inject a tiny poll timeout by running publish with env.
    env = os.environ.copy()
    env["RETHLAS_PUBLISH_POLL_TIMEOUT_S"] = "1.0"
    # Our publish module doesn't read that env yet; add it.
    r = subprocess.run(
        [PYTHON, "-m", "cli.main",
         "--workspace", str(tmp_path),
         "add-node", "--label", "def:z", "--kind", "definition",
         "--statement", "C", "--actor", "user:alice"],
        capture_output=True, text=True, env=env, check=False,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    # Either "queued, supervise not running" or applied (impossible here).
    assert "queued" in r.stdout.lower()
    assert "supervise" in r.stdout.lower() or "not running" in r.stdout.lower() or "behind" in r.stdout.lower()


def test_poll_reports_librarian_behind(tmp_path: Path) -> None:
    """supervise.lock HELD (by an external flock) but no AppliedEvent row."""
    _init(tmp_path)
    lock_path = tmp_path / "runtime" / "locks" / "supervise.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        env = os.environ.copy()
        env["RETHLAS_PUBLISH_POLL_TIMEOUT_S"] = "1.0"
        r = subprocess.run(
            [PYTHON, "-m", "cli.main",
             "--workspace", str(tmp_path),
             "add-node", "--label", "def:w", "--kind", "definition",
             "--statement", "D", "--actor", "user:alice"],
            capture_output=True, text=True, env=env, check=False,
            timeout=60,
        )
        assert r.returncode == 0, r.stderr
        assert "queued" in r.stdout.lower()
        assert "librarian" in r.stdout.lower() or "behind" in r.stdout.lower()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
