"""``events/`` watchdog (ARCHITECTURE §6.5 / §6.7.1).

Coordinator owns the watchdog; librarian is passive. Each new file
under ``events/{date}/`` is reported back so coordinator can:

1. send an ``APPLY(event_id, path)`` command to librarian;
2. mirror librarian's reply into ``coordinator.json`` and dashboard
   SSE state.

We use a polling scanner instead of pulling in ``watchdog``:
the events tree is small and only grows; polling once per loop tick
is well within latency budget.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from common.events.filenames import parse_filename


@dataclass(frozen=True, slots=True)
class EventFile:
    path: Path
    iso_ms: str
    seq: int
    uid: str
    event_id: str

    @property
    def sort_key(self) -> tuple[str, int, str]:
        return (self.iso_ms, self.seq, self.uid)


class WatcherCorruption(RuntimeError):
    """Canonical event file is malformed and must halt dispatch."""

    def __init__(self, path: Path, detail: str) -> None:
        super().__init__(detail)
        self.path = path
        self.detail = detail


class EventsWatcher:
    """Stateful scanner that reports unseen event files in causal order."""

    def __init__(self, events_root: Path) -> None:
        self.root = events_root
        self._seen: set[Path] = set()

    def ack(self, path: Path) -> None:
        """Mark ``path`` as successfully handed off to librarian.

        The watcher intentionally does NOT mark files seen during
        :meth:`poll`; doing so would lose events if coordinator sees a
        file but crashes or times out before librarian replies. Callers
        acknowledge only after the APPLY request completed with a reply.
        """
        self._seen.add(path)

    def poll(self) -> list[EventFile]:
        """Return new event files in ``(iso_ms, seq, uid)`` order."""
        if not self.root.is_dir():
            return []
        new: list[EventFile] = []
        for p in sorted(self.root.rglob("*.json")):
            if not p.is_file():
                continue
            if p in self._seen:
                continue
            try:
                parsed = parse_filename(p.name)
            except Exception as exc:
                raise WatcherCorruption(
                    p,
                    f"canonical event filename invalid: {p.name}: {exc}",
                ) from exc
            try:
                body = json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:
                raise WatcherCorruption(
                    p,
                    f"canonical event body unreadable: {p.name}: {exc}",
                ) from exc
            event_id = body.get("event_id")
            if not isinstance(event_id, str):
                raise WatcherCorruption(
                    p,
                    f"canonical event missing string event_id: {p.name}",
                )
            expected_event_id = f"{parsed.iso_ms}-{parsed.seq:04d}-{parsed.uid}"
            if event_id != expected_event_id:
                raise WatcherCorruption(
                    p,
                    f"canonical event_id mismatch: filename={expected_event_id} body={event_id}",
                )
            new.append(
                EventFile(
                    path=p,
                    iso_ms=parsed.iso_ms,
                    seq=parsed.seq,
                    uid=parsed.uid,
                    event_id=event_id,
                )
            )
        new.sort(key=lambda e: e.sort_key)
        return new


__all__ = ["EventFile", "EventsWatcher", "WatcherCorruption"]
