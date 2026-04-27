"""Generator decoder — parse Codex stdout into a validated batch.

ARCHITECTURE §6.2. The decoder is intentionally **Kuzu-free** (§4.1):
its only inputs are:

- the raw bytes of Codex stdout (already merged with stderr in the
  per-job log file, but the wrapper passes only the stdout stream)
- the dispatch context (``target``, ``mode``, ``H_rejected``,
  ``dep_statement_hashes``) coming from the job file
- ``existing_kb_view`` — a callable that answers "is this label
  present in ``nodes/*.md`` at ``pass_count >= 1``?"; the wrapper
  builds it by walking ``knowledge_base/nodes/`` once, no Kuzu needed
- ``existing_dep_hash`` — a callable that returns the
  ``statement_hash`` of an existing dep label as read from
  ``nodes/*.md`` frontmatter

Output: either a :class:`StagedBatch` ready for atomic publish, or a
:class:`DecodeError` whose ``reason`` matches the §5.2 rejection
table. Wrappers map :class:`DecodeError` to a single line in
``runtime/state/rejected_writes.jsonl``.

**Failure modes** (PHASE1 M6 list):
1. malformed ``<node>`` block
2. ``kind: external_theorem``  (user-only)
3. wrong label-prefix ↔ kind pairing
4. placeholder label
5. duplicate label within batch
6. batch ``target`` not in ``nodes[]`` or mismatches dispatch target
7. existing non-target label in ``nodes[]`` (write-scope invariant)
8. self-reference
9. unresolved ``\\ref{}``
10. batch-internal cycle
11. repair-must-change-hash (mode=repair only)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

import yaml

from common.kb.hashing import DepRef, statement_hash, verification_hash
from common.kb.types import KIND_PREFIX, LABEL_SLUG_RE, NodeKind, PLACEHOLDER_LABELS


# Decoder reasons — keep aligned with §5.2 + PHASE1 §M6.
REASON_MALFORMED_NODE = "malformed_node"
REASON_FORBIDDEN_KIND = "forbidden_kind"
REASON_PREFIX_KIND_MISMATCH = "prefix_kind_mismatch"
REASON_PLACEHOLDER_LABEL = "placeholder_label"
REASON_DUPLICATE_LABEL = "duplicate_label_in_batch"
REASON_TARGET_MISMATCH = "target_mismatch"
REASON_EXISTING_NON_TARGET = "existing_non_target_label"
REASON_SELF_REFERENCE = "self_reference"
REASON_REF_UNRESOLVED = "ref_unresolved"
REASON_CYCLE = "cycle"
REASON_REPAIR_NO_CHANGE = "repair_no_change"
REASON_NO_NODES = "no_nodes_in_batch"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class StagedNode:
    """One ``<node>`` block, fully validated, with computed hashes."""

    label: str
    kind: NodeKind
    statement: str
    proof: str
    remark: str
    source_note: str
    statement_hash: str
    verification_hash: str
    depends_on: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StagedBatch:
    target: str
    mode: str
    nodes: tuple[StagedNode, ...]


class DecodeError(Exception):
    """Raised when decoder rejects a batch. Carries a §5.2 ``reason`` code."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def decode_codex_stdout(
    raw: str,
    *,
    target: str,
    mode: str,
    h_rejected: str | None = None,
    existing_label_present: Callable[[str], bool],
    existing_dep_hash: Callable[[str], str | None],
) -> StagedBatch:
    """Parse + validate ``raw`` Codex stdout into a :class:`StagedBatch`.

    ``mode`` must be ``"fresh"`` or ``"repair"``. ``h_rejected`` is
    required iff ``mode == "repair"``.
    """
    if mode not in {"fresh", "repair"}:
        raise DecodeError("schema", f"invalid mode {mode!r}")
    if mode == "repair" and not h_rejected:
        raise DecodeError("schema", "repair mode requires H_rejected")

    cleaned = _strip_ansi(raw)
    blocks = _extract_node_blocks(cleaned)
    if not blocks:
        raise DecodeError(REASON_NO_NODES, "no <node> blocks parsed from stdout")

    parsed = []
    for blk in blocks:
        parsed.append(_parse_block(blk))

    # Per-node admission checks.
    labels_seen: set[str] = set()
    for entry in parsed:
        label = entry["label"]
        if label in labels_seen:
            raise DecodeError(
                REASON_DUPLICATE_LABEL, f"duplicate label {label!r} in batch"
            )
        labels_seen.add(label)
        if label in PLACEHOLDER_LABELS:
            raise DecodeError(REASON_PLACEHOLDER_LABEL, label)
        kind = _parse_kind(entry["kind"])
        if kind is NodeKind.EXTERNAL_THEOREM:
            raise DecodeError(
                REASON_FORBIDDEN_KIND, "generator may not introduce external_theorem"
            )
        _check_label_prefix(label, kind)
        # external_theorem is already excluded above so source_note rule
        # only applies to user-side; here every other kind permits empty.

    # Target presence.
    target_labels = [e["label"] for e in parsed]
    if target not in target_labels:
        raise DecodeError(
            REASON_TARGET_MISMATCH,
            f"dispatch target {target!r} missing from batch nodes",
        )

    # Write-scope invariant: every non-target label must NOT exist in
    # the local KB view (best-effort; librarian is authoritative).
    batch_label_set = set(target_labels)
    for entry in parsed:
        lbl = entry["label"]
        if lbl == target:
            continue
        if existing_label_present(lbl):
            raise DecodeError(
                REASON_EXISTING_NON_TARGET,
                f"label {lbl!r} already exists at pass_count>=1",
            )

    # Self-reference + ref resolution.
    refs_per_label: dict[str, list[str]] = {}
    for entry in parsed:
        lbl = entry["label"]
        text = entry["statement"] + "\n" + entry["proof"]
        refs = _extract_refs(text)
        if lbl in refs:
            raise DecodeError(REASON_SELF_REFERENCE, lbl)
        for ref in refs:
            if ref in batch_label_set:
                continue
            if existing_dep_hash(ref) is None:
                raise DecodeError(
                    REASON_REF_UNRESOLVED,
                    f"\\ref{{{ref}}} not found in nodes/*.md and not in batch",
                )
        refs_per_label[lbl] = refs

    # Batch-internal cycle: directed graph over batch labels only.
    _check_batch_internal_cycle(refs_per_label, batch_label_set)

    # Topologically sort by intra-batch deps so we can compute hashes
    # incrementally.
    order = _topological_order(refs_per_label, batch_label_set)

    # Hash each node.
    staged_by_label: dict[str, StagedNode] = {}
    parsed_by_label = {e["label"]: e for e in parsed}
    for lbl in order:
        entry = parsed_by_label[lbl]
        kind = _parse_kind(entry["kind"])
        deps = refs_per_label[lbl]
        dep_refs: list[DepRef] = []
        for d in deps:
            if d in staged_by_label:
                dep_refs.append(
                    DepRef(label=d, statement_hash=staged_by_label[d].statement_hash)
                )
            else:
                h = existing_dep_hash(d)
                if h is None:
                    raise DecodeError(REASON_REF_UNRESOLVED, d)
                dep_refs.append(DepRef(label=d, statement_hash=h))
        sh = statement_hash(
            label=lbl,
            kind=kind.value,
            statement=entry["statement"],
            depends_on=dep_refs,
        )
        vh = verification_hash(statement_hash_hex=sh, proof=entry["proof"])
        staged_by_label[lbl] = StagedNode(
            label=lbl,
            kind=kind,
            statement=entry["statement"],
            proof=entry["proof"],
            remark=entry["remark"],
            source_note=entry["source_note"],
            statement_hash=sh,
            verification_hash=vh,
            depends_on=tuple(deps),
        )

    # Repair-must-change-hash.
    if mode == "repair":
        target_node = staged_by_label[target]
        if target_node.verification_hash == h_rejected:
            raise DecodeError(
                REASON_REPAIR_NO_CHANGE,
                f"repair batch produced verification_hash equal to H_rejected={h_rejected[:12]}...",
            )

    # Order final batch in topological dispatch order so librarian's
    # apply path can rely on dep-before-target writes.
    nodes_tuple = tuple(staged_by_label[lbl] for lbl in order)
    return StagedBatch(target=target, mode=mode, nodes=nodes_tuple)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# H25: anchor ``<node>`` and ``</node>`` to their own lines so the regex
