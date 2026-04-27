"""M6 — generator decoder unit tests (post-H29).

Covers the 5 structural failure modes the decoder still owns and the
happy paths. Per ARCH §3.1.6 H29 boundary revision the decoder no
longer judges *content*: forbidden kind, prefix-kind mismatch,
placeholder labels, existing non-target labels, self-reference,
unresolved ``\\ref{}``, and batch-internal cycles are all admitted by
the decoder and judged downstream — physical-integrity violations by
the librarian projector (REASON_LABEL_CONFLICT, REASON_CYCLE,
REASON_KIND_MUTATION, REASON_HASH_MISMATCH); content gaps by the
verifier (``verdict=gap``, ``external_reference_checks``).
"""

from __future__ import annotations

from typing import Mapping

import pytest

from common.kb.hashing import DepRef, statement_hash, verification_hash
from generator.decoder import (
    DecodeError,
    REASON_DUPLICATE_LABEL,
    REASON_MALFORMED_NODE,
    REASON_NO_NODES,
    REASON_REPAIR_NO_CHANGE,
    REASON_TARGET_MISMATCH,
    StagedBatch,
    decode_codex_stdout,
)


# Convenience builders ------------------------------------------------------
def block(label: str, kind: str, statement: str, proof: str = "", **extra: str) -> str:
    head = f"label: {label}\nkind: {kind}\n"
    body_parts = []
    if statement:
        body_parts.append(f"**Statement.**\n\n{statement}")
    if proof:
        body_parts.append(f"**Proof.**\n\n{proof}")
    if extra.get("remark"):
        body_parts.insert(0, f"**Remark.**\n\n{extra['remark']}")
    if extra.get("source_note"):
        body_parts.insert(0, f"**Source Note.**\n\n{extra['source_note']}")
    body = "\n\n".join(body_parts)
    return f"<node>\n{head}---\n{body}\n</node>"


def _kb_view(existing: Mapping[str, str]):
    """Return ``(label_present, dep_hash)`` for an in-memory ``nodes/`` view."""
    return (lambda lbl: lbl in existing, lambda lbl: existing.get(lbl))


# Happy paths ---------------------------------------------------------------
def test_single_node_fresh_batch() -> None:
    raw = block("thm:goal", "theorem", "S", "P")
    label_present, dep_hash = _kb_view({})
    batch = decode_codex_stdout(
        raw,
        target="thm:goal",
        mode="fresh",
        existing_label_present=label_present,
        existing_dep_hash=dep_hash,
    )
    assert isinstance(batch, StagedBatch)
    assert batch.target == "thm:goal"
    assert len(batch.nodes) == 1
    assert batch.nodes[0].statement_hash
    assert batch.nodes[0].verification_hash


def test_two_node_batch_with_internal_ref() -> None:
    raw = (
        block("lem:helper_a", "lemma", "leaf", "p1")
        + "\n"
        + block("thm:goal", "theorem", r"uses \ref{lem:helper_a}", "p2")
    )
    label_present, dep_hash = _kb_view({})
    batch = decode_codex_stdout(
        raw,
        target="thm:goal",
        mode="fresh",
        existing_label_present=label_present,
        existing_dep_hash=dep_hash,
    )
    labels = [n.label for n in batch.nodes]
    # helper_a must come before goal.
    assert labels.index("lem:helper_a") < labels.index("thm:goal")


def test_existing_dep_resolved_from_kb_view() -> None:
    sh_existing = "ab" * 32
    raw = block("thm:goal", "theorem", r"uses \ref{def:x}", "p")
    label_present, dep_hash = _kb_view({"def:x": sh_existing})
    batch = decode_codex_stdout(
        raw,
        target="thm:goal",
        mode="fresh",
        existing_label_present=label_present,
        existing_dep_hash=dep_hash,
    )
    expected_sh = statement_hash(
        label="thm:goal",
        kind="theorem",
        statement=r"uses \ref{def:x}",
        depends_on=[DepRef(label="def:x", statement_hash=sh_existing)],
    )
    assert batch.nodes[0].statement_hash == expected_sh


