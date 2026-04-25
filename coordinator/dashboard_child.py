"""Dashboard child supervision (PHASE1 M9 deliverable).

Coordinator spawns ``rethlas dashboard`` as a child process and monitors:

- subprocess liveness via ``Popen.poll()``
- dashboard heartbeat freshness via ``runtime/state/dashboard.json``

State machine (PHASE1 M9):

- ``starting`` — initial spawn or post-restart spawn; honors a 30 s
  startup grace window for the dashboard to publish its first
  heartbeat.
- ``running`` — heartbeat is fresh and process is alive.
- ``backoff`` — last spawn failed (no first heartbeat in grace OR
  process died OR heartbeat went stale); waits ``restart_backoff_s``
  before respawning.
- ``degraded`` — restarts exhausted; coordinator + librarian keep
  running, dashboard endpoint stays down until operator restarts.
- ``stopped`` — coordinator-driven graceful shutdown.

The state machine reads its own clock via ``clock`` and spawns its
own process via ``spawn``; both are injectable so tests can drive
the state machine deterministically without binding real ports.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional


DEFAULT_STARTUP_GRACE_S = 30.0
# Per ARCHITECTURE §6.7.1, dashboard heartbeats older than 5 minutes
# are labelled "down" — that is the boundary at which the supervisor
# treats the child as broken and triggers a restart. The 60s "degraded"
# threshold is a UI label only; restarting at 60s would thrash because
# the heartbeat publisher writes only every 30s.
DEFAULT_HEARTBEAT_STALE_S = 300.0
DEFAULT_RESTART_BACKOFF_S = 30.0
DEFAULT_MAX_RESTARTS = 3


STATUS_STARTING = "starting"
STATUS_RUNNING = "running"
STATUS_BACKOFF = "backoff"
STATUS_DEGRADED = "degraded"
STATUS_STOPPED = "stopped"


def _read_dashboard_updated_at(ws_root: Path) -> Optional[str]:
    path = ws_root / "runtime" / "state" / "dashboard.json"
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    ua = body.get("updated_at")
    return ua if isinstance(ua, str) else None


def _iso_to_epoch(s: str) -> Optional[float]:
    try:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt.timestamp()


def default_spawn_dashboard(workspace: Path, bind: str) -> subprocess.Popen:
    """Launch ``python -m cli.main --workspace <ws> dashboard --bind <bind>``."""
    env = os.environ.copy()
    # Tell the dashboard CLI it's the coordinator's child so it does not
    # bail out on the "supervise.lock held" early-exit path (the
    # coordinator parent holds that lock).
    env["RETHLAS_COORDINATOR_DASHBOARD_CHILD"] = "1"
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "cli.main",
            "--workspace",
            str(workspace),
            "dashboard",
            "--bind",
            bind,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


@dataclass
class DashboardSupervisor:
    ws_root: Path
    bind: str
    spawn: Callable[[Path, str], subprocess.Popen] = default_spawn_dashboard
    clock: Callable[[], float] = time.time
    startup_grace_s: float = DEFAULT_STARTUP_GRACE_S
    heartbeat_stale_s: float = DEFAULT_HEARTBEAT_STALE_S
    restart_backoff_s: float = DEFAULT_RESTART_BACKOFF_S
    max_restarts: int = DEFAULT_MAX_RESTARTS

    proc: Optional[subprocess.Popen] = None
    status: str = STATUS_STARTING
    restart_count: int = 0
    spawned_at: float = 0.0
    failed_at: float = 0.0

    def start(self) -> None:
        """Initial spawn (idempotent — does nothing if already alive)."""
        if self.status == STATUS_DEGRADED:
            return
        if self.proc is not None and self.proc.poll() is None:
            return
        try:
            self.proc = self.spawn(self.ws_root, self.bind)
        except Exception:
            # Spawn itself failed; treat as immediate failure.
            self.proc = None
            self.failed_at = self.clock()
            self.restart_count += 1
            self.status = (
                STATUS_DEGRADED
                if self.restart_count > self.max_restarts
                else STATUS_BACKOFF
            )
            return
        self.spawned_at = self.clock()
        self.status = STATUS_STARTING

    def tick(self) -> None:
        """One supervisor tick. Drives state transitions."""
        if self.status in (STATUS_DEGRADED, STATUS_STOPPED):
            return
        now = self.clock()

        if self.status == STATUS_BACKOFF:
            if (now - self.failed_at) >= self.restart_backoff_s:
                self.start()
            return

        proc_alive = self.proc is not None and self.proc.poll() is None
        ua = _read_dashboard_updated_at(self.ws_root)
        ua_epoch = _iso_to_epoch(ua) if ua else None
        hb_fresh = ua_epoch is not None and (now - ua_epoch) <= self.heartbeat_stale_s

        if self.status == STATUS_STARTING:
            if proc_alive and hb_fresh:
                self.status = STATUS_RUNNING
                self.restart_count = 0
                return
            in_grace = (now - self.spawned_at) <= self.startup_grace_s
            if not proc_alive or not in_grace:
                self._fail(now)
            return

        # STATUS_RUNNING
        if not proc_alive or not hb_fresh:
            self._fail(now)

    def _fail(self, now: float) -> None:
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            try:
                if self.proc.poll() is None:
                    self.proc.wait(timeout=2.0)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None
        self.failed_at = now
        self.restart_count += 1
        if self.restart_count > self.max_restarts:
            self.status = STATUS_DEGRADED
        else:
            self.status = STATUS_BACKOFF

    def shutdown(self, *, timeout: float = 5.0) -> None:
        if self.proc is not None:
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait()
            except Exception:
                pass
            self.proc = None
        self.status = STATUS_STOPPED

    def child_pid(self) -> int:
        if self.proc is not None and self.proc.poll() is None:
            return self.proc.pid
        return 0


__all__ = [
    "DashboardSupervisor",
    "default_spawn_dashboard",
    "STATUS_BACKOFF",
    "STATUS_DEGRADED",
    "STATUS_RUNNING",
    "STATUS_STARTING",
    "STATUS_STOPPED",
    "DEFAULT_STARTUP_GRACE_S",
    "DEFAULT_HEARTBEAT_STALE_S",
    "DEFAULT_RESTART_BACKOFF_S",
    "DEFAULT_MAX_RESTARTS",
]
