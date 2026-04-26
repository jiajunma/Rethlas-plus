"""Linter check functions (ARCHITECTURE §6.6, PHASE1 M10).

The linter audits five sources of drift between the truth layer and
its derived projections:

- **A**: ``events/`` filename ↔ body / id-uniqueness / reference resolution
- **B**: KB structural invariants (cycles, label uniqueness, label-prefix
  ↔ kind agreement, kind-appropriate fields)
- **C**: ``Node.pass_count`` matches the §5.5.1 audit replay
- **D**: ``Node.repair_count`` matches the §5.5.1 audit replay
- **E**: ``nodes/`` rendered files match Kuzu (with optional ``--repair``)
- **F**: ``events/`` SHA-256 ↔ ``AppliedEvent.event_sha256``

Each function returns a list of ``Violation`` records — JSON-friendly
dictionaries with a ``code`` keyword. The linter aggregates all six
categories before emitting the JSON report (no fail-fast).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Iterator

from common.events.filenames import FilenameError, parse_filename
from common.events.io import event_sha256, read_event
from common.events.schema import SchemaError, validate_event_schema
from common.kb.types import (
    AXIOM_KINDS,
    KIND_PREFIX,
    Node,
    NodeKind,
    PROOF_REQUIRING_KINDS,
)
from librarian.renderer import node_filename, render_node

if TYPE_CHECKING:
    from common.kb.kuzu_backend import KuzuBackend, RawNodeRow


# ---------------------------------------------------------------------------
# Violation envelope.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Violation:
    """A single linter finding. ``code`` is the canonical machine label."""

    code: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": dict(self.detail)}


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _iter_event_files(events_dir: Path) -> Iterator[Path]:
    """Yield every ``events/**/*.json`` in lexicographic order."""
    if not events_dir.is_dir():
        return
    for shard in sorted(events_dir.iterdir()):
        if not shard.is_dir():
            continue
        for f in sorted(shard.glob("*.json")):
            yield f


def _load_node_from_row(row: "RawNodeRow", deps: list[str]) -> Node:
    """Convert a Kuzu raw row + dep labels into a :class:`Node`."""
    return Node(
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
        verification_report=row.verification_report,
        repair_hint=row.repair_hint,
        depends_on=tuple(deps),
    )


# ---------------------------------------------------------------------------
# Category A — event stream integrity.
# ---------------------------------------------------------------------------
def check_a_event_integrity(events_dir: Path) -> list[Violation]:
    """A. Filename ↔ body, id uniqueness, missing-reference detection."""
    out: list[Violation] = []
    seen: dict[str, Path] = {}  # event_id -> first path

    # First sweep: parse + filename<>body / duplicate-id checks.
    bodies: dict[str, dict[str, Any]] = {}
    for f in _iter_event_files(events_dir):
        try:
            parsed = parse_filename(f.name)
        except FilenameError as exc:
            out.append(
                Violation(
                    "A_filename_invalid",
                    f"event filename does not match §3.2 shape: {f.name}",
                    {"path": str(f), "error": str(exc)},
                )
            )
            continue
        try:
            _raw, body = read_event(f)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            out.append(
                Violation(
                    "A_body_unreadable",
                    f"event body unreadable: {f.name}",
                    {"path": str(f), "error": str(exc)},
                )
            )
            continue

        # §3.4 envelope validation — surface bad type / actor / ts / payload
        # shape before they propagate further (linter is the only Phase I
        # safety net for this on already-applied events).
        try:
            validate_event_schema(body)
        except SchemaError as exc:
            out.append(
                Violation(
                    "A_envelope_invalid",
                    f"§3.4 envelope rejected: {exc}",
                    {"path": str(f), "error": str(exc)},
                )
            )

        body_event_id = body.get("event_id")
        # filename's event_id reconstructs as iso_ms-seq-uid.
        filename_event_id = (
            f"{parsed.iso_ms}-{parsed.seq:04d}-{parsed.uid}"
        )
        if body_event_id != filename_event_id:
            out.append(
                Violation(
                    "A_event_id_mismatch",
                    f"filename event_id {filename_event_id!r} disagrees with body {body_event_id!r}",
                    {"path": str(f), "filename_id": filename_event_id, "body_id": body_event_id},
                )
            )

        if not isinstance(body_event_id, str):
            continue

        prior = seen.get(body_event_id)
        if prior is not None:
            out.append(
                Violation(
                    "A_event_id_duplicate",
                    f"event_id {body_event_id!r} appears in multiple files",
                    {
                        "event_id": body_event_id,
                        "first": str(prior),
                        "second": str(f),
                    },
                )
            )
            continue
        seen[body_event_id] = f
        bodies[body_event_id] = body

    # Second sweep: missing references. ``parent_event_id`` and any
    # ``payload.*event_id`` field must point at a known event.
    known_ids = set(bodies.keys())
    for eid, body in bodies.items():
        for key, value in _walk(body):
            if not isinstance(value, str):
                continue
            if not key.endswith("event_id") or key == "event_id":
                continue
            if value and value not in known_ids:
                out.append(
                    Violation(
                        "A_missing_reference",
                        f"event {eid!r} references unknown event_id at {key!r}: {value!r}",
                        {"event_id": eid, "field": key, "missing": value},
                    )
                )
    return out


def _walk(obj: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            yield path, v
            yield from _walk(v, path)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            path = f"{prefix}[{i}]"
            yield path, v
            yield from _walk(v, path)


# ---------------------------------------------------------------------------
# Category B — KB structural.
# ---------------------------------------------------------------------------
def check_b_kb_structural(backend: "KuzuBackend") -> list[Violation]:
    """B. Cycles, label uniqueness, label-prefix ↔ kind, kind-appropriate fields."""
    out: list[Violation] = []
    labels = backend.node_labels()
    seen: set[str] = set()
    for lbl in labels:
        if lbl in seen:
            out.append(
                Violation(
                    "B_label_duplicate",
                    f"label {lbl!r} appears more than once in Kuzu",
                    {"label": lbl},
                )
            )
        seen.add(lbl)

    # Per-node prefix + kind-appropriate fields.
    nodes_by_label: dict[str, "RawNodeRow"] = {}
    for lbl in seen:
        row = backend.node_by_label(lbl)
        if row is None:
            continue
        nodes_by_label[lbl] = row
        prefix, _, _slug = lbl.partition(":")
        try:
            kind = NodeKind(row.kind)
        except ValueError:
            out.append(
                Violation(
                    "B_unknown_kind",
                    f"node {lbl!r} has unknown kind {row.kind!r}",
                    {"label": lbl, "kind": row.kind},
                )
            )
            continue
        expected_prefix = KIND_PREFIX[kind]
        if prefix != expected_prefix:
            out.append(
                Violation(
                    "B_prefix_kind_mismatch",
                    f"label {lbl!r} prefix does not match kind {kind.value!r} (expected {expected_prefix!r})",
                    {"label": lbl, "kind": kind.value, "expected_prefix": expected_prefix},
                )
            )
        if kind is NodeKind.EXTERNAL_THEOREM and not (row.source_note or "").strip():
            out.append(
                Violation(
                    "B_external_missing_source_note",
                    f"external_theorem {lbl!r} requires a non-empty source_note",
                    {"label": lbl},
                )
            )

    # Cycle detection — DFS, return first cycle path encountered.
    deps: dict[str, list[str]] = {
        lbl: backend.dependencies_of(lbl) for lbl in nodes_by_label
    }
    cycle = _find_cycle(deps)
    if cycle is not None:
        out.append(
            Violation(
                "B_cycle",
                f"DependsOn cycle: {' -> '.join(cycle)}",
                {"path": cycle},
            )
        )
    return out


def _find_cycle(deps: dict[str, list[str]]) -> list[str] | None:
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in deps}
    parent: dict[str, str | None] = {n: None for n in deps}

    def visit(start: str) -> list[str] | None:
        # Iterative DFS that records a cycle path as soon as we hit a GRAY.
        stack: list[tuple[str, Iterator[str]]] = [(start, iter(deps.get(start, [])))]
        color[start] = GRAY
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt not in color:
                    continue  # dangling reference, not a cycle
                if color[nxt] == GRAY:
                    # Reconstruct path: parent links from `node` back to nxt.
                    path = [nxt]
                    cur: str | None = node
                    while cur is not None and cur != nxt:
                        path.append(cur)
                        cur = parent.get(cur)
                    if cur == nxt:
                        path.append(nxt)
                    return list(reversed(path))
                if color[nxt] == WHITE:
                    parent[nxt] = node
                    color[nxt] = GRAY
                    stack.append((nxt, iter(deps.get(nxt, []))))
                    advanced = True
                    break
            if not advanced:
                color[node] = BLACK
                stack.pop()
        return None

    for n in deps:
        if color[n] == WHITE:
            cycle = visit(n)
            if cycle is not None:
                return cycle
    return None


# ---------------------------------------------------------------------------
# Categories C + D — pass_count and repair_count audits.
# ---------------------------------------------------------------------------
def _replay_verifier_facts(events_dir: Path) -> list[dict[str, Any]]:
    """Return all ``verifier.run_completed`` event bodies in iso_ms order."""
    out: list[dict[str, Any]] = []
    for f in _iter_event_files(events_dir):
        try:
            _raw, body = read_event(f)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if body.get("type") != "verifier.run_completed":
            continue
        out.append(body)
    out.sort(key=lambda b: b.get("event_id", ""))
    return out


def _statement_changing_iso_ms(events_dir: Path, label: str) -> str | None:
    """Return the ``iso_ms`` portion of the most recent event that set ``label``'s
    ``statement_hash`` to its current value.

    A precise computation requires Merkle cascade replay; for Phase I
    linter we approximate using the most recent event whose ``target ==
    label`` and whose payload mentions a ``statement_hash``. This is the
    common case (definition added/revised, generator commit). Cascade-
    only updates on a node whose own statement bytes did not change are a
    rarer source of drift; if needed they can be added in Phase II.
    """
    most_recent: str | None = None
    for f in _iter_event_files(events_dir):
        try:
            _raw, body = read_event(f)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if body.get("target") != label and label not in (
            n.get("label")
            for n in body.get("payload", {}).get("nodes", []) if isinstance(n, dict)
        ):
            continue
        # event_id begins with iso_ms.
        eid = body.get("event_id", "")
        iso_ms = eid.split("-", 1)[0] if eid else ""
        if iso_ms and (most_recent is None or iso_ms > most_recent):
            most_recent = iso_ms
    return most_recent


def check_c_pass_count(events_dir: Path, backend: "KuzuBackend") -> list[Violation]:
    """C. Audit ``pass_count`` against §5.5.1 replay."""
    out: list[Violation] = []
    facts = _replay_verifier_facts(events_dir)
    for label in backend.node_labels():
        row = backend.node_by_label(label)
        if row is None:
            continue
        try:
            kind = NodeKind(row.kind)
        except ValueError:
            continue
        audit = _audit_pass_count(row, kind, facts)
        if audit != row.pass_count:
            out.append(
                Violation(
                    "C_pass_count_drift",
                    f"node {label!r}: stored pass_count={row.pass_count}, audit={audit}",
                    {"label": label, "stored": row.pass_count, "audit": audit},
                )
            )
    return out


def _audit_pass_count(
    row: "RawNodeRow", kind: NodeKind, facts: list[dict[str, Any]]
) -> int:
    if kind in PROOF_REQUIRING_KINDS and not (row.proof or "").strip():
        return -1
    matching = [
        f
        for f in facts
        if f.get("target") == row.label
        and f.get("payload", {}).get("verification_hash") == row.verification_hash
    ]
    if not matching:
        return 0
    # Mirror projector semantics (§5.5.1): a gap/critical verdict resets
    # pass_count to -1, and the next accepted verdict sets it to 1 (not
    # prior+1). So the audit value is the count of consecutive accepted
    # verdicts at the tail of the list — anything before the most recent
    # gap/critical was wiped out by that reset.
    tail_accepted = 0
    for f in reversed(matching):
        verdict = f.get("payload", {}).get("verdict")
        if verdict == "accepted":
            tail_accepted += 1
        elif verdict in ("gap", "critical"):
            break
    last_verdict = matching[-1].get("payload", {}).get("verdict")
    if last_verdict in ("gap", "critical"):
        return -1
    return tail_accepted


def check_d_repair_count(events_dir: Path, backend: "KuzuBackend") -> list[Violation]:
    """D. Audit ``repair_count`` against §5.5.1 replay."""
    out: list[Violation] = []
    facts = _replay_verifier_facts(events_dir)
    for label in backend.node_labels():
        row = backend.node_by_label(label)
        if row is None:
            continue
        last_change = _statement_changing_iso_ms(events_dir, label)
        # Audit repair_count = number of gap/critical verifier events
        # since `last_change` whose AppliedEvent.status is "applied".
        audit = 0
        for f in facts:
            if f.get("target") != label:
                continue
            payload = f.get("payload", {})
            verdict = payload.get("verdict")
            if verdict not in ("gap", "critical"):
                continue
            eid = f.get("event_id", "")
            iso_ms = eid.split("-", 1)[0] if eid else ""
            if last_change and iso_ms <= last_change:
                continue
            applied = backend.applied_event(eid)
            if applied is None or applied.status.value != "applied":
                continue
            audit += 1
        if audit != row.repair_count:
            out.append(
                Violation(
                    "D_repair_count_drift",
                    f"node {label!r}: stored repair_count={row.repair_count}, audit={audit}",
                    {"label": label, "stored": row.repair_count, "audit": audit},
                )
            )
    return out


# ---------------------------------------------------------------------------
# Category E — nodes/ ↔ Kuzu rendering.
# ---------------------------------------------------------------------------
def check_e_nodes_render(
    backend: "KuzuBackend",
    nodes_dir: Path,
    *,
    repair: bool = False,
) -> list[Violation]:
    """E. Audit ``nodes/`` rendered files against Kuzu state.

    When ``repair=True``, this function also rewrites divergent files
    and deletes orphans. The repair path is idempotent — a second run
    finds no E violations.
    """
    out: list[Violation] = []
    nodes_dir.mkdir(parents=True, exist_ok=True)

    # Build the expected set: every Kuzu Node with pass_count >= 1.
    expected: dict[str, bytes] = {}
    expected_label_by_filename: dict[str, str] = {}
    for label in backend.node_labels():
        row = backend.node_by_label(label)
        if row is None or row.pass_count < 1:
            continue
        try:
            kind = NodeKind(row.kind)
        except ValueError:
            continue
        deps = backend.dependencies_of(label)
        node = _load_node_from_row(row, deps)
        try:
            fname = node_filename(node)
        except ValueError as exc:
            out.append(
                Violation(
                    "E_filename_unbuildable",
                    f"cannot derive filename for {label!r}: {exc}",
                    {"label": label},
                )
            )
            continue
        expected[fname] = render_node(node)
        expected_label_by_filename[fname] = label

    # 1. Missing or mismatching files.
    for fname, expected_bytes in expected.items():
        target = nodes_dir / fname
        if not target.is_file():
            out.append(
                Violation(
                    "E_missing_file",
                    f"nodes/{fname} missing for label {expected_label_by_filename[fname]!r} at pass_count >= 1",
                    {"filename": fname, "label": expected_label_by_filename[fname]},
                )
            )
            if repair:
                target.write_bytes(expected_bytes)
            continue
        on_disk = target.read_bytes()
        if on_disk != expected_bytes:
            out.append(
                Violation(
                    "E_content_drift",
                    f"nodes/{fname} content does not match Kuzu render for label {expected_label_by_filename[fname]!r}",
                    {"filename": fname, "label": expected_label_by_filename[fname]},
                )
            )
            if repair:
                target.write_bytes(expected_bytes)

    # 2. Orphan files.
    for f in sorted(nodes_dir.glob("*.md")):
        if f.name in expected:
            continue
        out.append(
            Violation(
                "E_orphan_file",
                f"nodes/{f.name} has no matching Kuzu node at pass_count >= 1",
                {"filename": f.name},
            )
        )
        if repair:
            f.unlink()
    return out


# ---------------------------------------------------------------------------
# Category F — events/ ↔ AppliedEvent inventory.
# ---------------------------------------------------------------------------
def check_f_inventory(events_dir: Path, backend: "KuzuBackend") -> list[Violation]:
    """F. ``events/`` SHA-256 vs ``AppliedEvent.event_sha256`` and missing files."""
    out: list[Violation] = []

    # Map every event file -> body event_id + sha.
    file_by_event_id: dict[str, Path] = {}
    sha_by_event_id: dict[str, str] = {}
    for f in _iter_event_files(events_dir):
        try:
            raw, body = read_event(f)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        eid = body.get("event_id")
        if not isinstance(eid, str):
            continue
        file_by_event_id[eid] = f
        sha_by_event_id[eid] = event_sha256(raw)

    # All AppliedEvent rows.
    res = backend._conn.execute(
        "MATCH (a:AppliedEvent) RETURN a.event_id, a.event_sha256, a.status"
    )
    while res.has_next():
        row = res.get_next()
        eid, stored_sha, status = row[0], row[1], row[2]
        path = file_by_event_id.get(eid)
        if path is None:
            out.append(
                Violation(
                    "F_event_file_missing",
                    f"AppliedEvent {eid!r} has no matching file in events/",
                    {"event_id": eid, "status": status},
                )
            )
            continue
        actual_sha = sha_by_event_id.get(eid, "")
        if actual_sha != stored_sha:
            out.append(
                Violation(
                    "F_event_sha256_mismatch",
                    f"event {eid!r}: stored sha={stored_sha[:12]}, on-disk sha={actual_sha[:12]}",
                    {
                        "event_id": eid,
                        "stored_sha256": stored_sha,
                        "on_disk_sha256": actual_sha,
                        "path": str(path),
                    },
                )
            )
    return out


# ---------------------------------------------------------------------------
# Aggregation.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class LinterReport:
    a: list[Violation]
    b: list[Violation]
    c: list[Violation]
    d: list[Violation]
    e: list[Violation]
    f: list[Violation]

    @property
    def total(self) -> int:
        return sum(len(v) for v in (self.a, self.b, self.c, self.d, self.e, self.f))

    def to_dict(self) -> dict[str, Any]:
        def _section(vs: list[Violation]) -> dict[str, Any]:
            return {"violations": [v.to_dict() for v in vs], "count": len(vs)}

        return {
            "a": _section(self.a),
            "b": _section(self.b),
            "c": _section(self.c),
            "d": _section(self.d),
            "e": _section(self.e),
            "f": _section(self.f),
            "summary": f"{self.total} violations",
        }


__all__ = [
    "LinterReport",
    "Violation",
    "check_a_event_integrity",
    "check_b_kb_structural",
    "check_c_pass_count",
    "check_d_repair_count",
    "check_e_nodes_render",
    "check_f_inventory",
]