def test_ansi_escape_codes_stripped() -> None:
    raw = "\x1b[32m" + block("thm:goal", "theorem", "S", "P") + "\x1b[0m"
    label_present, dep_hash = _kb_view({})
    batch = decode_codex_stdout(
        raw,
        target="thm:goal",
        mode="fresh",
        existing_label_present=label_present,
        existing_dep_hash=dep_hash,
    )
    assert batch.nodes[0].label == "thm:goal"


def test_mcp_trace_before_node_block_tolerated() -> None:
    raw = (
        "tool: read_node{path='thm/foo.md'}\n"
        + "[mcp] result: ...\n"
        + block("thm:goal", "theorem", "S", "P")
    )
    label_present, dep_hash = _kb_view({})
    batch = decode_codex_stdout(
        raw,
        target="thm:goal",
        mode="fresh",
        existing_label_present=label_present,
        existing_dep_hash=dep_hash,
    )
    assert len(batch.nodes) == 1


# H29 — the seven content checks the decoder no longer enforces -------------
# Each of these used to raise a dedicated DecodeError reason; now the
# batch is admitted and the verifier / projector handle the issue
# downstream. The corresponding tests below assert *admission*.


def test_h29_unresolved_ref_is_admitted() -> None:
    """H29: an unresolved ``\\ref{def:nope}`` is no longer a decoder
    rejection. The decoder hashes the target with an empty dep hash;
    the verifier downstream emits ``external_reference_checks[].status
    = "missing_from_nodes"`` and the agent's repair attempt regenerates
    the node once the dep is defined."""
    raw = block("thm:goal", "theorem", r"uses \ref{def:nope}", "p")
    label_present, dep_hash = _kb_view({})
    batch = decode_codex_stdout(
        raw,
        target="thm:goal",
        mode="fresh",
        existing_label_present=label_present,
        existing_dep_hash=dep_hash,
    )
    assert len(batch.nodes) == 1
    assert "def:nope" in batch.nodes[0].depends_on
    # Hash must include the dep with an empty statement_hash so the
    # cascade later picks up the proper hash once def:nope is defined.
    expected_sh = statement_hash(
        label="thm:goal",
        kind="theorem",
        statement=r"uses \ref{def:nope}",
        depends_on=[DepRef(label="def:nope", statement_hash="")],
    )
    assert batch.nodes[0].statement_hash == expected_sh


def test_h29_self_reference_is_admitted() -> None:
    """H29: ``\\ref{thm:goal}`` inside thm:goal's own proof is a content
    judgment for the verifier (recursive citation), not a decoder
    rejection. The decoder admits the node with an empty dep hash for
    the self-loop."""
    raw = block("thm:goal", "theorem", r"uses \ref{thm:goal}", "p")
    batch = decode_codex_stdout(
        raw,
        target="thm:goal",
        mode="fresh",
        existing_label_present=lambda x: False,
        existing_dep_hash=lambda x: None,
    )
    assert len(batch.nodes) == 1
    assert "thm:goal" in batch.nodes[0].depends_on


def test_h29_batch_internal_cycle_is_admitted() -> None:
    """H29: a cycle within the batch's intra-batch refs is no longer a
    decoder error. The projector catches genuine DAG cycles when the
    graph closes against the existing KB; the verifier flags suspicious
    citation patterns as content gaps."""
    raw = (
        block("lem:a", "lemma", r"refers \ref{lem:b}", "p")
        + "\n"
        + block("lem:b", "lemma", r"refers \ref{lem:a}", "p")
        + "\n"
        + block("thm:goal", "theorem", r"uses \ref{lem:a}", "p")
    )
    batch = decode_codex_stdout(
        raw,
        target="thm:goal",
        mode="fresh",
        existing_label_present=lambda x: False,
        existing_dep_hash=lambda x: None,
    )
    labels = [n.label for n in batch.nodes]
    assert set(labels) == {"lem:a", "lem:b", "thm:goal"}


