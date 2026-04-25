"""M5 — orphan reaper detects dead-pid + stale jobs."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from common.runtime.jobs import (
    JobRecord,
    STATUS_PUBLISHING,
    STATUS_RUNNING,
    job_file_path,
    write_job_file,
)
from common.runtime.reaper import reap_orphans


def _record(
    *, job_id: str, pid: int, status: str = STATUS_RUNNING, age_s: float = 0.0
) -> JobRecord:
    when = datetime.now(tz=timezone.utc) - timedelta(seconds=age_s)
    iso = when.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return JobRecord(
        job_id=job_id,
        kind="verifier",
        target="lem:foo",
        mode="single",
        dispatch_hash="sha256:abc",
        pid=pid,
        pgid=pid,
        started_at=iso,
        updated_at=iso,
        status=status,
        log_path=f"runtime/logs/{job_id}.codex.log",
    )


def test_reaper_marks_dead_pid_old_record(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    rec = _record(job_id="ver-a", pid=1, age_s=10.0)  # pid 1 always alive
    rec_dead = _record(job_id="ver-b", pid=999_999_999, age_s=10.0)
    write_job_file(job_file_path(jobs_dir, rec.job_id), rec)
    write_job_file(job_file_path(jobs_dir, rec_dead.job_id), rec_dead)

    report = reap_orphans(jobs_dir, orphan_age_s=5.0, is_alive=lambda pid: pid != 999_999_999)
    assert "ver-b" in report.orphaned
    assert "ver-a" in report.skipped_alive
    assert not job_file_path(jobs_dir, "ver-b").exists()
    assert job_file_path(jobs_dir, "ver-a").exists()


def test_reaper_skips_recent_dead_pid(tmp_path: Path) -> None:
    """If updated_at is within the orphan window, leave the job alone."""
    jobs_dir = tmp_path / "jobs"
    rec = _record(job_id="ver-recent", pid=999_999_999, age_s=0.0)
    write_job_file(job_file_path(jobs_dir, rec.job_id), rec)
    report = reap_orphans(jobs_dir, orphan_age_s=60.0, is_alive=lambda pid: False)
    assert "ver-recent" not in report.orphaned
    assert job_file_path(jobs_dir, "ver-recent").exists()


def test_reaper_does_not_touch_publishing(tmp_path: Path) -> None:
    """Once the wrapper has flipped to ``publishing``, coordinator owns the
    file lifecycle — reaper must not interfere."""
    jobs_dir = tmp_path / "jobs"
    rec = _record(
        job_id="ver-pub", pid=999_999_999, status=STATUS_PUBLISHING, age_s=900.0
    )
    write_job_file(job_file_path(jobs_dir, rec.job_id), rec)
    report = reap_orphans(jobs_dir, orphan_age_s=60.0, is_alive=lambda pid: False)
    assert "ver-pub" not in report.orphaned
    assert job_file_path(jobs_dir, "ver-pub").exists()


def test_reaper_empty_dir_returns_empty_report(tmp_path: Path) -> None:
    report = reap_orphans(tmp_path / "jobs")
    assert report.orphaned == ()
    assert report.skipped_alive == ()
