"""M6 — generator decoder unit tests.

Covers the 11 failure modes from PHASE1 §M6 + happy-path batch staging.
The decoder is pure, so all tests run in-process without subprocesses.
"""

from __future__ import annotations

from typing import Mapping

import pytest

from common.kb.hashing import DepRef, statement_hash, verification_hash
from common.kb.types import NodeKind
from generator.decoder import (
    DecodeError,
    REASON_CYCLE,
    REASON_DUPLICATE_LABEL,
    REASON_EXISTING_NON_TARGET,
    REASON_FORBIDDEN_KIND,
    REASON_MALFORMED_NODE,
    REASON_NO_NODES,
    REASON_PLACEHOLDER_LABEL,
    REASON_PREFIX_KIND_MISMATCH,
    REASON_REF_UNRESOLVED,
    REASON_REPAIR_NO_CHANGE,
    REASON_SELF_REFERENCE,
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
    """Return ``(label_present, dep_hash)`` for an in-memory ``nodes/`` view.

    ``existing`` maps label → statement_hash.
    """
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
    # Computed statement_hash must use the existing dep's hash.
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


# Failure modes -------------------------------------------------------------
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


def test_failure_external_theorem_kind() -> None:
    raw = block("ext:foo", "external_theorem", "S")
    _expect(REASON_FORBIDDEN_KIND, raw, target="ext:foo")


def test_failure_prefix_kind_mismatch() -> None:
    raw = block("thm:foo", "lemma", "S", "P")
    _expect(REASON_PREFIX_KIND_MISMATCH, raw, target="thm:foo")


def test_failure_placeholder_label() -> None:
    raw = block("thm:main", "theorem", "S", "P")
    _expect(REASON_PLACEHOLDER_LABEL, raw, target="thm:main")


def test_failure_duplicate_label() -> None:
    raw = block("thm:goal", "theorem", "S1", "P1") + "\n" + block(
        "thm:goal", "theorem", "S2", "P2"
    )
    _expect(REASON_DUPLICATE_LABEL, raw)


def test_failure_target_missing_from_batch() -> None:
    raw = block("lem:other", "lemma", "S", "P")
    _expect(REASON_TARGET_MISMATCH, raw)


def test_failure_existing_non_target_label() -> None:
    raw = block("lem:exists", "lemma", "S", "P") + "\n" + block(
        "thm:goal", "theorem", "S", "P"
    )
    label_present, dep_hash = _kb_view({"lem:exists": "a" * 64})
    _expect(
        REASON_EXISTING_NON_TARGET,
        raw,
        existing_label_present=label_present,
        existing_dep_hash=dep_hash,
    )


def test_failure_self_reference() -> None:
    raw = block("thm:goal", "theorem", r"uses \ref{thm:goal}", "p")
    _expect(REASON_SELF_REFERENCE, raw)


def test_failure_unresolved_ref() -> None:
    raw = block("thm:goal", "theorem", r"uses \ref{def:nope}", "p")
    _expect(REASON_REF_UNRESOLVED, raw)


def test_failure_batch_internal_cycle() -> None:
    raw = (
        block("lem:a", "lemma", r"refers \ref{lem:b}", "p")
        + "\n"
        + block("lem:b", "lemma", r"refers \ref{lem:a}", "p")
        + "\n"
        + block("thm:goal", "theorem", r"uses \ref{lem:a}", "p")
    )
    _expect(REASON_CYCLE, raw)


def test_failure_repair_no_change() -> None:
    raw = block("thm:goal", "theorem", "S", "P")
    # Compute the verification_hash that the staged batch would produce
    # so the test forces a clash.
    sh = statement_hash(label="thm:goal", kind="theorem", statement="S", depends_on=[])
    vh = verification_hash(statement_hash_hex=sh, proof="P")
    _expect(REASON_REPAIR_NO_CHANGE, raw, mode="repair", h_rejected=vh)


def test_repair_mode_with_changed_proof_succeeds() -> None:
    """Repair must change verification_hash; trivially achieved by a different proof."""
    raw = block("thm:goal", "theorem", "S", "new proof")
    label_present, dep_hash = _kb_view({})
    batch = decode_codex_stdout(
        raw,
        target="thm:goal",
        mode="repair",
        h_rejected="d" * 64,  # different from the new proof's hash
        existing_label_present=label_present,
        existing_dep_hash=dep_hash,
    )
    assert batch.mode == "repair"
