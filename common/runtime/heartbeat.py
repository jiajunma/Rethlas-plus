"""Wrapper heartbeat refresher (ARCHITECTURE §7.4 F4).

While Codex is running, the wrapper refreshes
``runtime/jobs/{job_id}.json`` ``updated_at`` every 60 seconds even if
``status`` has not changed. This separates "Codex is reasoning quietly"
from "wrapper itself froze".

A :class:`JobHeartbeat` is a context manager that spawns a daemon
thread bumping the file. The thread terminates when either the manager
exits or :meth:`stop` is called.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from common.runtime.codex_runner import time_scale
from common.runtime.jobs import update_job_file


_DEFAULT_INTERVAL_S = 60.0


@dataclass
class JobHeartbeat:
    """Refreshes ``runtime/jobs/{job_id}.json`` on a fixed cadence."""

    job_file: Path
    interval_s: float = _DEFAULT_INTERVAL_S

    def __post_init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "JobHeartbeat":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _run(self) -> None:
        scaled = max(0.01, self.interval_s * time_scale())
        while not self._stop.wait(scaled):
            update_job_file(self.job_file)


__all__ = ["JobHeartbeat"]
