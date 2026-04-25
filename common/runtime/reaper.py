"""Orphan reaper + sliding-window outcome bookkeeping (ARCHITECTURE §6.7.1, §7.5).

The reaper:

- Scans ``runtime/jobs/*.json`` on each tick.
- Marks dead processes whose ``updated_at`` is older than the orphan
  threshold AND whose status never reached ``publishing`` as
  ``orphaned``, then deletes the file (§6.7.1 step 6).

The outcome window:

- Coordinator keeps a per-target deque of recent terminal outcomes.
- After **3 consecutive** ``crashed`` (§7.5) or ``timed_out`` (§7.4)
  outcomes on the same target+kind, dashboard surfaces the target.
- Repeated same-reason ``apply_failed`` is also tracked because
  scheduling priority depends on it (§10.2).

The window itself is in-memory; we expose helpers so the coordinator
can drive it deterministically and tests can assert behaviour.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from common.runtime.codex_runner import time_scale
from common.runtime.jobs import (
    JobRecord,
    STATUS_CRASHED,
    STATUS_ORPHANED,
    STATUS_PUBLISHING,
    STATUS_RUNNING,
    STATUS_STARTING,
    STATUS_TIMED_OUT,
    delete_job_file,
    job_file_path,
    list_jobs,
    update_job_file,
    utc_now_iso,
)


# §6.7.1 step 6: "older than 5 minutes". Honours RETHLAS_TEST_TIME_SCALE
# via the runner module so tests run quickly.
_ORPHAN_AGE_S = 300.0


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we cannot signal it; treat as alive.
        return True
    return True


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        # Allow both "Z" and "+00:00" trailers.
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _age_s(updated_at: str, *, now: datetime | None = None) -> float | None:
    parsed = _parse_iso(updated_at)
    if parsed is None:
        return None
    if now is None:
        now = datetime.now(tz=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).total_seconds()


@dataclass(frozen=True, slots=True)
class ReaperReport:
    """Result of a single reaper tick."""

    orphaned: tuple[str, ...] = ()
    skipped_alive: tuple[str, ...] = ()


def reap_orphans(
    jobs_dir: Path | str,
    *,
    orphan_age_s: float = _ORPHAN_AGE_S,
    is_alive: callable = _is_pid_alive,
    now: datetime | None = None,
) -> ReaperReport:
    """Walk ``jobs_dir``; mark + delete orphaned job files.

    A job is an orphan when:
    - its ``pid`` is no longer alive AND
    - its ``updated_at`` is older than ``orphan_age_s`` (scaled by
      :func:`time_scale`) AND
    - its ``status`` never progressed past ``starting`` / ``running``.
    """
    threshold = orphan_age_s * time_scale()
    orphaned: list[str] = []
    skipped_alive: list[str] = []
    for rec in list_jobs(jobs_dir):
        if rec.status not in {STATUS_STARTING, STATUS_RUNNING}:
            continue
        if is_alive(rec.pid):
            skipped_alive.append(rec.job_id)
            continue
        age = _age_s(rec.updated_at, now=now)
        if age is None or age < threshold:
            continue
        # Mark and delete (§6.7.1 step 6).
        update_job_file(
            job_file_path(jobs_dir, rec.job_id),
            status=STATUS_ORPHANED,
            detail="reaper: pid not alive past orphan threshold",
        )
        delete_job_file(job_file_path(jobs_dir, rec.job_id))
        orphaned.append(rec.job_id)
    return ReaperReport(
        orphaned=tuple(orphaned),
        skipped_alive=tuple(skipped_alive),
    )


# ---------------------------------------------------------------------------
# Sliding-window outcome bookkeeping (§7.5 F5).
# ---------------------------------------------------------------------------
@dataclass
class OutcomeWindow:
    """Per-(target, kind) sliding window of recent terminal outcomes.

    The window is bounded — once full it discards the oldest entry. The
    helpers ask only for "consecutive same outcome at the head", which
    is sufficient for §7.5 (3 consecutive crashes) and §7.4 (3
    consecutive timeouts) and for §10.2 priority decisions.
    """

    capacity: int = 16
    _buf: dict[tuple[str, str], deque[tuple[str, str]]] = field(default_factory=dict)

    def record(self, *, target: str, kind: str, status: str, reason: str = "") -> None:
        key = (target, kind)
        dq = self._buf.setdefault(key, deque(maxlen=self.capacity))
        dq.append((status, reason or ""))

    def consecutive_status(self, *, target: str, kind: str, status: str) -> int:
        """Number of consecutive ``status`` outcomes at the head of the window."""
        dq = self._buf.get((target, kind))
        if not dq:
            return 0
        count = 0
        for s, _ in reversed(dq):
            if s == status:
                count += 1
            else:
                break
        return count

    def consecutive_apply_failed_reason(
        self, *, target: str, kind: str, reason: str
    ) -> int:
        """Consecutive ``apply_failed`` outcomes with the same ``reason``."""
        dq = self._buf.get((target, kind))
        if not dq:
            return 0
        count = 0
        for s, r in reversed(dq):
            if s == "apply_failed" and r == reason:
                count += 1
            else:
                break
        return count


__all__ = [
    "OutcomeWindow",
    "ReaperReport",
    "reap_orphans",
]
