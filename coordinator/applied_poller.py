"""Poll librarian QUERY(applied_event_status) for jobs that just exited.

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

from dataclasses import dataclass
from pathlib import Path

from cli.workspace import workspace_paths
from coordinator.kb_client import applied_event_status
from common.runtime.jobs import (
    STATUS_APPLIED,
    STATUS_APPLY_FAILED,
    delete_job_file,
    job_file_path,
    list_jobs,
    update_job_file,
)


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    """Per-job terminal outcome for §7.4 / §7.5 consecutive-failure tracking."""

    job_id: str
    target: str
    kind: str  # "generator" | "verifier"
    status: str  # "applied" | "apply_failed"
    reason: str


def reconcile_publishing_jobs(
    jobs_dir: Path, ws_root: Path
) -> list[ReconcileOutcome]:
    """Walk ``runtime/jobs/`` for ``status == publishing`` records.

    For each, check if the ``event_id`` it published has a matching
    ``AppliedEvent`` row. If yes, mirror the status into the job file
    and delete it. Returns the list of :class:`ReconcileOutcome`
    records for the caller's :class:`OutcomeWindow`.
    """
    resolved: list[ReconcileOutcome] = []
    ws = workspace_paths(str(ws_root))
    for rec in list_jobs(jobs_dir):
        if rec.status != "publishing":
            continue
        # Detail field carries "event_id=..." per role.py's wrapper convention.
        event_id = _extract_event_id(rec.detail)
        if not event_id:
            continue
        row = applied_event_status(ws, event_id)
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
    "ReconcileOutcome",
    "reconcile_publishing_jobs",
]
