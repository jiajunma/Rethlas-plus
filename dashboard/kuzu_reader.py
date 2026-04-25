"""Read-only Kuzu accessor for the dashboard (ARCHITECTURE §6.7.1).

Dashboard opens a fresh ``kuzu.Database(read_only=True)`` per request —
Kuzu cleans up file descriptors on connection close, and request rates
are low enough that the open cost is negligible compared to network /
template render.

When ``librarian.json.rebuild_in_progress = true``, the
:func:`open_or_503` wrapper raises :class:`RebuildInProgress` so the
HTTP layer can return 503 + ``Retry-After: 5`` (per PHASE1 M9).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from librarian.heartbeat import read_heartbeat as read_librarian_hb


class RebuildInProgress(RuntimeError):
    """Signals that the projection is being rebuilt."""


@dataclass(frozen=True, slots=True)
class NodeRow:
    label: str
    kind: str
    statement: str
    proof: str
    pass_count: int
    repair_count: int
    statement_hash: str
    verification_hash: str
    repair_hint: str
    verification_report: str
    deps: tuple[str, ...]


def _check_rebuild(state_dir: Path) -> None:
    hb = read_librarian_hb(state_dir / "librarian.json")
    if hb and hb.get("rebuild_in_progress"):
        raise RebuildInProgress("rebuild_in_progress")


def list_nodes(ws_root: Path) -> list[NodeRow]:
    _check_rebuild(ws_root / "runtime" / "state")
    db_path = ws_root / "knowledge_base" / "dag.kz"
    if not db_path.is_dir() and not db_path.exists():
        return []
    import kuzu
    db = kuzu.Database(str(db_path), read_only=True)
    conn = kuzu.Connection(db)
    try:
        res = conn.execute(
            """
            MATCH (n:Node)
            OPTIONAL MATCH (n)-[:DependsOn]->(d:Node)
            RETURN n.label, n.kind, n.statement, n.proof,
                   n.pass_count, n.repair_count,
                   n.statement_hash, n.verification_hash,
                   n.repair_hint, n.verification_report,
                   collect(d.label)
            """
        )
        out: list[NodeRow] = []
        while res.has_next():
            r = res.get_next()
            deps = tuple(d for d in (r[10] or []) if d is not None)
            out.append(
                NodeRow(
                    label=r[0],
                    kind=r[1],
                    statement=r[2] or "",
                    proof=r[3] or "",
                    pass_count=int(r[4]) if r[4] is not None else -1,
                    repair_count=int(r[5]) if r[5] is not None else 0,
                    statement_hash=r[6] or "",
                    verification_hash=r[7] or "",
                    repair_hint=r[8] or "",
                    verification_report=r[9] or "",
                    deps=deps,
                )
            )
        return out
    finally:
        del conn
        del db


def list_applied_failed(ws_root: Path) -> list[dict[str, Any]]:
    _check_rebuild(ws_root / "runtime" / "state")
    db_path = ws_root / "knowledge_base" / "dag.kz"
    if not db_path.is_dir() and not db_path.exists():
        return []
    import kuzu
    db = kuzu.Database(str(db_path), read_only=True)
    conn = kuzu.Connection(db)
    try:
        res = conn.execute(
            "MATCH (a:AppliedEvent) WHERE a.status = 'apply_failed' "
            "RETURN a.event_id, a.reason, a.detail, a.applied_at, a.target_label "
            "ORDER BY a.applied_at DESC"
        )
        out: list[dict[str, Any]] = []
        while res.has_next():
            r = res.get_next()
            out.append(
                {
                    "event_id": r[0],
                    "reason": r[1] or "",
                    "detail": r[2] or "",
                    "applied_at": r[3],
                    "target": r[4] or "",
                }
            )
        return out
    finally:
        del conn
        del db


def dependents_of(ws_root: Path, label: str) -> list[str]:
    """Return the labels of nodes that DependsOn this label."""
    _check_rebuild(ws_root / "runtime" / "state")
    db_path = ws_root / "knowledge_base" / "dag.kz"
    if not db_path.is_dir() and not db_path.exists():
        return []
    import kuzu
    db = kuzu.Database(str(db_path), read_only=True)
    conn = kuzu.Connection(db)
    try:
        res = conn.execute(
            "MATCH (d:Node)-[:DependsOn]->(n:Node {label: $lbl}) RETURN d.label",
            {"lbl": label},
        )
        out: list[str] = []
        while res.has_next():
            out.append(res.get_next()[0])
        out.sort()
        return out
    finally:
        del conn
        del db


def list_applied_since(
    ws_root: Path,
    watermark: tuple[str, str],
) -> list[dict[str, Any]]:
    """Return ``AppliedEvent`` rows strictly after ``watermark``.

    Used by :class:`dashboard.state_watcher.StateWatcher` to fire the
    ``applied_event`` SSE envelope (§6.7.1). The watermark is the
    lexicographic tuple ``(applied_at, event_id)`` of the most recent
    row already published. The tie-break on ``event_id`` matters when
    multiple AppliedEvent rows share the same millisecond timestamp.
    """
    _check_rebuild(ws_root / "runtime" / "state")
    db_path = ws_root / "knowledge_base" / "dag.kz"
    if not db_path.is_dir() and not db_path.exists():
        return []
    import kuzu
    db = kuzu.Database(str(db_path), read_only=True)
    conn = kuzu.Connection(db)
    try:
        applied_at_wm, event_id_wm = watermark
        if applied_at_wm:
            res = conn.execute(
                "MATCH (a:AppliedEvent) "
                "WHERE a.applied_at > $ts "
                "   OR (a.applied_at = $ts AND a.event_id > $eid) "
                "RETURN a.event_id, a.status, a.reason, a.detail, a.applied_at, "
                "a.target_label ORDER BY a.applied_at ASC, a.event_id ASC",
                {"ts": applied_at_wm, "eid": event_id_wm},
            )
        else:
            res = conn.execute(
                "MATCH (a:AppliedEvent) "
                "RETURN a.event_id, a.status, a.reason, a.detail, a.applied_at, "
                "a.target_label ORDER BY a.applied_at ASC, a.event_id ASC"
            )
        out: list[dict[str, Any]] = []
        while res.has_next():
            r = res.get_next()
            out.append(
                {
                    "event_id": r[0],
                    "status": r[1],
                    "reason": r[2] or "",
                    "detail": r[3] or "",
                    "applied_at": r[4],
                    "target": r[5] or "",
                }
            )
        return out
    finally:
        del conn
        del db


__all__ = [
    "NodeRow",
    "RebuildInProgress",
    "dependents_of",
    "list_applied_failed",
    "list_applied_since",
    "list_nodes",
]
