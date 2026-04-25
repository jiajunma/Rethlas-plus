"""M5 — wrapper heartbeat refreshes ``runtime/jobs/{job_id}.json``."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

from common.runtime.heartbeat import JobHeartbeat
from common.runtime.jobs import (
    JobRecord,
    STATUS_RUNNING,
    job_file_path,
    read_job_file,
    utc_now_iso,
    write_job_file,
)


pytestmark = pytest.mark.timeout(30)


_ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _seed_record(tmp_path: Path) -> Path:
    rec = JobRecord(
        job_id="ver-x",
        kind="verifier",
        target="lem:foo",
        mode="single",
        dispatch_hash="sha256:abc",
        pid=os.getpid(),
        pgid=os.getpid(),
        started_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        status=STATUS_RUNNING,
        log_path="runtime/logs/ver-x.codex.log",
    )
    path = job_file_path(tmp_path / "jobs", rec.job_id)
    write_job_file(path, rec)
    return path


def test_heartbeat_refreshes_updated_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETHLAS_TEST_TIME_SCALE", "0.01")  # 60s -> 0.6s
    path = _seed_record(tmp_path)
    initial = read_job_file(path)
    assert initial is not None

    with JobHeartbeat(path, interval_s=60.0):
        time.sleep(2.0)  # plenty for several beats at 0.01x

    after = read_job_file(path)
    assert after is not None
    assert after.updated_at > initial.updated_at


def test_heartbeat_writes_z_suffixed_timestamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETHLAS_TEST_TIME_SCALE", "0.01")
    path = _seed_record(tmp_path)
    with JobHeartbeat(path, interval_s=60.0):
        time.sleep(0.6)
    rec = read_job_file(path)
    assert rec is not None
    assert _ISO_Z_RE.match(rec.updated_at), rec.updated_at


def test_heartbeat_stops_when_context_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETHLAS_TEST_TIME_SCALE", "0.01")
    path = _seed_record(tmp_path)
    with JobHeartbeat(path, interval_s=60.0):
        time.sleep(0.6)
    snapshot = read_job_file(path).updated_at  # type: ignore[union-attr]
    time.sleep(1.0)
    after = read_job_file(path)
    assert after is not None
    assert after.updated_at == snapshot, "heartbeat thread kept writing past stop()"
