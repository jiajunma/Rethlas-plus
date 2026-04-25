"""M5 — job record schema + atomic writer."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from common.runtime.jobs import (
    JOB_SCHEMA,
    JobRecord,
    STATUS_RUNNING,
    STATUS_STARTING,
    delete_job_file,
    job_file_path,
    list_jobs,
    log_path_for,
    make_job_id,
    read_job_file,
    update_job_file,
    utc_now_iso,
    write_job_file,
)


def _make_record(**overrides) -> JobRecord:
    base = dict(
        job_id="ver-20260424T100420.111-a7b2c912d4f1e380",
        kind="verifier",
        target="lem:foo",
        mode="single",
        dispatch_hash="sha256:abc",
        pid=12345,
        pgid=12345,
        started_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        status=STATUS_STARTING,
        log_path="runtime/logs/ver-20260424T100420.111-a7b2c912d4f1e380.codex.log",
    )
    base.update(overrides)
    return JobRecord(**base)


def test_make_job_id_shape() -> None:
    jid = make_job_id("verifier", iso_ms="20260424T100420.111", uid="a" * 16)
    assert jid == "ver-20260424T100420.111-aaaaaaaaaaaaaaaa"
    jid2 = make_job_id("generator", iso_ms="20260424T100420.111", uid="b" * 16)
    assert jid2.startswith("gen-")


def test_log_path_and_job_file_path(tmp_path: Path) -> None:
    p = job_file_path(tmp_path / "jobs", "ver-x")
    assert p.name == "ver-x.json"
    lp = log_path_for(tmp_path / "logs", "ver-x")
    assert lp.name == "ver-x.codex.log"


def test_round_trip_write_read(tmp_path: Path) -> None:
    rec = _make_record()
    path = job_file_path(tmp_path / "jobs", rec.job_id)
    write_job_file(path, rec)
    parsed = read_job_file(path)
    assert parsed is not None
    assert parsed.job_id == rec.job_id
    assert parsed.status == rec.status
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema"] == JOB_SCHEMA


def test_timestamps_are_utc_z(tmp_path: Path) -> None:
    rec = _make_record()
    path = job_file_path(tmp_path / "jobs", rec.job_id)
    write_job_file(path, rec)
    body = json.loads(path.read_text(encoding="utf-8"))
    iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    assert iso_re.match(body["started_at"]), body["started_at"]
    assert iso_re.match(body["updated_at"]), body["updated_at"]


def test_update_bumps_updated_at_and_persists_status(tmp_path: Path) -> None:
    rec = _make_record(status=STATUS_STARTING)
    path = job_file_path(tmp_path / "jobs", rec.job_id)
    write_job_file(path, rec)
    first = read_job_file(path)
    assert first is not None
    new = update_job_file(path, status=STATUS_RUNNING)
    assert new is not None
    assert new.status == STATUS_RUNNING
    assert new.updated_at >= first.updated_at


def test_update_missing_file_returns_none(tmp_path: Path) -> None:
    assert update_job_file(tmp_path / "missing.json", status=STATUS_RUNNING) is None


def test_delete_job_file_idempotent(tmp_path: Path) -> None:
    rec = _make_record()
    path = job_file_path(tmp_path / "jobs", rec.job_id)
    write_job_file(path, rec)
    delete_job_file(path)
    delete_job_file(path)  # second call must not raise
    assert not path.exists()


def test_list_jobs_returns_sorted(tmp_path: Path) -> None:
    for jid in ["ver-c", "ver-a", "ver-b"]:
        rec = _make_record(job_id=jid)
        write_job_file(job_file_path(tmp_path / "jobs", jid), rec)
    jobs = list_jobs(tmp_path / "jobs")
    assert [j.job_id for j in jobs] == ["ver-a", "ver-b", "ver-c"]


def test_atomic_write_no_tmp_left_behind(tmp_path: Path) -> None:
    rec = _make_record()
    path = job_file_path(tmp_path / "jobs", rec.job_id)
    write_job_file(path, rec)
    assert path.exists()
    assert not path.with_suffix(".json.tmp").exists()


def test_read_malformed_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_job_file(p) is None
