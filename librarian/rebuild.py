"""Rebuild projection state from truth (ARCHITECTURE §11.2 / §6.5).

Librarian's rebuild path:

1. ``wipe()`` the Kuzu tables.
2. Scan ``events/**/*.json`` for canonical event files.
3. Sort by ``(iso_ms, seq, uid)`` derived from each filename — the
   lexicographic sort is the §3.2 global causal order.
4. For each event, parse the body, read the raw bytes, and call
   :meth:`librarian.projector.Projector.apply`.
5. Run the same ``nodes/`` reconciliation pass librarian does on startup
   (§6.5 step 5) so the rebuild output is byte-identical to a fresh
   librarian startup.

The outcome is a freshly-populated KB whose contents are a pure
function of ``events/``. Idempotent re-runs — a second rebuild over
the same ``events/`` tree produces byte-identical Kuzu rows AND
byte-identical ``nodes/*.md`` files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from common.events.filenames import parse_filename
from common.events.io import read_event
from common.kb.kuzu_backend import KuzuBackend
from common.kb.types import Node, NodeKind
from librarian.projector import Projector
from librarian.renderer import node_filename, write_node_file


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
    nodes_dir: Path | None = None,
) -> list[tuple[Path, str, str | None]]:
    """Replay ``events_root`` into ``backend``. Returns an audit trail.

    The audit trail is ``[(path, status, reason), ...]`` so the caller
    (tests, CLI) can surface the decision per event.

    When ``nodes_dir`` is provided, the function also renders every
    ``pass_count >= 1`` node into that directory (§6.5 step 5). Pass
    ``None`` (default) to keep callers that only want a Kuzu rebuild.
    """
    backend.wipe()
    files = sorted(_iter_event_files(events_root), key=_sort_key)
    projector = Projector(backend)
    trail: list[tuple[Path, str, str | None]] = []
    for path in files:
        raw, body = read_event(path)
        outcome = projector.apply(body, raw)
        trail.append((path, outcome.status.value, outcome.reason))

    if nodes_dir is not None:
        render_published_nodes(backend, nodes_dir)
    return trail


def render_published_nodes(backend: KuzuBackend, nodes_dir: Path) -> int:
    """Re-render every Kuzu node with ``pass_count >= 1`` into ``nodes_dir``.

    Returns the number of files written. Idempotent — calling twice on
    an unchanged backend produces byte-identical output.
    """
    nodes_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for label in backend.node_labels():
        row = backend.node_by_label(label)
        if row is None or row.pass_count < 1:
            continue
        deps = backend.dependencies_of(label)
        node = Node(
            label=row.label,
            kind=NodeKind(row.kind),
            statement=row.statement,
            proof=row.proof,
            remark=row.remark,
            source_note=row.source_note,
            pass_count=row.pass_count,
            repair_count=row.repair_count,
            statement_hash=row.statement_hash,
            verification_hash=row.verification_hash,
            depends_on=tuple(deps),
        )
        try:
            write_node_file(nodes_dir, node)
        except ValueError:
            continue
        count += 1
    return count
