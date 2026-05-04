from __future__ import annotations

import os
from pathlib import Path

from common.runtime.jobs import STATUS_RUNNING, STATUS_STARTING, utc_now_iso
from common.runtime.jobs_v2 import (
    JOB_SCHEMA_V2,
    RoleJobRecord,
    list_role_jobs,
    read_role_job_file,
    update_role_job_file,
    write_role_job_file,
)


def test_role_job_v2_round_trip(tmp_path: Path) -> None:
    rec = RoleJobRecord(
        job_id="learn-20260504T010203.004-deadbeefdeadbeef",
        kind="learner",
        mode="learn_source_spans",
        target="src:toy#spans",
        context_hash="sha256:a",
        dispatch_hash="sha256:a",
        pid=os.getpid(),
        pgid=os.getpid(),
        started_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        status=STATUS_STARTING,
        log_path="runtime/logs/learn.codex.log",
        input={"source_id": "src:toy"},
    )
    path = tmp_path / "runtime" / "jobs" / f"{rec.job_id}.json"
    write_role_job_file(path, rec)
    raw = read_role_job_file(path)
    assert raw is not None
    assert raw.to_dict()["schema"] == JOB_SCHEMA_V2
    assert raw.input["source_id"] == "src:toy"

    update_role_job_file(path, status=STATUS_RUNNING, output_event_id="evt")
    updated = read_role_job_file(path)
    assert updated is not None
    assert updated.status == STATUS_RUNNING
    assert updated.output_event_id == "evt"
    assert [j.job_id for j in list_role_jobs(path.parent)] == [rec.job_id]
