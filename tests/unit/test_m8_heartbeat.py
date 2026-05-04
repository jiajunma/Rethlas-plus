"""M8 — coordinator.json heartbeat schema + writer."""

from __future__ import annotations

import json
import re
from pathlib import Path

from common.runtime.reaper import OutcomeWindow
from coordinator.heartbeat import (
    COORDINATOR_JSON_SCHEMA,
    CoordinatorHeartbeat,
    STATUS_DEGRADED,
    IDLE_LIBRARIAN_STARTING,
    STATUS_RUNNING,
    read_heartbeat,
    utc_now_iso,
    write_heartbeat,
)
from coordinator.main import _write_heartbeat


def _hb(**overrides) -> CoordinatorHeartbeat:
    base = dict(
        pid=1234,
        started_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    base.update(overrides)
    return CoordinatorHeartbeat(**base)


def _state(tmp_path: Path):
    class _Lib:
        pid = 123

        def is_alive(self) -> bool:
            return True

    return type(
        "State",
        (),
        {
            "ws": type(
                "Ws",
                (),
                {
                    "runtime_jobs": tmp_path / "runtime" / "jobs",
                    "runtime_state": tmp_path / "runtime" / "state",
                },
            )(),
            "librarian": _Lib(),
            "dashboard": None,
            "started_at": utc_now_iso(),
            "loop_seq": 7,
            "config": type(
                "Config",
                (),
                {
                    "scheduling": type(
                        "Scheduling",
                        (),
                        {
                            "desired_pass_count": 3,
                            "codex_silent_timeout_seconds": 1800,
                        },
                    )()
                },
            )(),
            "outcome_window": OutcomeWindow(),
        },
    )()


def test_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "coordinator.json"
    write_heartbeat(p, _hb(loop_seq=42, status=STATUS_RUNNING))
    parsed = read_heartbeat(p)
    assert parsed is not None
    assert parsed["schema"] == COORDINATOR_JSON_SCHEMA
    assert parsed["loop_seq"] == 42


def test_z_suffix_timestamps(tmp_path: Path) -> None:
    p = tmp_path / "coordinator.json"
    write_heartbeat(p, _hb())
    body = json.loads(p.read_text(encoding="utf-8"))
    iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    assert iso_re.match(body["started_at"]), body["started_at"]
    assert iso_re.match(body["updated_at"]), body["updated_at"]


def test_idle_reason_detail_capped_at_512(tmp_path: Path) -> None:
    p = tmp_path / "coordinator.json"
    long_detail = "x" * 1000
    write_heartbeat(
        p,
        _hb(idle_reason_code=IDLE_LIBRARIAN_STARTING, idle_reason_detail=long_detail),
    )
    body = json.loads(p.read_text(encoding="utf-8"))
    assert len(body["idle_reason_detail"]) <= 512
    assert body["idle_reason_detail"].endswith("...")


def test_atomic_overwrite_no_tmp_left_behind(tmp_path: Path) -> None:
    p = tmp_path / "coordinator.json"
    write_heartbeat(p, _hb(loop_seq=1))
    write_heartbeat(p, _hb(loop_seq=2))
    assert not (tmp_path / "coordinator.json.tmp").exists()
    parsed = read_heartbeat(p)
    assert parsed["loop_seq"] == 2


def test_write_heartbeat_rejects_worker_status_words(tmp_path: Path) -> None:
    """A worker terminal state must not leak into coordinator.status."""

    state = _state(tmp_path)

    _write_heartbeat(state, status="crashed")

    parsed = read_heartbeat(tmp_path / "runtime" / "state" / "coordinator.json")
    assert parsed is not None
    assert parsed["status"] == STATUS_DEGRADED
    assert "invalid coordinator heartbeat status" in parsed["idle_reason_detail"]


def test_write_heartbeat_preserves_status_with_worker_outcomes(tmp_path: Path) -> None:
    """Worker outcome-window rows must not overwrite coordinator.status."""

    state = _state(tmp_path)
    state.outcome_window.record(
        target="thm:t",
        kind="generator",
        status="crashed",
        reason="",
    )

    _write_heartbeat(state, status=STATUS_RUNNING)

    parsed = read_heartbeat(tmp_path / "runtime" / "state" / "coordinator.json")
    assert parsed is not None
    assert parsed["status"] == STATUS_RUNNING
