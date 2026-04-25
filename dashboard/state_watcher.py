"""File-polling SSE emitter (ARCHITECTURE §6.7.1).

Phase I uses a polling watcher rather than ``watchdog`` to avoid a
third-party dep. Cadence is 1 s; tests can drop it via the
``poll_interval_s`` constructor arg.

The watcher emits typed envelopes through a :class:`SseBroker`:

```json
{"type": "<kind>", "ts": "<utc-iso-Z>", "payload": {...}}
```

Six envelope kinds are emitted (per §6.7.1):

- ``truth_event``       new file under ``events/**``
- ``applied_event``     polled by an external caller (librarian heartbeat)
- ``job_change``        creation / update / deletion in ``runtime/jobs/``
- ``coordinator_tick``  ``runtime/state/coordinator.json`` changed
- ``librarian_tick``    ``runtime/state/librarian.json`` changed
- ``alert``             new line appended to ``rejected_writes.jsonl`` or
                        ``drift_alerts.jsonl``

The watcher tracks per-file mtime + size + ``.jsonl`` byte offsets so a
notification only carries newly observed bytes.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dashboard.server import SseBroker


log = logging.getLogger("rethlas.dashboard.watcher")


def _utc_now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def envelope(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": kind, "ts": _utc_now_iso(), "payload": payload}


@dataclass
class _FileState:
    mtime_ns: int = 0
    size: int = 0


class StateWatcher:
    """Poll workspace files and publish SSE envelopes through a broker."""

    def __init__(
        self,
        ws_root: Path,
        broker: SseBroker,
        *,
        poll_interval_s: float = 1.0,
    ) -> None:
        self.ws_root = Path(ws_root)
        self.broker = broker
        self.poll_interval_s = poll_interval_s

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # Per-file tracking.
        self._events_seen: set[Path] = set()
        self._jobs_state: dict[str, _FileState] = {}  # job_id -> state
        self._coordinator_state = _FileState()
        self._librarian_state = _FileState()
        self._rejected_offset = 0
        self._drift_offset = 0
        self._primed = False

    # --- lifecycle ---
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="dashboard-watcher", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    # --- main loop ---
    def _run(self) -> None:  # pragma: no cover - thread loop
        # Prime first so the snapshot used as "baseline" emits no envelopes.
        try:
            self.tick(prime=True)
        except Exception as exc:
            log.warning("dashboard watcher prime failed: %s", exc)
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                log.warning("dashboard watcher tick failed: %s", exc)
            self._stop.wait(self.poll_interval_s)

    def tick(self, *, prime: bool = False) -> list[dict[str, Any]]:
        """Run one polling tick. Returns the list of envelopes published.

        Tests call this directly without spawning the thread.
        """
        envelopes: list[dict[str, Any]] = []
        envelopes.extend(self._scan_events(prime=prime))
        envelopes.extend(self._scan_jobs(prime=prime))
        envelopes.extend(self._scan_coordinator(prime=prime))
        envelopes.extend(self._scan_librarian(prime=prime))
        envelopes.extend(self._scan_alerts(prime=prime))
        if not prime:
            for env in envelopes:
                self.broker.publish(env)
        self._primed = True
        return envelopes

    # --- per-source scans ---
    def _scan_events(self, *, prime: bool) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        events_dir = self.ws_root / "events"
        if not events_dir.is_dir():
            return out
        for shard in events_dir.iterdir():
            if not shard.is_dir():
                continue
            for f in shard.glob("*.json"):
                if f in self._events_seen:
                    continue
                self._events_seen.add(f)
                if prime:
                    continue
                try:
                    body = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    # Filename body unreadable: fall back to a structured
                    # event_id parsed from the filename (iso_ms-seq-uid).
                    try:
                        from common.events.filenames import parse_filename
                        parsed = parse_filename(f.name)
                        body = {
                            "event_id": (
                                f"{parsed.iso_ms}-{parsed.seq:04d}-{parsed.uid}"
                            )
                        }
                    except Exception:
                        body = {"event_id": f.stem}
                out.append(
                    envelope(
                        "truth_event",
                        {
                            "filename": f.name,
                            "shard": shard.name,
                            "body": body,
                        },
                    )
                )
        return out

    def _scan_jobs(self, *, prime: bool) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        jobs_dir = self.ws_root / "runtime" / "jobs"
        if not jobs_dir.is_dir():
            seen_ids = list(self._jobs_state.keys())
            for job_id in seen_ids:
                # Files vanished but watcher thinks they exist.
                if not (jobs_dir / f"{job_id}.json").exists():
                    if not prime:
                        out.append(
                            envelope(
                                "job_change",
                                {"job_id": job_id, "status": "terminated"},
                            )
                        )
                    self._jobs_state.pop(job_id, None)
            return out

        live_ids: set[str] = set()
        for f in jobs_dir.glob("*.json"):
            job_id = f.stem
            live_ids.add(job_id)
            try:
                stat = f.stat()
            except FileNotFoundError:
                continue
            prev = self._jobs_state.get(job_id, _FileState(0, 0))
            if stat.st_mtime_ns == prev.mtime_ns and stat.st_size == prev.size:
                continue
            self._jobs_state[job_id] = _FileState(stat.st_mtime_ns, stat.st_size)
            if prime:
                continue
            try:
                body = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                body = {"job_id": job_id}
            out.append(envelope("job_change", body))

        for job_id in list(self._jobs_state):
            if job_id in live_ids:
                continue
            if not prime:
                out.append(
                    envelope(
                        "job_change",
                        {"job_id": job_id, "status": "terminated"},
                    )
                )
            self._jobs_state.pop(job_id, None)
        return out

    def _scan_state_file(
        self,
        path: Path,
        cache: _FileState,
        kind: str,
        *,
        prime: bool,
    ) -> tuple[list[dict[str, Any]], _FileState]:
        out: list[dict[str, Any]] = []
        try:
            stat = path.stat()
        except FileNotFoundError:
            return out, cache
        if stat.st_mtime_ns == cache.mtime_ns and stat.st_size == cache.size:
            return out, cache
        new_state = _FileState(stat.st_mtime_ns, stat.st_size)
        if prime:
            return out, new_state
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            body = {}
        out.append(envelope(kind, body if isinstance(body, dict) else {"raw": body}))
        return out, new_state

    def _scan_coordinator(self, *, prime: bool) -> list[dict[str, Any]]:
        path = self.ws_root / "runtime" / "state" / "coordinator.json"
        out, self._coordinator_state = self._scan_state_file(
            path, self._coordinator_state, "coordinator_tick", prime=prime
        )
        return out

    def _scan_librarian(self, *, prime: bool) -> list[dict[str, Any]]:
        path = self.ws_root / "runtime" / "state" / "librarian.json"
        out, self._librarian_state = self._scan_state_file(
            path, self._librarian_state, "librarian_tick", prime=prime
        )
        return out

    def _scan_alerts(self, *, prime: bool) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        state_dir = self.ws_root / "runtime" / "state"
        for filename, attr in (
            ("rejected_writes.jsonl", "_rejected_offset"),
            ("drift_alerts.jsonl", "_drift_offset"),
        ):
            path = state_dir / filename
            offset: int = getattr(self, attr)
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                continue
            if size <= offset:
                if size < offset:
                    # File got truncated (rebuild). Reset.
                    offset = 0
                    setattr(self, attr, 0)
                continue
            try:
                with path.open("rb") as fh:
                    fh.seek(offset)
                    chunk = fh.read()
            except OSError:
                continue
            setattr(self, attr, size)
            if prime:
                continue
            for line in chunk.decode("utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    body = json.loads(line)
                except json.JSONDecodeError:
                    body = {"raw": line}
                out.append(
                    envelope(
                        "alert",
                        {"source": filename, "body": body},
                    )
                )
        return out

    # --- AppliedEvent helper ---
    def emit_applied_event(self, *, event_id: str, status: str, reason: str = "") -> None:
        """External caller (coordinator's applied poller) invokes this.

        Dashboard cannot tail Kuzu directly, so the coordinator-side
        applied-poller (or any external observer) calls this hook.
        Tests use it to force the applied_event envelope.
        """
        self.broker.publish(
            envelope(
                "applied_event",
                {"event_id": event_id, "status": status, "reason": reason},
            )
        )


__all__ = ["StateWatcher", "envelope"]
