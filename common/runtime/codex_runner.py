"""Spawn and supervise a Codex subprocess (ARCHITECTURE §7.4).

The runner:

1. Opens ``runtime/logs/{job_id}.codex.log`` for append.
2. Spawns the codex command with stdout + stderr both redirected to
   that log fd (single merged log; §7.4 paragraph 2).
3. Starts a watchdog thread that polls the log file's mtime every
   ``poll_interval_s`` and kills the process group via SIGINT then
   SIGKILL when staleness exceeds ``silent_timeout_s`` (§7.4 rule).
4. Waits for the subprocess and returns a :class:`RunOutcome`.

The runner is **synchronous** — callers (generator/verifier wrappers)
block on ``run()``. Heartbeat updates and event publication happen in
the wrapper's main thread between Codex calls.

To keep tests fast, ``RETHLAS_TEST_TIME_SCALE`` (default ``1.0``)
shrinks every wall-clock duration the runner reasons about. The fake
codex script honours the same scale on its own ``delay_s`` /
``silent_seconds``. Production keeps ``1.0``; M5 / M6 tests use ``0.01``
or smaller.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


def time_scale() -> float:
    """Wall-clock multiplier read once per call (tests patch the env)."""
    raw = os.environ.get("RETHLAS_TEST_TIME_SCALE", "1.0")
    try:
        v = float(raw)
    except ValueError:
        return 1.0
    if v <= 0:
        return 1.0
    return v


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Result of a single Codex invocation."""

    exit_code: int
    timed_out: bool
    duration_s: float
    log_path: Path

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def _scaled(seconds: float) -> float:
    return seconds * time_scale()


def run_codex(
    *,
    argv: Sequence[str],
    log_path: Path | str,
    silent_timeout_s: float,
    poll_interval_s: float = 1.0,
    env: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
) -> RunOutcome:
    """Spawn ``argv``, redirect both streams to ``log_path``, kill on stale mtime.

    Parameters
    ----------
    argv:
        Full command line. Caller is responsible for picking the
        correct binary (real ``codex`` or
        ``tests/fixtures/fake_codex.py``).
    log_path:
        Where to write the merged stdout/stderr. Created in append mode
        so a wrapper can correlate restart attempts.
    silent_timeout_s:
        Max seconds without a log mtime change before the runner kills
        the process group (§7.4 rule). Scaled by
        ``RETHLAS_TEST_TIME_SCALE``.
    poll_interval_s:
        Seconds between mtime polls. Same scaling.
    env:
        Environment for the child. ``None`` means inherit from current
        process (§6.7.1 step 1 "full environment inheritance").
    cwd:
        Working directory for the child.
    """
    log = Path(log_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.touch(exist_ok=True)
    log_fd = os.open(str(log), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)

    started = time.monotonic()
    timeout_real = max(0.05, _scaled(silent_timeout_s))
    poll_real = max(0.01, _scaled(poll_interval_s))

    try:
        proc = subprocess.Popen(
            list(argv),
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=dict(env) if env is not None else None,
            cwd=str(cwd) if cwd is not None else None,
            start_new_session=True,  # gives the child its own pgid
        )
    finally:
        os.close(log_fd)

    timed_out_flag = threading.Event()

    def watchdog() -> None:
        last_mtime = log.stat().st_mtime if log.exists() else 0.0
        last_seen_change = time.monotonic()
        while True:
            if proc.poll() is not None:
                return
            time.sleep(poll_real)
            try:
                cur = log.stat().st_mtime
            except FileNotFoundError:
                continue
            if cur > last_mtime:
                last_mtime = cur
                last_seen_change = time.monotonic()
                continue
            if time.monotonic() - last_seen_change >= timeout_real:
                # Mark BEFORE killing — main thread observes proc exit as
                # soon as SIGINT lands and may build the RunOutcome before
                # ``_kill_pgroup`` returns from its grace-period sleep.
                timed_out_flag.set()
                _kill_pgroup(proc)
                return

    t = threading.Thread(target=watchdog, daemon=True)
    t.start()
    proc.wait()
    duration = time.monotonic() - started
    # Give watchdog a moment to finish (it exits on poll).
    t.join(timeout=poll_real * 2)

    return RunOutcome(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        timed_out=timed_out_flag.is_set(),
        duration_s=duration,
        log_path=log,
    )


def _kill_pgroup(proc: subprocess.Popen) -> None:
    """Send SIGINT, wait briefly, then SIGKILL the child's pgid (§7.4)."""
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError):
        return
    try:
        os.killpg(pgid, signal.SIGINT)
    except (ProcessLookupError, PermissionError):
        return
    grace = max(0.05, _scaled(10.0))
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(min(0.1, grace / 5))
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return


__all__ = [
    "RunOutcome",
    "run_codex",
    "time_scale",
]
