"""Thin wrapper around an embedded Kuzu database for librarian's projection.

Only the **librarian process** instantiates :class:`KuzuBackend`; other
processes reach KB state via librarian's IPC (§4.1 "Revised concurrency
model"). This module is therefore forbidden content for worker wrappers
(§4.1 — enforced by a static grep test in M5/M6/M7).

Responsibilities:
- Initialise / migrate schema (`Node`, `DependsOn`, `ProjectionState`,
  `AppliedEvent`).
- Provide a transactional ``apply_event(event)`` that updates KB state
  and the ``AppliedEvent`` row atomically (§5.2).
- Support idempotent re-apply of an already-decided event (via
  ``AppliedEvent`` primary key).
- Support ``rebuild_from_events(events_dir)`` that wipes the projection
  and replays canonical events in ``(iso_ms, seq, uid)`` order (§11.2).

The projection rules themselves (pass_count / repair_count / Merkle
cascade / repair_hint handling) live in :mod:`librarian.projector` — this
module just exposes the storage primitives those rules need.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import kuzu

from common.kb.types import AppliedEvent, ApplyOutcome, Node, NodeKind


# Schema definition kept as a module-level list so tests can assert
# migrations are all applied.
_SCHEMA_DDL: list[str] = [
    """
    CREATE NODE TABLE IF NOT EXISTS Node (
      label STRING PRIMARY KEY,
      kind STRING,
      statement STRING,
      proof STRING,
      statement_hash STRING,
      verification_hash STRING,
      pass_count INT64 DEFAULT -1,
      repair_count INT64 DEFAULT 0,
      verification_report STRING DEFAULT '',
      repair_hint STRING DEFAULT '',
      remark STRING DEFAULT '',
      source_note STRING DEFAULT ''
    )
    """,
    # Kuzu currently requires explicit REL TABLE DDL without IF NOT EXISTS
    # pre-1.0; query the catalog instead and create only when missing.
    # We handle that via _ensure_rel_table() below.
    """
    CREATE NODE TABLE IF NOT EXISTS ProjectionState (
      key STRING PRIMARY KEY,
      value STRING
    )
    """,
    """
    CREATE NODE TABLE IF NOT EXISTS AppliedEvent (
      event_id STRING PRIMARY KEY,
      status STRING,
      reason STRING DEFAULT '',
      detail STRING DEFAULT '',
      event_sha256 STRING,
      applied_at STRING,
      target_label STRING DEFAULT ''
    )
    """,
]


def _utc_now_iso() -> str:
    """Timestamp helper for ``AppliedEvent.applied_at`` (§2.4 trailer / G1)."""
    now = datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds")
    # isoformat() returns "+00:00"; §2.4 G1 requires "Z" suffix.
    return now.replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class RawNodeRow:
    """Raw row returned from ``SELECT * FROM Node``. Used by projector."""

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


class KuzuBackend:
    """Librarian's authoritative view of the projected KB."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db = kuzu.Database(str(self._db_path))
        self._conn = kuzu.Connection(self._db)
        self._ensure_schema()

    # ---- lifecycle --------------------------------------------------
    def close(self) -> None:
        # Explicitly drop connection + database so the OS-level lock is
        # released. Useful for tests that want to verify readers can
        # now open.
        self._conn = None  # type: ignore[assignment]
        self._db = None  # type: ignore[assignment]

    def __enter__(self) -> "KuzuBackend":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- schema -----------------------------------------------------
    def _ensure_schema(self) -> None:
        for ddl in _SCHEMA_DDL:
            self._conn.execute(ddl)
        self._ensure_rel_table()

    def _ensure_rel_table(self) -> None:
        # DependsOn does not support IF NOT EXISTS at Kuzu 0.11.3; swallow
        # "already exists" errors so the call is idempotent under rebuild.
        try:
            self._conn.execute(
                "CREATE REL TABLE DependsOn (FROM Node TO Node, MANY_MANY)"
            )
        except RuntimeError as exc:
            if "already exists" in str(exc).lower():
                return
            raise

    def table_names(self) -> list[str]:
        res = self._conn.execute("CALL show_tables() RETURN *")
        names: list[str] = []
        while res.has_next():
            row = res.get_next()
            names.append(row[1])  # (id, name, type, ...)
        return names

    # ---- AppliedEvent helpers --------------------------------------
    def applied_event(self, event_id: str) -> AppliedEvent | None:
        res = self._conn.execute(
            "MATCH (a:AppliedEvent {event_id: $eid}) "
            "RETURN a.event_id, a.status, a.reason, a.detail, a.event_sha256, "
            "a.applied_at, a.target_label",
            {"eid": event_id},
        )
        if not res.has_next():
            return None
        row = res.get_next()
        return AppliedEvent(
            event_id=row[0],
            status=ApplyOutcome(row[1]),
            reason=row[2] or None,
            detail=row[3] or None,
            event_sha256=row[4],
            applied_at=row[5],
            target_label=row[6] or None,
        )

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
        """Insert a new :class:`AppliedEvent` row. Raises if one already exists."""
        if applied_at is None:
            applied_at = _utc_now_iso()
        self._conn.execute(
            """
            CREATE (a:AppliedEvent {
              event_id: $eid,
              status: $status,
              reason: $reason,
              detail: $detail,
              event_sha256: $sha,
              applied_at: $at,
              target_label: $tgt
            })
            """,
            {
                "eid": event_id,
                "status": status.value,
                "reason": reason or "",
                "detail": detail or "",
                "sha": event_sha256,
                "at": applied_at,
                "tgt": target_label or "",
            },
        )
        return AppliedEvent(
            event_id=event_id,
            status=status,
            reason=reason,
            detail=detail,
            event_sha256=event_sha256,
            applied_at=applied_at,
            target_label=target_label,
        )

    # ---- Node helpers ---------------------------------------------
    def node_by_label(self, label: str) -> RawNodeRow | None:
        res = self._conn.execute(
            """
            MATCH (n:Node {label: $lbl})
            RETURN n.label, n.kind, n.statement, n.proof, n.statement_hash,
                   n.verification_hash, n.pass_count, n.repair_count,
                   n.verification_report, n.repair_hint, n.remark, n.source_note
            """,
            {"lbl": label},
        )
        if not res.has_next():
            return None
        r = res.get_next()
        return RawNodeRow(
            label=r[0],
            kind=r[1],
            statement=r[2],
            proof=r[3],
            statement_hash=r[4],
            verification_hash=r[5],
            pass_count=int(r[6]),
            repair_count=int(r[7]),
            verification_report=r[8] or "",
            repair_hint=r[9] or "",
            remark=r[10] or "",
            source_note=r[11] or "",
        )

    def node_labels(self) -> list[str]:
        res = self._conn.execute("MATCH (n:Node) RETURN n.label ORDER BY n.label")
        out = []
        while res.has_next():
            out.append(res.get_next()[0])
        return out

    def create_node(self, node: Node) -> None:
        self._conn.execute(
            """
            CREATE (n:Node {
              label: $lbl,
              kind: $kind,
              statement: $stmt,
              proof: $proof,
              statement_hash: $sh,
              verification_hash: $vh,
              pass_count: $pc,
              repair_count: $rc,
              verification_report: $vr,
              repair_hint: $rh,
              remark: $rem,
              source_note: $src
            })
            """,
            self._node_params(node),
        )
        self._set_dependencies(node.label, node.depends_on)

    def update_node(self, node: Node) -> None:
        """Overwrite every column of an existing ``Node`` row."""
        params = self._node_params(node)
        self._conn.execute(
            """
            MATCH (n:Node {label: $lbl})
            SET n.kind = $kind,
                n.statement = $stmt,
                n.proof = $proof,
                n.statement_hash = $sh,
                n.verification_hash = $vh,
                n.pass_count = $pc,
                n.repair_count = $rc,
                n.verification_report = $vr,
                n.repair_hint = $rh,
                n.remark = $rem,
                n.source_note = $src
            """,
            params,
        )
        self._set_dependencies(node.label, node.depends_on)

    def set_node_fields(self, label: str, **fields: Any) -> None:
        """Set a subset of Node columns on an existing row."""
        if not fields:
            return
        sets = ", ".join(f"n.{k} = ${k}" for k in fields)
        params: dict[str, Any] = {"lbl": label, **fields}
        self._conn.execute(
            f"MATCH (n:Node {{label: $lbl}}) SET {sets}",
            params,
        )

    @staticmethod
    def _node_params(node: Node) -> dict[str, Any]:
        kind = node.kind.value if isinstance(node.kind, NodeKind) else node.kind
        return {
            "lbl": node.label,
            "kind": kind,
            "stmt": node.statement,
            "proof": node.proof,
            "sh": node.statement_hash,
            "vh": node.verification_hash,
            "pc": node.pass_count,
            "rc": node.repair_count,
            "vr": "",
            "rh": "",
            "rem": node.remark,
            "src": node.source_note,
        }

    def _set_dependencies(self, label: str, depends_on: Iterable[str]) -> None:
        # Fully overwrite DependsOn edges for this node.
        self._conn.execute(
            "MATCH (n:Node {label: $lbl})-[r:DependsOn]->() DELETE r",
            {"lbl": label},
        )
        for dep in depends_on:
            self._conn.execute(
                """
                MATCH (a:Node {label: $from}), (b:Node {label: $to})
                CREATE (a)-[:DependsOn]->(b)
                """,
                {"from": label, "to": dep},
            )

    def dependencies_of(self, label: str) -> list[str]:
        res = self._conn.execute(
            "MATCH (n:Node {label: $lbl})-[:DependsOn]->(d:Node) "
            "RETURN d.label ORDER BY d.label",
            {"lbl": label},
        )
        out = []
        while res.has_next():
            out.append(res.get_next()[0])
        return out

    def dependents_of(self, label: str) -> list[str]:
        res = self._conn.execute(
            "MATCH (d:Node)-[:DependsOn]->(n:Node {label: $lbl}) "
            "RETURN d.label ORDER BY d.label",
            {"lbl": label},
        )
        out = []
        while res.has_next():
            out.append(res.get_next()[0])
        return out

    def would_introduce_cycle(
        self,
        label: str,
        new_deps: Iterable[str],
    ) -> list[str] | None:
        """Return the cycle path ``[label, ..., label]`` if adding ``new_deps``
        as ``DependsOn`` edges from ``label`` would close a cycle; ``None``
        otherwise.

        Used by librarian's apply-time projection check (§6.5). The graph
        walk is a manual BFS in Python because Kuzu 0.11.3's Cypher dialect
        does not accept unbounded ``*1..`` variable-length edges combined
        with ``path p = ...`` binding; we pull the ``DependsOn`` edge set
        once and run the BFS in-process.
        """
        deps_list = list(new_deps)
        if not deps_list:
            return None

        # Self-reference short-circuit.
        for dep in deps_list:
            if dep == label:
                return [label, label]

        # Build an adjacency map of the current DependsOn graph.
        adjacency: dict[str, list[str]] = {}
        res = self._conn.execute(
            "MATCH (u:Node)-[:DependsOn]->(v:Node) RETURN u.label, v.label"
        )
        while res.has_next():
            src, dst = res.get_next()
            adjacency.setdefault(src, []).append(dst)

        # For each new dep, check whether there is an existing path
        # ``dep -> ... -> label``. If so, ``label -> dep`` closes a cycle.
        for dep in deps_list:
            path = _bfs_path(adjacency, dep, label)
            if path is not None:
                return [label, *path]
        return None

    # ---- Transactions ---------------------------------------------
    def begin(self) -> None:
        self._conn.execute("BEGIN TRANSACTION")

    def commit(self) -> None:
        self._conn.execute("COMMIT")

    def rollback(self) -> None:
        self._conn.execute("ROLLBACK")

    # ---- Full teardown (used by rebuild) ---------------------------
    def wipe(self) -> None:
        """Drop all rows. Used by ``rebuild_from_events``."""
        self._conn.execute("MATCH ()-[r:DependsOn]->() DELETE r")
        self._conn.execute("MATCH (n:Node) DELETE n")
        self._conn.execute("MATCH (a:AppliedEvent) DELETE a")
        self._conn.execute("MATCH (p:ProjectionState) DELETE p")


def _bfs_path(
    adjacency: dict[str, list[str]], start: str, target: str
) -> list[str] | None:
    """Return a path ``[start, ..., target]`` if one exists, else ``None``."""
    from collections import deque

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
                # Reconstruct path.
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
