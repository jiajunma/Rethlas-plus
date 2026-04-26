"""M8 — coordinator pid/pgid patch must not clobber wrapper status.

The coordinator's :func:`coordinator.main._dispatch_job` writes the job
file twice:

1. Initial create with ``pid=0`` (wrapper needs the file to read its
   context).
2. Patch ``pid``/``pgid`` once :func:`spawn_wrapper` returns a Popen.

The wrapper, meanwhile, transitions ``status`` from ``starting`` to
``running`` as its first action. A naïve full-record write at step 2
would overwrite that wrapper-side update; the read-modify-write
``update_job_file(extra=…)`` form preserves it.
"""

from __future__ import annotations

from pathlib import Path

from common.runtime.jobs import (
    JobRecord,
    STATUS_RUNNING,
    STATUS_STARTING,
    job_file_path,
    read_job_file,
    update_job_file,
    write_job_file,
)


def _make_starting(jobs_dir: Path, job_id: str) -> Path:
    rec = JobRecord(
        job_id=job_id,
        kind="generator",
        target="thm:t",
        mode="fresh",
        dispatch_hash="h" * 64,
        pid=0,
        pgid=0,
        started_at="2026-04-26T00:00:00.000Z",
        updated_at="2026-04-26T00:00:00.000Z",
        status=STATUS_STARTING,
        log_path="runtime/logs/x.log",
    )
    return write_job_file(job_file_path(jobs_dir, job_id), rec)


def test_pid_patch_preserves_wrapper_running_status(tmp_path: Path) -> None:
    jobs_dir = tmp_path
    job_id = "gen-20260426T000000.000-abcdef0123456789"
    path = _make_starting(jobs_dir, job_id)

    # Wrapper races ahead and writes status=running before the
    # coordinator's pid patch.
    update_job_file(path, status=STATUS_RUNNING)

    # Coordinator's pid patch — read-modify-write must preserve
    # status=running and only update pid/pgid.
    update_job_file(path, extra={"pid": 4242, "pgid": 4242})

    rec = read_job_file(path)
    assert rec is not None
    assert rec.status == STATUS_RUNNING
    assert rec.pid == 4242
    assert rec.pgid == 4242


def test_pid_patch_when_wrapper_has_not_yet_run(tmp_path: Path) -> None:
    jobs_dir = tmp_path
    job_id = "gen-20260426T000001.000-abcdef0123456789"
    path = _make_starting(jobs_dir, job_id)

    update_job_file(path, extra={"pid": 7, "pgid": 7})

    rec = read_job_file(path)
    assert rec is not None
    # Wrapper hasn't written yet, so status remains STARTING.
    assert rec.status == STATUS_STARTING
    assert rec.pid == 7
    assert rec.pgid == 7
