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


class EventsWatcher:
    """Stateful scanner that reports unseen event files in causal order."""

    def __init__(self, events_root: Path) -> None:
        self.root = events_root
        self._seen: set[Path] = set()

    def prime(self) -> None:
        """Mark every file currently on disk as already-seen.

        Used after librarian's startup replay finishes so we don't
        re-emit APPLY for every event we just processed.
        """
        if not self.root.is_dir():
            return
        for p in self.root.rglob("*.json"):
            if p.is_file():
                self._seen.add(p)

    def poll(self) -> list[EventFile]:
        """Return new event files in ``(iso_ms, seq, uid)`` order."""
        if not self.root.is_dir():
            return []
        new: list[EventFile] = []
        for p in self.root.rglob("*.json"):
            if not p.is_file():
                continue
            if p in self._seen:
                continue
            try:
                parsed = parse_filename(p.name)
            except Exception:
                # Filename does not match §3.2 — skip; linter will surface it.
                continue
            try:
                body = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            event_id = body.get("event_id")
            if not isinstance(event_id, str):
                continue
            new.append(
                EventFile(
                    path=p,
                    iso_ms=parsed.iso_ms,
                    seq=parsed.seq,
                    uid=parsed.uid,
                    event_id=event_id,
                )
            )
            self._seen.add(p)
        new.sort(key=lambda e: e.sort_key)
        return new


__all__ = ["EventFile", "EventsWatcher"]
