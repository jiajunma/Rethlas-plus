"""Markdown-backed projection store — the Kuzu-free replacement for KuzuBackend.

§13 cuts Kuzu entirely. The projected KB now lives as:

- **markdown node files** (``knowledge_base/nodes/*.md``) — the durable *math*
  of every node (all states, staged *and* verified; ``pass_count`` frontmatter
  distinguishes them). Written via :mod:`librarian.renderer`, read via
  :mod:`common.kb.markdown_reader`.
- **in-memory operational state** — the fields markdown does not carry
  (``repair_count``, ``verification_report``, ``repair_hint``,
  ``introduced_by_actor``) plus the ``AppliedEvent`` ledger. Rebuilt by
  replaying ``events/`` (the librarian does that on startup); never persisted
  to a database.

:class:`MarkdownBackend` exposes the **same method surface** as the old
``KuzuBackend`` so :class:`librarian.projector.Projector` and the daemon are
near drop-in. Transactions are snapshot-based: ``begin`` snapshots the in-memory
maps, mutations stay in memory, ``commit`` flushes dirty nodes to markdown, and
``rollback`` restores the snapshot without writing.
"""

from __future__ import annotations

import dataclasses
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from common.kb.types import AppliedEvent, ApplyOutcome, Node, NodeKind
from librarian.renderer import node_filename, write_node_file


@dataclass(frozen=True, slots=True)
class RawNodeRow:
    """Full projected row (math + operational). Same shape as the old backend."""

    label: str
    kind: str
    statement: str
    proof: str
    statement_hash: str
    verification_hash: str
    pass_count: int
    repair_count: int
    verification_report: str
    repair_hint: str
    remark: str
    source_note: str
    introduced_by_actor: str = "user:cli"


def _bfs_path(adjacency: dict[str, list[str]], start: str, target: str) -> list[str] | None:
    """Return a path ``[start, ..., target]`` over ``adjacency`` if one exists."""
    if start == target:
        return [start]
    visited: set[str] = {start}
    parent: dict[str, str] = {}
    q: deque[str] = deque([start])
    while q:
        cur = q.popleft()
        for nxt in adjacency.get(cur, []):
            if nxt in visited:
                continue
            parent[nxt] = cur
            if nxt == target:
                path = [target]
                p = cur
                while True:
                    path.append(p)
                    if p == start:
                        break
                    p = parent[p]
                return list(reversed(path))
            visited.add(nxt)
            q.append(nxt)
    return None


def _kind_str(node: Node) -> str:
    return node.kind.value if isinstance(node.kind, NodeKind) else node.kind


def _row(node: Node) -> RawNodeRow:
    return RawNodeRow(
        label=node.label,
        kind=_kind_str(node),
        statement=node.statement,
        proof=node.proof,
        statement_hash=node.statement_hash,
        verification_hash=node.verification_hash,
        pass_count=node.pass_count,
        repair_count=node.repair_count,
        verification_report=node.verification_report,
        repair_hint=node.repair_hint,
        remark=node.remark,
        source_note=node.source_note,
        introduced_by_actor=node.introduced_by_actor or "user:cli",
    )


