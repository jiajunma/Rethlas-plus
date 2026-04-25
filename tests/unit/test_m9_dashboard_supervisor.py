"""M9 — coordinator-side dashboard child supervisor state machine."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from coordinator.dashboard_child import (
    DashboardSupervisor,
    STATUS_BACKOFF,
    STATUS_DEGRADED,
    STATUS_RUNNING,
    STATUS_STARTING,
    STATUS_STOPPED,
)


class FakeProc:
    def __init__(self) -> None:
        self.alive = True
        self.terminated = False
        self.killed = False
        self.pid = 12345

    def poll(self):
        return None if self.alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False

    def kill(self) -> None:
        self.killed = True
        self.alive = False

    def wait(self, timeout=None) -> int:
        return 0


@dataclass
class _Harness:
    ws: Path
    now: float = 0.0
    spawned: list[FakeProc] = field(default_factory=list)
    fail_next_spawn: bool = False

    def clock(self) -> float:
        return self.now

    def spawn(self, ws_root: Path, bind: str) -> FakeProc:
        if self.fail_next_spawn:
            self.fail_next_spawn = False
            raise OSError("bind in use")
        proc = FakeProc()
        self.spawned.append(proc)
        return proc

    def write_heartbeat(self, *, age_s: float = 0.0) -> None:
        from datetime import datetime, timezone, timedelta

        ts = datetime.fromtimestamp(self.now - age_s, tz=timezone.utc)
        body = {
            "schema": "rethlas-dashboard-v1",
            "pid": 1,
            "started_at": ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3] + "Z",
            "updated_at": ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3] + "Z",
            "bind": "127.0.0.1:8765",
            "status": "running",
        }
        path = self.ws / "runtime" / "state" / "dashboard.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body), encoding="utf-8")

    def make(self) -> DashboardSupervisor:
        return DashboardSupervisor(
            ws_root=self.ws,
            bind="127.0.0.1:8765",
            spawn=self.spawn,
            clock=self.clock,
            startup_grace_s=10.0,
            heartbeat_stale_s=5.0,
            restart_backoff_s=2.0,
            max_restarts=3,
        )


def test_starts_and_transitions_to_running_on_first_heartbeat(tmp_path: Path) -> None:
    h = _Harness(ws=tmp_path)
    sup = h.make()
    sup.start()
    assert sup.status == STATUS_STARTING
    h.now = 1.0
    h.write_heartbeat(age_s=0.0)
    sup.tick()
    assert sup.status == STATUS_RUNNING
    assert len(h.spawned) == 1


def test_no_heartbeat_in_grace_triggers_backoff(tmp_path: Path) -> None:
    h = _Harness(ws=tmp_path)
    sup = h.make()
    sup.start()
    h.now = 11.0  # past 10s grace
    sup.tick()
    assert sup.status == STATUS_BACKOFF


def test_three_failed_starts_then_degraded(tmp_path: Path) -> None:
    h = _Harness(ws=tmp_path)
    sup = h.make()
    # Initial spawn → fail (no heartbeat in grace)
    sup.start()
    h.now = 11.0
    sup.tick()
    assert sup.status == STATUS_BACKOFF
    # First restart
    h.now = 13.5
    sup.tick()  # past 2s backoff -> respawn
    assert sup.status == STATUS_STARTING
    h.now = 24.0
    sup.tick()
    assert sup.status == STATUS_BACKOFF
    # Second restart
    h.now = 27.0
    sup.tick()
    assert sup.status == STATUS_STARTING
    h.now = 38.0
    sup.tick()
    assert sup.status == STATUS_BACKOFF
    # Third restart
    h.now = 41.0
    sup.tick()
    assert sup.status == STATUS_STARTING
    h.now = 52.0
    sup.tick()
    # Fourth failure → restart_count > max_restarts(3) → degraded
    assert sup.status == STATUS_DEGRADED


def test_backoff_waits_full_window_before_respawn(tmp_path: Path) -> None:
    h = _Harness(ws=tmp_path)
    sup = h.make()
    sup.start()
    h.now = 11.0
    sup.tick()
    assert sup.status == STATUS_BACKOFF
    spawn_count = len(h.spawned)
    h.now = 12.5  # < 2s after failed_at(11.0)
    sup.tick()
    assert sup.status == STATUS_BACKOFF
    assert len(h.spawned) == spawn_count
    h.now = 13.0
    sup.tick()
    assert sup.status == STATUS_STARTING
    assert len(h.spawned) == spawn_count + 1


def test_running_to_backoff_when_heartbeat_goes_stale(tmp_path: Path) -> None:
    h = _Harness(ws=tmp_path)
    sup = h.make()
    sup.start()
    h.now = 1.0
    h.write_heartbeat(age_s=0.0)
    sup.tick()
    assert sup.status == STATUS_RUNNING
    # No more heartbeats -> stale after 5s
    h.now = 8.0
    sup.tick()
    assert sup.status == STATUS_BACKOFF


def test_running_to_backoff_when_process_dies(tmp_path: Path) -> None:
    h = _Harness(ws=tmp_path)
    sup = h.make()
    sup.start()
    h.now = 1.0
    h.write_heartbeat(age_s=0.0)
    sup.tick()
    assert sup.status == STATUS_RUNNING
    # Kill the process from outside
    h.spawned[-1].alive = False
    h.now = 2.0
    h.write_heartbeat(age_s=0.0)
    sup.tick()
    assert sup.status == STATUS_BACKOFF


def test_recovery_resets_restart_counter(tmp_path: Path) -> None:
    h = _Harness(ws=tmp_path)
    sup = h.make()
    sup.start()
    h.now = 11.0
    sup.tick()
    assert sup.status == STATUS_BACKOFF
    assert sup.restart_count == 1
    # Recover on next start
    h.now = 13.5
    sup.tick()  # respawn
    h.now = 14.0
    h.write_heartbeat(age_s=0.0)
    sup.tick()
    assert sup.status == STATUS_RUNNING
    assert sup.restart_count == 0


def test_spawn_failure_counts_as_a_failure(tmp_path: Path) -> None:
    h = _Harness(ws=tmp_path)
    sup = h.make()
    h.fail_next_spawn = True
    sup.start()
    assert sup.status == STATUS_BACKOFF
    assert sup.restart_count == 1


def test_shutdown_terminates_and_marks_stopped(tmp_path: Path) -> None:
    h = _Harness(ws=tmp_path)
    sup = h.make()
    sup.start()
    sup.shutdown()
    assert sup.status == STATUS_STOPPED
    assert h.spawned[0].terminated


def test_degraded_state_does_not_respawn(tmp_path: Path) -> None:
    h = _Harness(ws=tmp_path)
    sup = h.make()
    sup.status = STATUS_DEGRADED  # forced
    sup.start()
    sup.tick()
    assert sup.status == STATUS_DEGRADED
    assert h.spawned == []


def test_child_pid_zero_when_no_process(tmp_path: Path) -> None:
    h = _Harness(ws=tmp_path)
    sup = h.make()
    assert sup.child_pid() == 0
    sup.start()
    assert sup.child_pid() == h.spawned[0].pid
    h.spawned[0].alive = False
    assert sup.child_pid() == 0
