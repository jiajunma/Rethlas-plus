"""``runtime/state/librarian.json`` heartbeat writer.

Schema mirrors ARCHITECTURE §6.5. The file is rewritten atomically via
tmp+rename so dashboard polls never see a half-written object.

The heartbeat is owned by :class:`librarian.daemon.LibrarianDaemon`; this
module exists so the structure is testable in isolation and so other
components (linter, tests) can read it without dragging in the daemon.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

LIBRARIAN_JSON_SCHEMA: Final[str] = "rethlas-librarian-v1"

# §6.5 startup phases.
PHASE_REPLAYING: Final[str] = "replaying"
PHASE_RECONCILING: Final[str] = "reconciling"
PHASE_READY: Final[str] = "ready"

# §6.5 status values.
STATUS_RUNNING: Final[str] = "running"
STATUS_IDLE: Final[str] = "idle"
STATUS_DEGRADED: Final[str] = "degraded"
STATUS_REBUILDING: Final[str] = "rebuilding"


def utc_now_iso() -> str:
    """UTC ISO 8601 with ``Z`` suffix (§2.4 trailer)."""
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass
class LibrarianHeartbeat:
    """Structured form of ``librarian.json`` (§6.5)."""

    pid: int
    started_at: str
    updated_at: str
    status: str = STATUS_RUNNING
    startup_phase: str = PHASE_REPLAYING
    last_seen_event_id: str = ""
    last_applied_event_id: str = ""
    events_applied_total: int = 0
    events_apply_failed_total: int = 0
    projection_backlog: int = 0
    rebuild_in_progress: bool = False
    last_rebuild_at: str | None = None
    last_error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["schema"] = LIBRARIAN_JSON_SCHEMA
        return d


def write_heartbeat(path: Path, hb: LibrarianHeartbeat) -> None:
    """Atomically rewrite ``path`` with the heartbeat payload.

    The rename is atomic; we do not fsync — this is observability state,
    not durable truth (§6.5 last paragraph).

    The temp file uses a **unique** name (pid + thread id). The librarian
    writes heartbeats from both a background pulse thread and the main loop;
    a shared ``.tmp`` name would let one writer's ``os.replace`` consume the
    temp another writer is about to rename, producing a spurious ``ENOENT``
    (seen once the projection backend became fast enough to fire several
    heartbeats back-to-back at startup).
    """
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(hb.to_dict(), sort_keys=True, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_heartbeat(path: Path) -> dict | None:
    """Return the parsed JSON or ``None`` if the file is missing / unreadable.

    Tolerant of any filesystem error (permission, broken symlink, EIO) so
    callers — coordinator dispatch loop, dashboard supervisor, dashboard
    HTTP layer — don't crash on transient read failures of this
    observability artefact.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


__all__ = [
    "LIBRARIAN_JSON_SCHEMA",
    "LibrarianHeartbeat",
    "PHASE_READY",
    "PHASE_RECONCILING",
    "PHASE_REPLAYING",
    "STATUS_DEGRADED",
    "STATUS_IDLE",
    "STATUS_REBUILDING",
    "STATUS_RUNNING",
    "read_heartbeat",
    "utc_now_iso",
    "write_heartbeat",
]
