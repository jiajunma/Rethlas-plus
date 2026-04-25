"""M8 — coordinator.json heartbeat schema + writer."""

from __future__ import annotations

import json
import re
from pathlib import Path

from coordinator.heartbeat import (
    COORDINATOR_JSON_SCHEMA,
    CoordinatorHeartbeat,
    IDLE_LIBRARIAN_STARTING,
    STATUS_RUNNING,
    read_heartbeat,
    utc_now_iso,
    write_heartbeat,
)


def _hb(**overrides) -> CoordinatorHeartbeat:
    base = dict(
        pid=1234,
        started_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    base.update(overrides)
    return CoordinatorHeartbeat(**base)


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
