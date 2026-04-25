"""Poll ``AppliedEvent`` for jobs that just exited (ARCHITECTURE §6.7.1 step 3).

When a wrapper exits with ``status == "publishing"``, coordinator on
its next tick reads the corresponding ``AppliedEvent`` row from Kuzu
and mirrors the outcome (``applied`` / ``apply_failed`` + reason +
detail) into the job file, then deletes the file.

Coordinator opens a short-lived read-only Kuzu connection — the
revised §4.1 model allows that as long as librarian's writer isn't
holding the lock at the same instant. We don't open a long-lived
connection because the coordinator process is the rebuild path's
critical-section gate too.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import kuzu

from common.runtime.jobs import (
    JobRecord,
    STATUS_APPLIED,
    STATUS_APPLY_FAILED,
    delete_job_file,
    job_file_path,
    list_jobs,
    update_job_file,
)


@dataclass(frozen=True, slots=True)
class AppliedRow:
    event_id: str
    status: str  # "applied" | "apply_failed"
    reason: str
    detail: str


def lookup_applied(db_path: Path, event_id: str) -> AppliedRow | None:
    """Open a short-lived read-only Kuzu connection and look up the row."""
    if not db_path.is_dir() and not db_path.exists():
        return None
    try:
        db = kuzu.Database(str(db_path), read_only=True)
        conn = kuzu.Connection(db)
    except Exception:
        return None
    try:
        res = conn.execute(
            "MATCH (a:AppliedEvent {event_id: $eid}) "
            "RETURN a.status, a.reason, a.detail",
            {"eid": event_id},
        )
        if not res.has_next():
            return None
        status, reason, detail = res.get_next()
        return AppliedRow(
            event_id=event_id,
            status=status,
            reason=reason or "",
            detail=detail or "",
        )
    finally:
        del conn
        del db


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    """Per-job terminal outcome for §7.4 / §7.5 consecutive-failure tracking."""

    job_id: str
    target: str
    kind: str  # "generator" | "verifier"
    status: str  # "applied" | "apply_failed"
    reason: str


def reconcile_publishing_jobs(
    jobs_dir: Path, db_path: Path
) -> list[ReconcileOutcome]:
    """Walk ``runtime/jobs/`` for ``status == publishing`` records.

    For each, check if the ``event_id`` it published has a matching
    ``AppliedEvent`` row. If yes, mirror the status into the job file
    and delete it. Returns the list of :class:`ReconcileOutcome`
    records for the caller's :class:`OutcomeWindow`.
    """
    resolved: list[ReconcileOutcome] = []
    for rec in list_jobs(jobs_dir):
        if rec.status != "publishing":
            continue
        # Detail field carries "event_id=..." per role.py's wrapper convention.
        event_id = _extract_event_id(rec.detail)
        if not event_id:
            continue
        row = lookup_applied(db_path, event_id)
        if row is None:
            continue
        path = job_file_path(jobs_dir, rec.job_id)
        if row.status == "applied":
            update_job_file(
                path,
                status=STATUS_APPLIED,
                detail=f"event_id={event_id}",
            )
        else:
            update_job_file(
                path,
                status=STATUS_APPLY_FAILED,
                reason=row.reason,
                detail=row.detail,
            )
        delete_job_file(path)
        resolved.append(
            ReconcileOutcome(
                job_id=rec.job_id,
                target=rec.target,
                kind=rec.kind,
                status=row.status,
                reason=row.reason,
            )
        )
    return resolved


def _extract_event_id(detail: str) -> str:
    """``event_id=...`` extraction (wrapper writes that on publishing)."""
    if not detail:
        return ""
    for token in detail.split():
        if token.startswith("event_id="):
            return token.split("=", 1)[1]
    return ""


__all__ = [
    "AppliedRow",
    "ReconcileOutcome",
    "lookup_applied",
    "reconcile_publishing_jobs",
]
