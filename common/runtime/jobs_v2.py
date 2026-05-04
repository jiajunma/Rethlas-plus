"""Role-oriented runtime job records for Phase 3 workers.

Generator/verifier keep the Phase I node-targeted ``rethlas-job-v1`` shape.
Learner/referee jobs need source/review context packets, so they start on a
small v2 envelope with role-specific data under ``input``.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from common.runtime.jobs import (
    ALL_STATUSES,
    STATUS_APPLIED,
    STATUS_APPLY_FAILED,
    STATUS_CRASHED,
    STATUS_ORPHANED,
    STATUS_PUBLISHING,
    STATUS_RUNNING,
    STATUS_STARTING,
    STATUS_TIMED_OUT,
    TERMINAL_STATUSES,
    utc_now_iso,
)


JOB_SCHEMA_V2 = "rethlas-job-v2"

ROLE_JOB_KINDS = frozenset(
    {
        "learner",
        "referee",
        "source_ingest",
        "reference_retrieval",
    }
)


@dataclass
class RoleJobRecord:
    """In-flight Phase 3 job record (`runtime/jobs/{job_id}.json`)."""

    job_id: str
    kind: str
    mode: str
    target: str
    context_hash: str
    dispatch_hash: str
    pid: int
    pgid: int
    started_at: str
    updated_at: str
    status: str
    log_path: str
    input: dict[str, Any] = field(default_factory=dict)
    output_event_id: str = ""
    detail: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["schema"] = JOB_SCHEMA_V2
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoleJobRecord":
        if data.get("schema") != JOB_SCHEMA_V2:
            raise ValueError(f"not a {JOB_SCHEMA_V2} record")
        raw = {k: v for k, v in data.items() if k != "schema"}
        return cls(**raw)


def make_role_job_id(kind: str, *, iso_ms: str, uid: str) -> str:
    short = {
        "learner": "learn",
        "referee": "ref",
        "source_ingest": "src",
        "reference_retrieval": "retr",
    }.get(kind, kind)
    return f"{short}-{iso_ms}-{uid}"


def write_role_job_file(path: Path | str, record: RoleJobRecord) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    body = json.dumps(record.to_dict(), sort_keys=True, ensure_ascii=False) + "\n"
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, p)
    return p


def read_role_job_file(path: Path | str) -> RoleJobRecord | None:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("schema") != JOB_SCHEMA_V2:
        return None
    try:
        return RoleJobRecord.from_dict(data)
    except (TypeError, KeyError, ValueError):
        return None


def update_role_job_file(
    path: Path | str,
    *,
    status: str | None = None,
    detail: str | None = None,
    reason: str | None = None,
    output_event_id: str | None = None,
) -> RoleJobRecord | None:
    rec = read_role_job_file(path)
    if rec is None:
        return None
    if status is not None:
        rec.status = status
    if detail is not None:
        rec.detail = detail
    if reason is not None:
        rec.reason = reason
    if output_event_id is not None:
        rec.output_event_id = output_event_id
    rec.updated_at = utc_now_iso()
    write_role_job_file(path, rec)
    return rec


def list_role_jobs(jobs_dir: Path | str) -> list[RoleJobRecord]:
    out: list[RoleJobRecord] = []
    d = Path(jobs_dir)
    if not d.is_dir():
        return out
    for entry in sorted(d.glob("*.json")):
        rec = read_role_job_file(entry)
        if rec is not None:
            out.append(rec)
    return out


__all__ = [
    "ALL_STATUSES",
    "JOB_SCHEMA_V2",
    "ROLE_JOB_KINDS",
    "RoleJobRecord",
    "STATUS_APPLIED",
    "STATUS_APPLY_FAILED",
    "STATUS_CRASHED",
    "STATUS_ORPHANED",
    "STATUS_PUBLISHING",
    "STATUS_RUNNING",
    "STATUS_STARTING",
    "STATUS_TIMED_OUT",
    "TERMINAL_STATUSES",
    "list_role_jobs",
    "make_role_job_id",
    "read_role_job_file",
    "update_role_job_file",
    "write_role_job_file",
]
