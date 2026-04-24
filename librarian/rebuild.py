"""Rebuild projection state from truth (ARCHITECTURE §11.2).

Librarian's rebuild path:
1. ``wipe()`` the Kuzu tables.
2. Scan ``events/**/*.json`` for canonical event files.
3. Sort by ``(iso_ms, seq, uid)`` derived from each filename — the
   lexicographic sort is the §3.2 global causal order.
4. For each event, parse the body, read the raw bytes, and call
   :meth:`librarian.projector.Projector.apply`.

The outcome is a freshly-populated KB whose contents are a pure
function of ``events/``. Idempotent re-runs — a second rebuild over
the same ``events/`` tree produces byte-identical Kuzu rows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from common.events.filenames import parse_filename
from common.events.io import read_event
from common.kb.kuzu_backend import KuzuBackend
from librarian.projector import Projector


def _iter_event_files(events_root: Path) -> Iterator[Path]:
    if not events_root.is_dir():
        return
    for p in events_root.rglob("*.json"):
        if p.is_file():
            yield p


def _sort_key(path: Path) -> tuple[str, int, str]:
    """``(iso_ms, seq, uid)`` sort key per §3.2."""
    parsed = parse_filename(path.name)
    return (parsed.iso_ms, parsed.seq, parsed.uid)


def rebuild_from_events(
    *,
    backend: KuzuBackend,
    events_root: Path,
) -> list[tuple[Path, str, str | None]]:
    """Replay ``events_root`` into ``backend``. Returns an audit trail.

    The audit trail is ``[(path, status, reason), ...]`` so the caller
    (tests, CLI) can surface the decision per event.
    """
    backend.wipe()
    files = sorted(_iter_event_files(events_root), key=_sort_key)
    projector = Projector(backend)
    trail: list[tuple[Path, str, str | None]] = []
    for path in files:
        raw, body = read_event(path)
        outcome = projector.apply(body, raw)
        trail.append((path, outcome.status.value, outcome.reason))
    return trail