def test_h29_external_theorem_kind_is_admitted() -> None:
    """H29: ``kind: external_theorem`` from the generator is a content
    judgment for the projector / verifier (only users may publish
    external_theorem nodes per §6.5). The decoder no longer guards it."""
    raw = block("ext:foo", "external_theorem", "S")
    batch = decode_codex_stdout(
        raw,
        target="ext:foo",
        mode="fresh",
        existing_label_present=lambda x: False,
        existing_dep_hash=lambda x: None,
    )
    assert batch.nodes[0].label == "ext:foo"


def test_h29_prefix_kind_mismatch_is_admitted() -> None:
    """H29: prefix-vs-kind mismatch (label says ``thm:`` but kind is
    ``lemma``) is now a projector concern. ``_check_label_prefix`` lives
    only on the projector side."""
    raw = block("thm:foo", "lemma", "S", "P")
    batch = decode_codex_stdout(
        raw,
        target="thm:foo",
        mode="fresh",
        existing_label_present=lambda x: False,
        existing_dep_hash=lambda x: None,
    )
    assert batch.nodes[0].label == "thm:foo"


def test_h29_placeholder_label_is_admitted() -> None:
    """H29: placeholder labels (``thm:main``, ``prop:claim1``) are no
    longer rejected by the decoder. The verifier or operator catches
    placeholder citations during content review."""
    raw = block("thm:main", "theorem", "S", "P")
    batch = decode_codex_stdout(
        raw,
        target="thm:main",
        mode="fresh",
        existing_label_present=lambda x: False,
        existing_dep_hash=lambda x: None,
    )
    assert batch.nodes[0].label == "thm:main"


def test_h29_existing_non_target_label_is_admitted() -> None:
    """H29: trying to add a brand-new node whose label collides with an
    existing verified node is the projector's REASON_LABEL_CONFLICT,
    not the decoder's job. The decoder admits both blocks."""
    raw = block("lem:exists", "lemma", "S", "P") + "\n" + block(
        "thm:goal", "theorem", "S", "P"
    )
    label_present, dep_hash = _kb_view({"lem:exists": "a" * 64})
    batch = decode_codex_stdout(
        raw,
        target="thm:goal",
        mode="fresh",
        existing_label_present=label_present,
        existing_dep_hash=dep_hash,
    )
    labels = [n.label for n in batch.nodes]
    assert "lem:exists" in labels
    assert "thm:goal" in labels


# Failure modes (the 5 structural rejections that remain) -------------------
def _expect(reason: str, raw: str, **kw):
    label_present = kw.pop("existing_label_present", lambda x: False)
    dep_hash = kw.pop("existing_dep_hash", lambda x: None)
    target = kw.pop("target", "thm:goal")
    mode = kw.pop("mode", "fresh")
    with pytest.raises(DecodeError) as ei:
        decode_codex_stdout(
            raw,
            target=target,
            mode=mode,
            existing_label_present=label_present,
            existing_dep_hash=dep_hash,
            **kw,
        )
    assert ei.value.reason == reason, ei.value.reason


def test_failure_no_nodes() -> None:
    _expect(REASON_NO_NODES, "no node blocks here")


def test_failure_malformed_yaml() -> None:
    raw = "<node>\n!!!!:::: not yaml\n---\n**Statement.**\nx\n</node>"
    _expect(REASON_MALFORMED_NODE, raw)


def test_failure_unknown_kind_is_malformed_node() -> None:
    """H29: an unknown ``kind`` value is structural (we can't construct
    the StagedNode without a NodeKind enum value) — recorded as
    ``malformed_node`` so the agent gets a clear shape error."""
    raw = block("thm:goal", "axiom", "S", "P")
    _expect(REASON_MALFORMED_NODE, raw)


def test_failure_target_missing_from_batch() -> None:
    raw = block("lem:other", "lemma", "S", "P")
    _expect(REASON_TARGET_MISMATCH, raw)