# does NOT match inline backtick-quoted occurrences (e.g. skill prose
# saying "assemble candidate ``<node>`` blocks for the batch"). The
# valid Phase I batch format always puts the tags on bare lines, so
# this is an honest semantic anchor, not a workaround.
#
# ``re.MULTILINE`` makes ``^`` match after every newline (and at start
# of string), so ``^<node>`` rejects an inline backtick-quoted
# occurrence but accepts the bare-line form used by every well-formed
# emission.
_NODE_BLOCK_RE = re.compile(
    r"^<node>[ \t]*\n(.*?)\n^</node>[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _extract_node_blocks(text: str) -> list[str]:
    """Find every well-formed ``<node>...</node>`` block in ``text``.

    Tolerates banner output and MCP traces by anchoring on the literal
    tags. Unterminated ``<node>`` tags are silently dropped — the
    decoder will still raise ``no_nodes_in_batch`` if NO blocks parse.
    """
    return [m.group(1).strip() for m in _NODE_BLOCK_RE.finditer(text)]


def _parse_block(block: str) -> dict[str, str]:
    """Split a block into YAML frontmatter + markdown body, return dict.

    The expected shape:
    ```
    label: lem:foo
    kind: lemma
    remark: ...
    source_note: ...
    ---
    **Statement.**
    ...
    **Proof.**
    ...
    ```
    """
    if not block:
        raise DecodeError(REASON_MALFORMED_NODE, "empty <node> block")
    parts = block.split("\n---\n", 1)
    if len(parts) != 2:
        # Allow a trailing form: "---" line followed by the body.
        parts = block.split("---", 1)
        if len(parts) != 2:
            raise DecodeError(
                REASON_MALFORMED_NODE,
                "missing '---' divider between frontmatter and body",
            )
    head, body = parts[0].strip(), parts[1].strip()
    try:
        meta = yaml.safe_load(head)
    except yaml.YAMLError as exc:
        raise DecodeError(REASON_MALFORMED_NODE, f"YAML error: {exc}") from exc
    if not isinstance(meta, dict):
        raise DecodeError(REASON_MALFORMED_NODE, "frontmatter must be a mapping")

    label = meta.get("label")
    kind = meta.get("kind")
    if not isinstance(label, str) or not label:
        raise DecodeError(REASON_MALFORMED_NODE, "missing label")
    if not isinstance(kind, str) or not kind:
        raise DecodeError(REASON_MALFORMED_NODE, "missing kind")

    statement = _extract_section(body, "Statement.")
    proof = _extract_section(body, "Proof.") or ""
    remark = _extract_section(body, "Remark.") or meta.get("remark") or ""
    source_note = (
        _extract_section(body, "Source Note.") or meta.get("source_note") or ""
    )
    if not statement.strip():
        raise DecodeError(REASON_MALFORMED_NODE, f"node {label!r} missing Statement")

    return {
        "label": label,
        "kind": kind,
        "statement": _normalise_text(statement),
        "proof": _normalise_text(proof),
        "remark": _normalise_text(remark),
        "source_note": _normalise_text(source_note),
    }


# H26: accept ``**Statement.** Body...`` on the same line as well as
# ``**Statement.**\nBody...``. The original ``\s*\n+`` after the
# heading required the body to start on a *new* line, but real codex
# output frequently puts the first sentence on the same line as the
# heading. Both forms are equally readable in markdown.
_SECTION_RE_TPL = r"\*\*{name}\*\*[ \t]*\n*(.*?)(?=\n\*\*[A-Z][^*]*\*\*|\Z)"


def _extract_section(body: str, name: str) -> str:
    pattern = re.compile(_SECTION_RE_TPL.format(name=re.escape(name)), re.DOTALL)
    m = pattern.search(body)
    if not m:
        return ""
    return m.group(1).strip()


def _normalise_text(s: str) -> str:
    return unicodedata.normalize("NFC", s).replace("\r\n", "\n").replace("\r", "\n").strip()


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def _parse_kind(raw: str) -> NodeKind:
    try:
        return NodeKind(raw)
    except ValueError as exc:
        raise DecodeError(REASON_PREFIX_KIND_MISMATCH, f"unknown kind {raw!r}") from exc


def _check_label_prefix(label: str, kind: NodeKind) -> None:
    expected = KIND_PREFIX[kind]
    if ":" not in label:
        raise DecodeError(REASON_PREFIX_KIND_MISMATCH, f"label {label!r} missing prefix:slug")
    prefix, _, slug = label.partition(":")
    if prefix != expected:
        raise DecodeError(
            REASON_PREFIX_KIND_MISMATCH,
            f"label {label!r} prefix mismatch (kind={kind.value} requires {expected})",
        )
    if not slug or not LABEL_SLUG_RE.match(slug):
        raise DecodeError(REASON_MALFORMED_NODE, f"label {label!r} has invalid slug")


def _extract_refs(text: str) -> list[str]:
    seen: list[str] = []
    for m in re.finditer(r"\\ref\{([^}]+)\}", text):
        lbl = m.group(1).strip()
        if lbl and lbl not in seen:
            seen.append(lbl)
    return seen


def _check_batch_internal_cycle(
    refs_per_label: dict[str, list[str]], batch_labels: set[str]
) -> None:
    """DFS over edges that stay inside the batch; raise on first cycle."""
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {lbl: WHITE for lbl in batch_labels}

    def dfs(start: str) -> None:
        path: list[str] = [start]
        # iterator stack: each entry is an iterator over the *batch-internal*
        # neighbours of the corresponding ``path`` entry.
        iters: list[iter] = [iter(_internal_refs(refs_per_label, batch_labels, start))]
        color[start] = GREY
        while path:
            try:
                nxt = next(iters[-1])
            except StopIteration:
                color[path[-1]] = BLACK
                path.pop()
                iters.pop()
                continue
            c = color.get(nxt, WHITE)
            if c == GREY:
                # Cycle — find where ``nxt`` appears in path.
                idx = path.index(nxt)
                cycle = path[idx:] + [nxt]
                raise DecodeError(REASON_CYCLE, "batch-internal cycle: " + " -> ".join(cycle))
            if c == WHITE:
                color[nxt] = GREY
                path.append(nxt)
                iters.append(iter(_internal_refs(refs_per_label, batch_labels, nxt)))

    for lbl in batch_labels:
        if color.get(lbl, WHITE) == WHITE:
            dfs(lbl)


def _internal_refs(
    refs_per_label: dict[str, list[str]], batch_labels: set[str], lbl: str
) -> list[str]:
    return [r for r in refs_per_label.get(lbl, []) if r in batch_labels]


def _topological_order(
    refs_per_label: dict[str, list[str]], batch_labels: set[str]
) -> list[str]:
    """Kahn-style topological sort over intra-batch edges only.

    Edges to non-batch labels are ignored. Caller has already verified
    acyclicity.
    """
    # Edges as ``lbl -> ref`` meaning "lbl needs ref first"; ref appears
    # before lbl in the order. ``in_count[lbl]`` = number of intra-batch
    # deps of ``lbl``; ``parents[r]`` = labels that depend on ``r``.
    in_count: dict[str, int] = {lbl: 0 for lbl in batch_labels}
    parents: dict[str, list[str]] = {lbl: [] for lbl in batch_labels}
    for lbl, refs in refs_per_label.items():
        for r in refs:
            if r in batch_labels:
                in_count[lbl] += 1
                parents[r].append(lbl)
    ready = sorted([lbl for lbl, c in in_count.items() if c == 0])
    order: list[str] = []
    while ready:
        nxt = ready.pop(0)
        order.append(nxt)
        for child in parents[nxt]:
            in_count[child] -= 1
            if in_count[child] == 0:
                ready.append(child)
        ready.sort()
    if len(order) != len(batch_labels):
        # Should be unreachable thanks to _check_batch_internal_cycle.
        raise DecodeError(REASON_CYCLE, "internal cycle (topo)")
    return order


__all__ = [
    "DecodeError",
    "REASON_CYCLE",
    "REASON_DUPLICATE_LABEL",
    "REASON_EXISTING_NON_TARGET",
    "REASON_FORBIDDEN_KIND",
    "REASON_MALFORMED_NODE",
    "REASON_NO_NODES",
    "REASON_PLACEHOLDER_LABEL",
    "REASON_PREFIX_KIND_MISMATCH",
    "REASON_REF_UNRESOLVED",
    "REASON_REPAIR_NO_CHANGE",
    "REASON_SELF_REFERENCE",
    "REASON_TARGET_MISMATCH",
    "StagedBatch",
    "StagedNode",
    "decode_codex_stdout",
]