class MarkdownBackend:
    """Librarian's projected KB, backed by markdown files + in-memory state."""

    def __init__(self, nodes_dir: str | Path) -> None:
        self._nodes_dir = Path(nodes_dir)
        # Authoritative working state: full Node (math + operational) per label.
        self._nodes: dict[str, Node] = {}
        self._applied: dict[str, AppliedEvent] = {}
        # Labels written since the last commit, flushed to markdown on commit.
        self._dirty: set[str] = set()
        self._snapshot: tuple[dict[str, Node], dict[str, AppliedEvent], set[str]] | None = None
        # Starts EMPTY by design. events/ is the source of truth; the librarian
        # rebuilds this in-memory projection by replaying events on startup, and
        # markdown is the write-only output (written on commit). The backend does
        # NOT load markdown back as authoritative — that would collide with the
        # replay (re-applying ``user.node_added`` for a node already on disk).
        # Workers/humans read the markdown files directly via ``markdown_reader``.

    # ---- lifecycle --------------------------------------------------
    def close(self) -> None:
        return None

    def __enter__(self) -> "MarkdownBackend":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def table_names(self) -> list[str]:
        # Compatibility shim — there are no tables, just markdown + memory.
        return []

    # ---- transactions (snapshot-based) -----------------------------
    def begin(self) -> None:
        self._snapshot = (dict(self._nodes), dict(self._applied), set(self._dirty))

    def commit(self) -> None:
        for label in self._dirty:
            node = self._nodes.get(label)
            if node is not None:
                write_node_file(self._nodes_dir, node)
        self._dirty.clear()
        self._snapshot = None

    def rollback(self) -> None:
        if self._snapshot is not None:
            self._nodes, self._applied, self._dirty = (
                self._snapshot[0],
                self._snapshot[1],
                self._snapshot[2],
            )
            self._snapshot = None

    # ---- AppliedEvent ledger (in-memory) ---------------------------
    def applied_event(self, event_id: str) -> AppliedEvent | None:
        return self._applied.get(event_id)

    def record_applied_event(
        self,
        *,
        event_id: str,
        status: ApplyOutcome,
        event_sha256: str,
        reason: str | None = None,
        detail: str | None = None,
        target_label: str | None = None,
        applied_at: str | None = None,
    ) -> AppliedEvent:
        if applied_at is None:
            from datetime import datetime, timezone

            applied_at = (
                datetime.now(tz=timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
        ev = AppliedEvent(
            event_id=event_id,
            status=status,
            reason=reason,
            detail=detail,
            event_sha256=event_sha256,
            applied_at=applied_at,
            target_label=target_label,
        )
        self._applied[event_id] = ev
        return ev

    def applied_event_counts(self) -> tuple[int, int, int]:
        total = len(self._applied)
        applied = sum(1 for e in self._applied.values() if e.status is ApplyOutcome.APPLIED)
        failed = sum(1 for e in self._applied.values() if e.status is ApplyOutcome.APPLY_FAILED)
        return total, applied, failed

    def last_applied_event_id(self) -> str:
        applied = [e for e in self._applied.values() if e.status is ApplyOutcome.APPLIED]
        if not applied:
            return ""
        applied.sort(key=lambda e: (e.applied_at, e.event_id), reverse=True)
        return applied[0].event_id or ""

    def applied_failed_rows(self) -> list[dict[str, Any]]:
        rows = [e for e in self._applied.values() if e.status is ApplyOutcome.APPLY_FAILED]
        rows.sort(key=lambda e: (e.applied_at, e.event_id), reverse=True)
        return [
            {
                "event_id": e.event_id,
                "reason": e.reason or "",
                "detail": e.detail or "",
                "applied_at": e.applied_at,
                "target": e.target_label or "",
            }
            for e in rows
        ]

    def applied_since_rows(self, watermark: tuple[str, str]) -> list[dict[str, Any]]:
        applied_at_wm, event_id_wm = watermark
        rows = sorted(self._applied.values(), key=lambda e: (e.applied_at, e.event_id))
        out: list[dict[str, Any]] = []
        for e in rows:
            if applied_at_wm:
                key = (e.applied_at, e.event_id)
                if key <= (applied_at_wm, event_id_wm):
                    continue
            out.append(
                {
                    "event_id": e.event_id,
                    "status": e.status.value,
                    "reason": e.reason or "",
                    "detail": e.detail or "",
                    "applied_at": e.applied_at,
                    "target": e.target_label or "",
                }
            )
        return out

    def iter_applied_events(self) -> list[AppliedEvent]:
        """All AppliedEvent rows (replaces linter's raw Cypher inventory pull)."""
        return list(self._applied.values())

    # ---- Node helpers ----------------------------------------------
    def node_by_label(self, label: str) -> RawNodeRow | None:
        node = self._nodes.get(label)
        return _row(node) if node is not None else None

    def node_labels(self) -> list[str]:
        return sorted(self._nodes)

    def create_node(self, node: Node) -> None:
        self._nodes[node.label] = node
        self._dirty.add(node.label)

    def update_node(self, node: Node) -> None:
        self._nodes[node.label] = node
        self._dirty.add(node.label)

    def set_node_fields(self, label: str, **fields: Any) -> None:
        if not fields:
            return
        current = self._nodes.get(label)
        if current is None:
            raise KeyError(f"set_node_fields on missing node {label!r}")
        self._nodes[label] = dataclasses.replace(current, **fields)
        self._dirty.add(label)

    def dependencies_of(self, label: str) -> list[str]:
        node = self._nodes.get(label)
        return sorted(node.depends_on) if node is not None else []

    def dependents_of(self, label: str) -> list[str]:
        return sorted(
            other for other, node in self._nodes.items() if label in node.depends_on
        )

    def all_dependency_edges(self) -> list[tuple[str, str]]:
        """Every ``(label, dep)`` edge — replaces the projector's raw Cypher pull."""
        return [
            (label, dep) for label, node in self._nodes.items() for dep in node.depends_on
        ]

    def would_introduce_cycle(
        self, label: str, new_deps: Iterable[str]
    ) -> list[str] | None:
        deps_list = list(new_deps)
        if not deps_list:
            return None
        for dep in deps_list:
            if dep == label:
                return [label, label]
        adjacency: dict[str, list[str]] = {}
        for src, dst in self.all_dependency_edges():
            adjacency.setdefault(src, []).append(dst)
        for dep in deps_list:
            path = _bfs_path(adjacency, dep, label)
            if path is not None:
                return [label, *path]
        return None

    # ---- dashboard / coordinator views -----------------------------
    def dashboard_node_rows(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for node in self._nodes.values():
            out.append(
                {
                    "label": node.label,
                    "kind": _kind_str(node),
                    "statement": node.statement or "",
                    "proof": node.proof or "",
                    "pass_count": node.pass_count,
                    "repair_count": node.repair_count,
                    "statement_hash": node.statement_hash or "",
                    "verification_hash": node.verification_hash or "",
                    "repair_hint": node.repair_hint or "",
                    "verification_report": node.verification_report or "",
                    "introduced_by_actor": node.introduced_by_actor or "user:cli",
                    "deps": sorted(node.depends_on),
                }
            )
        return out

    def coordinator_candidate_rows(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for node in self._nodes.values():
            dep_labels = sorted(node.depends_on)
            dep_hashes: list[str] = []
            dep_counts: list[int] = []
            for dep in dep_labels:
                drow = self._nodes.get(dep)
                dep_hashes.append(drow.statement_hash if drow is not None else "")
                dep_counts.append(drow.pass_count if drow is not None else -1)
            out.append(
                {
                    "target": node.label,
                    "target_kind": _kind_str(node),
                    "statement": node.statement or "",
                    "proof": node.proof or "",
                    "statement_hash": node.statement_hash or "",
                    "verification_hash": node.verification_hash or "",
                    "pass_count": node.pass_count,
                    "repair_count": node.repair_count,
                    "repair_hint": node.repair_hint or "",
                    "verification_report": node.verification_report or "",
                    "introduced_by_actor": node.introduced_by_actor or "user:cli",
                    "dep_labels": dep_labels,
                    "dep_hashes": dep_hashes,
                    "dep_counts": dep_counts,
                }
            )
        return out

    # ---- teardown (rebuild) ----------------------------------------
    def wipe(self) -> None:
        self._nodes.clear()
        self._applied.clear()
        self._dirty.clear()
        if self._nodes_dir.is_dir():
            for path in self._nodes_dir.glob("*.md"):
                path.unlink()


__all__ = ["MarkdownBackend", "RawNodeRow"]