def test_failure_repair_no_change() -> None:
    raw = block("thm:goal", "theorem", "S", "P")
    sh = statement_hash(label="thm:goal", kind="theorem", statement="S", depends_on=[])
    vh = verification_hash(statement_hash_hex=sh, proof="P")
    _expect(REASON_REPAIR_NO_CHANGE, raw, mode="repair", h_rejected=vh)


def test_repair_mode_with_changed_proof_succeeds() -> None:
    raw = block("thm:goal", "theorem", "S", "new proof")
    label_present, dep_hash = _kb_view({})
    batch = decode_codex_stdout(
        raw,
        target="thm:goal",
        mode="repair",
        h_rejected="d" * 64,
        existing_label_present=label_present,
        existing_dep_hash=dep_hash,
    )
    assert batch.mode == "repair"


# H27 — byte-identical duplicates collapse; genuine label clashes still reject
def test_byte_identical_duplicate_blocks_collapse_to_one() -> None:
    blk = block("thm:goal", "theorem", "S", "P")
    raw = blk + "\n" + blk
    batch = decode_codex_stdout(
        raw,
        target="thm:goal",
        mode="fresh",
        existing_label_present=lambda x: False,
        existing_dep_hash=lambda x: None,
    )
    assert len(batch.nodes) == 1
    assert batch.nodes[0].label == "thm:goal"


def test_label_clash_with_different_content_still_rejects() -> None:
    raw = (
        block("thm:goal", "theorem", "S1", "P1")
        + "\n"
        + block("thm:goal", "theorem", "S2", "P2")
    )
    _expect(REASON_DUPLICATE_LABEL, raw)


def test_inline_backtick_node_text_is_skipped() -> None:
    raw = (
        "Here are my plan steps:\n"
        "8. Assemble candidate `<node>` blocks for the batch.\n"
        "   The blocks must satisfy the §6.2 batch contract: every \n"
        "   reference must resolve inside the batch or to an existing \n"
        "   verified node; intra-batch references form a DAG.\n"
        "\n"
        + block("thm:goal", "theorem", "S1", "P1")
        + "\n"
    )
    batch = decode_codex_stdout(
        raw,
        target="thm:goal",
        mode="fresh",
        existing_label_present=lambda x: False,
        existing_dep_hash=lambda x: None,
    )
    assert len(batch.nodes) == 1
    assert batch.nodes[0].label == "thm:goal"


# H29 phase A-2 — parsed-block capture on rejection
def test_h29_target_mismatch_carries_parsed_blocks() -> None:
    """A structurally-rejected batch still surfaces every block the
    decoder did parse so the wrapper can persist them in
    ``rejected_writes.jsonl`` and the next attempt's prompt has a draft
    to repair against."""
    raw = (
        block("lem:helper", "lemma", "Sh", "Ph")
        + "\n"
        + block("lem:other", "lemma", "So", "Po")
    )
    label_present, dep_hash = _kb_view({})
    with pytest.raises(DecodeError) as ei:
        decode_codex_stdout(
            raw,
            target="thm:goal",
            mode="fresh",
            existing_label_present=label_present,
            existing_dep_hash=dep_hash,
        )
    assert ei.value.reason == REASON_TARGET_MISMATCH
    captured_labels = [b["label"] for b in ei.value.parsed_blocks]
    assert captured_labels == ["lem:helper", "lem:other"]


def test_h29_no_nodes_has_empty_parsed_blocks() -> None:
    """``no_nodes_in_batch`` happens before any block parsed — the
    wrapper records an empty ``parsed_blocks`` list so it can fall back
    to the raw codex log for repair context."""
    label_present, dep_hash = _kb_view({})
    with pytest.raises(DecodeError) as ei:
        decode_codex_stdout(
            "no node blocks here at all",
            target="thm:goal",
            mode="fresh",
            existing_label_present=label_present,
            existing_dep_hash=dep_hash,
        )
    assert ei.value.reason == REASON_NO_NODES
    assert ei.value.parsed_blocks == ()
