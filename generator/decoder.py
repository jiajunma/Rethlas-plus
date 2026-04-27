"""Generator decoder — parse Codex stdout into a validated batch.

ARCHITECTURE §6.2. The decoder is intentionally **Kuzu-free** (§4.1)
and **structural-only** (H29). It only rejects when the parsed bytes
literally cannot be turned into a writable ``generator.batch_committed``
event payload. Every *content* admissibility judgment — forbidden
kind, prefix-kind mismatch, placeholder labels, existing non-target
labels, self-reference, unresolved ``\\ref{}``, batch-internal cycles
— is the verifier's responsibility (per ARCH §3.1.6 H29 boundary
revision): the verifier reads the admitted node and emits
``verdict=gap`` with ``external_reference_checks[].status =
"missing_from_nodes"`` (or similar) plus a ``repair_hint`` that the
generator's next attempt receives. The librarian projector still
guards *physical KB integrity* (label uniqueness, kind immutability,
hash chain, real DAG cycles among existing edges); it no longer
rejects on missing-ref or self-ref content patterns.

Inputs:

- the raw bytes of Codex stdout (already merged with stderr in the
  per-job log file, but the wrapper passes only the stdout stream)
- the dispatch context (``target``, ``mode``, ``H_rejected``)
  coming from the job file
- ``existing_label_present`` / ``existing_dep_hash`` — best-effort
  callables that let the decoder compute the target's
  ``verification_hash`` so the ``repair_no_change`` invariant can
  fire on repair-mode runs. Missing entries are tolerated (used as
  empty-string dep hashes); the projector is authoritative for
  admissibility.

Output: either a :class:`StagedBatch` ready for atomic publish, or a
:class:`DecodeError` whose ``reason`` is one of the five structural
codes below.

**Failure modes** (post-H29 — only structural):
1. ``malformed_node`` — YAML / section parse failure or invalid slug
2. ``no_nodes_in_batch`` — stdout produced no parseable ``<node>`` blocks
3. ``duplicate_label_in_batch`` — two non-identical blocks share a
   label (byte-identical duplicates are auto-collapsed; H27)
4. ``target_mismatch`` — dispatch target is not among the parsed labels
5. ``repair_no_change`` — repair-mode batch produced a target
   ``verification_hash`` equal to ``H_rejected`` (state-machine guard)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

import yaml

from common.kb.hashing import DepRef, statement_hash, verification_hash
from common.kb.types import KIND_PREFIX, LABEL_SLUG_RE, NodeKind


# Decoder reasons — only the five structural codes survive the H29
# boundary revision (ARCH §3.1.6). Anything that is "the agent wrote
# something semantically inadmissible" lands at the librarian projector
# (physical-integrity rejections like label_conflict) or at the
# verifier (content gaps like missing references).
REASON_MALFORMED_NODE = "malformed_node"
REASON_DUPLICATE_LABEL = "duplicate_label_in_batch"
REASON_TARGET_MISMATCH = "target_mismatch"
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
    """Raised when decoder rejects a batch. Carries a §5.2 ``reason`` code.

    ``parsed_blocks`` (H29 phase A-2) preserves the raw ``<node>`` block
    dicts (``label``, ``kind``, ``statement``, ``proof``, ``remark``,
    ``source_note``) the decoder managed to extract before failing, so
    the wrapper can record them in ``rejected_writes.jsonl`` and the
    next attempt's prompt has a draft to repair against. May be empty
    when the batch failed before any block parsed cleanly (e.g.
    ``no_nodes_in_batch``).
    """

    def __init__(
        self,
        reason: str,
        detail: str,
        parsed_blocks: tuple[dict, ...] = (),
    ) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.parsed_blocks = parsed_blocks


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

    parsed: list[dict] = []
    try:
        for blk in blocks:
            parsed.append(_parse_block(blk))
    except DecodeError as exc:
        # H29 phase A-2: surface every block we *did* manage to parse so
        # the wrapper can persist them in ``rejected_writes.jsonl`` and
        # the next attempt's prompt has a draft to repair against.
        if not exc.parsed_blocks:
            exc.parsed_blocks = tuple(parsed)
        raise

    # H27: codex frequently emits the same node twice — once during
    # its reasoning ("here's my draft") and once as the final emission
    # ("emitting batch") — with byte-identical content. The agent
    # didn't make a real mistake; the duplicate is an artifact of
    # codex echoing its own reasoning into stdout. Treat
    # content-identical same-label blocks as a single emission (keep
    # the last; semantically they're indistinguishable). Only
    # genuinely different-content collisions still raise
    # ``duplicate_label_in_batch`` below.
    parsed = _dedupe_identical_blocks(parsed)

    # H29 phase A-2: anything that raises beyond this point already has
    # the full ``parsed`` list of admitted block dicts; thread it onto
    # any propagating ``DecodeError`` so the wrapper can persist them.
    try:
        return _decode_validate_and_stage(
            parsed=parsed,
            target=target,
            mode=mode,
            h_rejected=h_rejected,
            existing_dep_hash=existing_dep_hash,
        )
    except DecodeError as exc:
        if not exc.parsed_blocks:
            exc.parsed_blocks = tuple(parsed)
        raise


def _decode_validate_and_stage(
    *,
    parsed: list[dict],
    target: str,
    mode: str,
    h_rejected: str | None,
    existing_dep_hash: Callable[[str], str | None],
) -> StagedBatch:

    # Per-node structural shape check (H29: only duplicate-label).
    # Forbidden-kind / prefix-mismatch / placeholder-label / existing-
    # non-target-label are all CONTENT judgments — the verifier and
    # the projector handle them. The decoder only rejects when the
    # batch literally cannot be assembled into a writable payload.
    labels_seen: set[str] = set()
    for entry in parsed:
        label = entry["label"]
        if label in labels_seen:
            raise DecodeError(
                REASON_DUPLICATE_LABEL, f"duplicate label {label!r} in batch"
            )
        labels_seen.add(label)
        # ``_parse_kind`` is structural — without a known kind we can't
        # construct the StagedNode at all. An unknown kind is recorded
        # as ``malformed_node`` (the YAML body was syntactically a
        # mapping but ``kind: <bad>`` is not a NodeKind enum value).
        _parse_kind(entry["kind"])

    # Target presence — without the target in the batch, the wrapper
    # cannot fill ``payload.target`` correctly. This is structural.
    target_labels = [e["label"] for e in parsed]
    if target not in target_labels:
        raise DecodeError(
            REASON_TARGET_MISMATCH,
            f"dispatch target {target!r} missing from batch nodes",
        )

    # Compute refs per label (informational only — used to compute
    # statement_hash and to expose ``depends_on`` on each StagedNode).
    # Self-reference and unresolved refs are NOT errors here; the
    # verifier (only) judges content quality and emits ``verdict=gap``
    # with ``external_reference_checks[].status="missing_from_nodes"``.
    batch_label_set = set(target_labels)
    refs_per_label: dict[str, list[str]] = {}
    for entry in parsed:
        text = entry["statement"] + "\n" + entry["proof"]
        refs_per_label[entry["label"]] = _extract_refs(text)

    # Hash each node in topological order so intra-batch refs see the
    # freshly computed dep hash. ``_safe_topological_order`` falls back
    # to insertion order when a real cycle exists in intra-batch refs;
    # the projector will catch genuine DAG cycles against the existing
    # graph at apply time. Self-loops (label refs itself) are simply
    # ignored when ordering — the verifier flags them as content
    # issues, and the hash treats a self-ref like an unresolved dep
    # (empty string), which is harmless because the verifier will say
    # ``gap`` regardless and the agent will repair.
    order = _safe_topological_order(refs_per_label, batch_label_set)

    staged_by_label: dict[str, StagedNode] = {}
    parsed_by_label = {e["label"]: e for e in parsed}
    for lbl in order:
        entry = parsed_by_label[lbl]
        kind = _parse_kind(entry["kind"])
        deps = refs_per_label[lbl]
        dep_refs: list[DepRef] = []
        for d in deps:
            if d == lbl:
                # Self-ref: hash with empty dep — the verifier will
                # flag the recursive reference as a content gap.
                dep_refs.append(DepRef(label=d, statement_hash=""))
                continue
            if d in staged_by_label:
                dep_refs.append(
                    DepRef(label=d, statement_hash=staged_by_label[d].statement_hash)
                )
                continue
            h = existing_dep_hash(d)
            # Missing dep is no longer a decoder error (H29). The
            # statement_hash includes an empty string for the missing
            # dep; the verifier will emit ``missing_from_nodes`` and
            # the agent's repair attempt will rebuild this node once
            # the dep is defined elsewhere.
            dep_refs.append(DepRef(label=d, statement_hash=h or ""))
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

    # Repair-must-change-hash. Worker-contract guard: a repair attempt
    # that re-emits the same proof would loop forever, so it is a
    # state-machine error rather than a content judgment.
    if mode == "repair":
        target_node = staged_by_label[target]
        if target_node.verification_hash == h_rejected:
            raise DecodeError(
                REASON_REPAIR_NO_CHANGE,
                f"repair batch produced verification_hash equal to H_rejected={h_rejected[:12]}...",
            )

    # Order final batch in topological dispatch order so librarian's
    # apply path can rely on dep-before-target writes when refs do
    # resolve inside the batch.
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


def _dedupe_identical_blocks(parsed: list[dict[str, str]]) -> list[dict[str, str]]:
    """Collapse byte-identical duplicates emitted under the same label.

    Codex sometimes echoes its draft batch and its final batch into
    the same stdout stream, producing two ``<node>...</node>`` blocks
    with the same label and the same body. The original strict check
    rejected the batch as ``duplicate_label_in_batch`` even though
    the two blocks carry no semantic disagreement. We preserve that
    strictness when the bodies actually differ — that is a real
    contract violation — but treat identical-body duplicates as a
    single emission (the last copy wins). Order is preserved by the
    label's first occurrence so downstream consumers see a stable list.
    """
    first_index_for_label: dict[str, int] = {}
    result: list[dict[str, str]] = []
    for entry in parsed:
        label = entry["label"]
        prior_idx = first_index_for_label.get(label)
        if prior_idx is None:
            first_index_for_label[label] = len(result)
            result.append(entry)
            continue
        if _entries_byte_equal(result[prior_idx], entry):
            # Same content — replace the prior copy in-place so the
            # last-emitted version wins while preserving its slot.
            result[prior_idx] = entry
        else:
            # Different content — preserve both so the caller's
            # duplicate-label check fires the correct rejection reason.
            result.append(entry)
    return result


def _entries_byte_equal(a: dict[str, str], b: dict[str, str]) -> bool:
    fields = ("kind", "statement", "proof", "remark", "source_note")
    return all(a.get(f, "") == b.get(f, "") for f in fields)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def _parse_kind(raw: str) -> NodeKind:
    # Unknown kind is structural — without a valid NodeKind enum
    # value the wrapper cannot construct the staged node payload.
    # Recorded as ``malformed_node`` (post-H29) since the YAML body
    # was syntactically a mapping but the ``kind`` slot was unusable.
    try:
        return NodeKind(raw)
    except ValueError as exc:
        raise DecodeError(REASON_MALFORMED_NODE, f"unknown kind {raw!r}") from exc


def _extract_refs(text: str) -> list[str]:
    seen: list[str] = []
    for m in re.finditer(r"\\ref\{([^}]+)\}", text):
        lbl = m.group(1).strip()
        if lbl and lbl not in seen:
            seen.append(lbl)
    return seen


def _safe_topological_order(
    refs_per_label: dict[str, list[str]], batch_labels: set[str]
) -> list[str]:
    """Kahn-style topological sort over intra-batch edges, cycle-tolerant.

    H29: a real intra-batch cycle is no longer a decoder error — the
    librarian projector catches genuine DAG cycles when the graph
    closes against existing nodes, and the verifier flags suspicious
    self / cyclic references as content gaps. When the intra-batch
    sub-graph contains a cycle (or a self-loop), Kahn's algorithm
    leaves some nodes unconsumed; we append those in their original
    insertion order so every parsed node ends up in the dispatch.
    Self-loops are excluded from the in-count so a node referencing
    itself does not block its own admission.
    """
    in_count: dict[str, int] = {lbl: 0 for lbl in batch_labels}
    parents: dict[str, list[str]] = {lbl: [] for lbl in batch_labels}
    for lbl, refs in refs_per_label.items():
        for r in refs:
            if r in batch_labels and r != lbl:
                in_count[lbl] += 1
                parents[r].append(lbl)

    insertion_order = list(refs_per_label.keys())
    ready = sorted([lbl for lbl, c in in_count.items() if c == 0])
    seen: set[str] = set()
    order: list[str] = []
    while ready:
        nxt = ready.pop(0)
        if nxt in seen:
            continue
        seen.add(nxt)
        order.append(nxt)
        for child in parents.get(nxt, ()):
            in_count[child] -= 1
            if in_count[child] == 0 and child not in seen:
                ready.append(child)
        ready.sort()
    # Cycle remnant: append leftovers in original insertion order so
    # the projector still sees every parsed node.
    for lbl in insertion_order:
        if lbl not in seen:
            order.append(lbl)
            seen.add(lbl)
    return order


__all__ = [
    "DecodeError",
    "REASON_DUPLICATE_LABEL",
    "REASON_MALFORMED_NODE",
    "REASON_NO_NODES",
    "REASON_REPAIR_NO_CHANGE",
    "REASON_TARGET_MISMATCH",
    "StagedBatch",
    "StagedNode",
    "decode_codex_stdout",
]
