"""Runtime job record (ARCHITECTURE §6.7.1 ``runtime/jobs/{job_id}.json``).

Schema mirrors §6.7.1 verbatim. Writers: coordinator at dispatch,
wrapper during execution, coordinator at terminal state. The file is
always rewritten atomically (``.tmp`` + rename) so concurrent dashboard
reads see a complete snapshot.

Status enumeration (Phase I):

- ``starting``    — coordinator wrote the file, wrapper has not started
- ``running``     — Codex subprocess is live, wrapper monitoring
- ``publishing``  — wrapper emitted the truth event, about to exit
- ``applied``     — coordinator mirrored AppliedEvent(applied)
- ``apply_failed`` — coordinator mirrored AppliedEvent(apply_failed)
- ``timed_out``   — coordinator killed the pgroup on log-mtime stale
- ``crashed``     — wrapper exited non-zero before ``publishing``
- ``orphaned``    — orphan reaper found dead pid, never reached publishing
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

JOB_SCHEMA: str = "rethlas-job-v1"


# Status enum kept as plain strings — JSON-serialisable + grep-friendly.
STATUS_STARTING = "starting"
STATUS_RUNNING = "running"
STATUS_PUBLISHING = "publishing"
STATUS_APPLIED = "applied"
STATUS_APPLY_FAILED = "apply_failed"
STATUS_TIMED_OUT = "timed_out"
STATUS_CRASHED = "crashed"
STATUS_ORPHANED = "orphaned"

ALL_STATUSES = frozenset(
    {
        STATUS_STARTING,
        STATUS_RUNNING,
        STATUS_PUBLISHING,
        STATUS_APPLIED,
        STATUS_APPLY_FAILED,
        STATUS_TIMED_OUT,
        STATUS_CRASHED,
        STATUS_ORPHANED,
    }
)

# After ``publishing`` only the coordinator updates the file. Wrappers
# must not move past ``publishing`` themselves.
TERMINAL_STATUSES = frozenset(
    {
        STATUS_APPLIED,
        STATUS_APPLY_FAILED,
        STATUS_TIMED_OUT,
        STATUS_CRASHED,
        STATUS_ORPHANED,
    }
)


def utc_now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass
class JobRecord:
    """In-flight job record (`runtime/jobs/{job_id}.json`)."""

    job_id: str
    kind: str  # "generator" | "verifier"
    target: str
    mode: str  # generator: "fresh" | "repair"; verifier: "single"
    dispatch_hash: str
    pid: int
    pgid: int
    started_at: str
    updated_at: str
    status: str
    log_path: str
    detail: str = ""
    reason: str = ""
    # Worker-supplied context the wrapper reads. Optional in early
    # milestones — coordinator fills these from Kuzu in M8.
    target_kind: str = ""  # NodeKind value (definition/lemma/theorem/...)
    statement: str = ""
    proof: str = ""
    dep_statement_hashes: dict[str, str] = field(default_factory=dict)
    verification_report: str = ""
    repair_hint: str = ""
    repair_count: int = 0
    h_rejected: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["schema"] = JOB_SCHEMA
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobRecord":
        # Drop the schema field if present — it is a constant.
        data = {k: v for k, v in data.items() if k != "schema"}
        return cls(**data)


def make_job_id(kind: str, *, iso_ms: str, uid: str) -> str:
    """Compose a job id of the form ``ver-{iso_ms}-{uid}`` / ``gen-{iso_ms}-{uid}``.

    The shape is stable across milestones so that ``log_path`` (which
    encodes ``job_id``) is predictable.
    """
    short = {"generator": "gen", "verifier": "ver"}.get(kind, kind)
    return f"{short}-{iso_ms}-{uid}"


def job_file_path(jobs_dir: Path | str, job_id: str) -> Path:
    return Path(jobs_dir) / f"{job_id}.json"


def log_path_for(logs_dir: Path | str, job_id: str) -> Path:
    return Path(logs_dir) / f"{job_id}.codex.log"


def write_job_file(path: Path | str, record: JobRecord) -> Path:
    """Atomically write ``record`` to ``path`` (.tmp + rename, no fsync).

    Job records are observability state, not truth (§6.7.1). We do not
    fsync the parent directory — coordinator regenerates jobs on
    restart from runtime state.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    body = json.dumps(record.to_dict(), sort_keys=True, ensure_ascii=False) + "\n"
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, p)
    return p


def read_job_file(path: Path | str) -> JobRecord | None:
    """Return :class:`JobRecord` or ``None`` if missing / unparseable."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return JobRecord.from_dict(data)
    except (TypeError, KeyError):
        return None


def update_job_file(
    path: Path | str,
    *,
    status: str | None = None,
    detail: str | None = None,
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> JobRecord | None:
    """Read-modify-write the job file. Returns the new record or ``None``
    if the file does not exist.

    The ``updated_at`` field is bumped on every successful update.
    Concurrent updates from coordinator and wrapper are serialised by
    the atomic rename — last-writer-wins, which matches §6.7.1 (only
    the wrapper writes ``running``/``publishing``; only coordinator
    writes the terminal status).
    """
    rec = read_job_file(path)
    if rec is None:
        return None
    if status is not None:
        rec.status = status
    if detail is not None:
        rec.detail = detail
    if reason is not None:
        rec.reason = reason
    if extra:
        for k, v in extra.items():
            if hasattr(rec, k):
                setattr(rec, k, v)
    rec.updated_at = utc_now_iso()
    write_job_file(path, rec)
    return rec


def delete_job_file(path: Path | str) -> None:
    """Best-effort delete; missing file is not an error."""
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return


def list_jobs(jobs_dir: Path | str) -> list[JobRecord]:
    """Return all readable job records in ``jobs_dir``.

    Files that fail to parse are silently skipped — coordinator's orphan
    reaper handles cleanup; this helper is for read-only scans.
    """
    out: list[JobRecord] = []
    d = Path(jobs_dir)
    if not d.is_dir():
        return out
    for entry in sorted(d.glob("*.json")):
        rec = read_job_file(entry)
        if rec is not None:
            out.append(rec)
    return out


__all__ = [
    "ALL_STATUSES",
    "JOB_SCHEMA",
    "JobRecord",
    "STATUS_APPLIED",
    "STATUS_APPLY_FAILED",
    "STATUS_CRASHED",
    "STATUS_ORPHANED",
    "STATUS_PUBLISHING",
    "STATUS_RUNNING",
    "STATUS_STARTING",
    "STATUS_TIMED_OUT",
    "TERMINAL_STATUSES",
    "delete_job_file",
    "job_file_path",
    "list_jobs",
    "log_path_for",
    "make_job_id",
    "read_job_file",
    "update_job_file",
    "utc_now_iso",
    "write_job_file",
]
