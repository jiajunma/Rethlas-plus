"""``runtime/state/dashboard.json`` heartbeat (ARCHITECTURE §6.7.1).

Even in standalone mode the dashboard publishes its own liveness so:

- a future supervise-managed dashboard can be monitored uniformly with
  the librarian heartbeat (§6.4.2 children dict);
- the linter / external observers can detect a stale dashboard in the
  same staleness vocabulary (healthy / degraded / down) the rest of
  Phase I uses.

Schema mirrors §6.7.1; rewritten atomically with ``.tmp`` + rename.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

DASHBOARD_JSON_SCHEMA: Final[str] = "rethlas-dashboard-v1"


def _utc_now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass
class DashboardHeartbeat:
    pid: int
    started_at: str
    updated_at: str
    bind: str
    status: str = "running"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["schema"] = DASHBOARD_JSON_SCHEMA
        return d


def write_heartbeat(path: Path, hb: DashboardHeartbeat) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    body = json.dumps(hb.to_dict(), sort_keys=True, ensure_ascii=False) + "\n"
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)


def read_heartbeat(path: Path) -> dict | None:
    """Return the parsed heartbeat or ``None`` on any filesystem / parse error.

    Tolerant of permission errors, EIO, and broken symlinks: heartbeats
    are observability state, so transient read failures should not crash
    the dashboard supervisor or HTTP layer.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


class HeartbeatPublisher:
    """Background thread that rewrites ``dashboard.json`` periodically."""

    def __init__(
        self,
        ws_root: Path,
        bind: str,
        *,
        interval_s: float = 30.0,
    ) -> None:
        self.path = ws_root / "runtime" / "state" / "dashboard.json"
        self.bind = bind
        self.interval_s = interval_s
        self.started_at = _utc_now_iso()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        # Write immediately so dashboard.json exists before the first
        # tick — dashboard polling tools don't see "down" while we're
        # only sleeping.
        self._write("running")
        self._thread = threading.Thread(
            target=self._run, name="dashboard-heartbeat", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        # Final stopping write (best-effort).
        try:
            self._write("stopping")
        except Exception:
            pass

    def _run(self) -> None:  # pragma: no cover - thread loop
        while not self._stop.is_set():
            self._write("running")
            self._stop.wait(self.interval_s)

    def _write(self, status: str) -> None:
        hb = DashboardHeartbeat(
            pid=os.getpid(),
            started_at=self.started_at,
            updated_at=_utc_now_iso(),
            bind=self.bind,
            status=status,
        )
        write_heartbeat(self.path, hb)


__all__ = [
    "DASHBOARD_JSON_SCHEMA",
    "DashboardHeartbeat",
    "HeartbeatPublisher",
    "read_heartbeat",
    "write_heartbeat",
]
