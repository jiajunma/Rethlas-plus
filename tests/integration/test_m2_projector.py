"""M2 — projector end-to-end: every event type + every apply_failed reason."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from common.kb.kuzu_backend import KuzuBackend
from common.kb.types import ApplyOutcome
from librarian.projector import (
    REASON_CYCLE,
    REASON_HASH_MISMATCH,
    REASON_HINT_TARGET_MISSING,
    REASON_HINT_TARGET_UNREACHABLE,
    REASON_KIND_MUTATION,
    REASON_LABEL_CONFLICT,
    REASON_REF_MISSING,
    REASON_SELF_REFERENCE,
    Projector,
)


# ---------------------------------------------------------------------------
# Event fixture helpers.
# ---------------------------------------------------------------------------
def _event(
    *,
    eid: str,
    etype: str,
    actor: str,
    target: str | None = None,
    payload: dict[str, Any] | None = None,
    ts: str = "2026-04-25T12:00:00.000+00:00",
) -> tuple[dict[str, Any], bytes]:
    body = {
        "event_id": eid,
        "type": etype,
        "actor": actor,
        "ts": ts,
        "payload": payload or {},
    }
    if target is not None:
        body["target"] = target
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return body, raw


def _node_added(
    *,
    eid: str,
    target: str,
    kind: str,
    statement: str,
    proof: str = "",
    actor: str = "user:alice",
    remark: str = "",
    source_note: str = "",
) -> tuple[dict[str, Any], bytes]:
    return _event(
        eid=eid,
        etype="user.node_added",
        actor=actor,
        target=target,
        payload={
            "kind": kind,
            "statement": statement,
            "proof": proof,
            "remark": remark,
            "source_note": source_note,
        },
    )


@pytest.fixture
def kb(tmp_path: Path) -> KuzuBackend:
    backend = KuzuBackend(tmp_path / "dag.kz")
    yield backend
    backend.close()


@pytest.fixture
def projector(kb: KuzuBackend) -> Projector:
    return Projector(kb)


# ---------------------------------------------------------------------------
# Happy-path: user.node_added -> node exists at pass_count=0 (definition).
# ---------------------------------------------------------------------------
def test_user_node_added_definition(projector: Projector, kb: KuzuBackend) -> None:
    body, raw = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="def:primary_object",
        kind="definition",
        statement="A primary object is ...",
    )
    r = projector.apply(body, raw)
    assert r.status is ApplyOutcome.APPLIED
    node = kb.node_by_label("def:primary_object")
    assert node is not None
    assert node.kind == "definition"
    assert node.pass_count == 0
    assert node.repair_count == 0


def test_user_node_added_lemma_with_proof(projector: Projector, kb: KuzuBackend) -> None:
    body, raw = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="lem:trivial",
        kind="lemma",
        statement="A statement",
        proof="proof text",
    )
    projector.apply(body, raw)
    node = kb.node_by_label("lem:trivial")
    assert node is not None
    assert node.pass_count == 0


def test_user_node_added_lemma_without_proof_goes_to_neg_one(
    projector: Projector, kb: KuzuBackend
) -> None:
    body, raw = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="lem:unproved",
        kind="lemma",
        statement="A statement",
        proof="",
    )
    projector.apply(body, raw)
    node = kb.node_by_label("lem:unproved")
    assert node is not None
    assert node.pass_count == -1


def test_user_node_added_label_conflict(projector: Projector, kb: KuzuBackend) -> None:
    body1, raw1 = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="def:x",
        kind="definition",
        statement="...",
    )
    body2, raw2 = _node_added(
        eid="20260425T120001.000-0001-abc0123456789abd",
        target="def:x",
        kind="definition",
        statement="...",
    )
    projector.apply(body1, raw1)
    r = projector.apply(body2, raw2)
    assert r.status is ApplyOutcome.APPLY_FAILED
    assert r.reason == REASON_LABEL_CONFLICT
    # stored in AppliedEvent
    row = kb.applied_event(body2["event_id"])
    assert row is not None and row.status is ApplyOutcome.APPLY_FAILED


# ---------------------------------------------------------------------------
# Idempotent re-apply.
# ---------------------------------------------------------------------------
def test_re_apply_same_event_is_noop(projector: Projector, kb: KuzuBackend) -> None:
    body, raw = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="def:x",
        kind="definition",
        statement="...",
    )
    r1 = projector.apply(body, raw)
    r2 = projector.apply(body, raw)
    assert r1.status is ApplyOutcome.APPLIED
    assert r2.status is ApplyOutcome.APPLIED
    # still only one node
    assert kb.node_labels() == ["def:x"]


def test_re_apply_with_tampered_bytes_raises(
    projector: Projector, kb: KuzuBackend
) -> None:
    body, raw = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="def:x",
        kind="definition",
        statement="...",
    )
    projector.apply(body, raw)
    tampered = raw.replace(b"A primary", b"XXX primary") if b"A primary" in raw else raw + b" "
    with pytest.raises(Exception) as exc_info:
        projector.apply(body, tampered)
    assert "tampered" in str(exc_info.value) or "corruption" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# user.node_revised.
# ---------------------------------------------------------------------------
def _revise(
    *,
    eid: str,
    target: str,
    kind: str,
    statement: str,
    proof: str = "",
) -> tuple[dict[str, Any], bytes]:
    return _event(
        eid=eid,
        etype="user.node_revised",
        actor="user:alice",
        target=target,
        payload={
            "kind": kind,
            "statement": statement,
            "proof": proof,
            "remark": "",
            "source_note": "",
        },
    )


def test_node_revised_updates_and_resets_counts(
    projector: Projector, kb: KuzuBackend
) -> None:
    body1, raw1 = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="lem:x",
        kind="lemma",
        statement="A",
        proof="p1",
    )
    projector.apply(body1, raw1)

    # Pretend a verifier said "accepted" so pass_count advances.
    kb.set_node_fields("lem:x", pass_count=2, repair_count=3)

    body2, raw2 = _revise(
        eid="20260425T120001.000-0001-abc0123456789abd",
        target="lem:x",
        kind="lemma",
        statement="B",  # statement change -> statement_hash change
        proof="p1",
    )
    r = projector.apply(body2, raw2)
    assert r.status is ApplyOutcome.APPLIED
    node = kb.node_by_label("lem:x")
    assert node is not None
    # statement changed -> repair_count resets to 0
    assert node.repair_count == 0
    # pass_count = initial_count(lemma, p1) == 0
    assert node.pass_count == 0


def test_node_revised_cascades_to_dependents(
    projector: Projector, kb: KuzuBackend
) -> None:
    """ARCHITECTURE §5.4 + PHASE1 M11 #4: when an upstream node's
    statement_hash changes, every transitive dependent has its
    pass_count and repair_count reset (because dependent's own
    verification_hash changes via Merkle cascade).
    """
    body_def, raw_def = _node_added(
        eid="20260425T120000.000-0001-aaaaaaaaaaaaaaa0",
        target="def:x",
        kind="definition",
        statement="A",
        proof="",
    )
    projector.apply(body_def, raw_def)

    body_lem, raw_lem = _node_added(
        eid="20260425T120001.000-0001-aaaaaaaaaaaaaaa1",
        target="lem:y",
        kind="lemma",
        statement="L about \\ref{def:x}",
        proof="p",
    )
    projector.apply(body_lem, raw_lem)

    body_thm, raw_thm = _node_added(
        eid="20260425T120002.000-0001-aaaaaaaaaaaaaaa2",
        target="thm:z",
        kind="theorem",
        statement="T about \\ref{lem:y}",
        proof="p",
    )
    projector.apply(body_thm, raw_thm)

    # Pretend verifier verdicts have advanced both nodes.
    kb.set_node_fields("lem:y", pass_count=2, repair_count=1)
    kb.set_node_fields("thm:z", pass_count=3, repair_count=2)
    lem_before = kb.node_by_label("lem:y")
    thm_before = kb.node_by_label("thm:z")
    assert lem_before is not None and thm_before is not None
    lem_vh_before = lem_before.verification_hash
    thm_vh_before = thm_before.verification_hash

    # Revise the upstream definition's statement.
    body_rev, raw_rev = _revise(
        eid="20260425T120003.000-0001-aaaaaaaaaaaaaaa3",
        target="def:x",
        kind="definition",
        statement="A revised",
        proof="",
    )
    r = projector.apply(body_rev, raw_rev)
    assert r.status is ApplyOutcome.APPLIED

    # Cascade should have recomputed both downstream verification hashes
    # AND reset pass_count + repair_count to initial values per §5.4.
    lem_after = kb.node_by_label("lem:y")
    thm_after = kb.node_by_label("thm:z")
    assert lem_after is not None and thm_after is not None
    assert lem_after.verification_hash != lem_vh_before, "cascade did not recompute lem:y"
    assert thm_after.verification_hash != thm_vh_before, "cascade did not recompute thm:z"
    # initial_count(lemma, "p") = 0; statement_hash changed so repair_count = 0
    assert lem_after.pass_count == 0
    assert lem_after.repair_count == 0
    assert thm_after.pass_count == 0
    assert thm_after.repair_count == 0


def test_node_revised_kind_mutation_rejected(
    projector: Projector, kb: KuzuBackend
) -> None:
    body1, raw1 = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="lem:x",
        kind="lemma",
        statement="A",
        proof="p",
    )
    projector.apply(body1, raw1)
    body2, raw2 = _revise(
        eid="20260425T120001.000-0001-abc0123456789abd",
        target="lem:x",
        kind="theorem",  # cannot mutate kind
        statement="A",
        proof="p",
    )
    r = projector.apply(body2, raw2)
    assert r.status is ApplyOutcome.APPLY_FAILED
    assert r.reason == REASON_KIND_MUTATION


# ---------------------------------------------------------------------------
# user.hint_attached.
# ---------------------------------------------------------------------------
def _hint(
    *, eid: str, target: str, hint: str, ts: str = "2026-04-25T12:30:00.000Z"
) -> tuple[dict[str, Any], bytes]:
    return _event(
        eid=eid,
        etype="user.hint_attached",
        actor="user:alice",
        target=target,
        payload={"hint": hint, "remark": "", "ts": ts},
    )


def test_hint_attached_appends_user_section(
    projector: Projector, kb: KuzuBackend
) -> None:
    body1, raw1 = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="lem:stuck",
        kind="lemma",
        statement="S",
        proof="",
    )
    projector.apply(body1, raw1)

    body2, raw2 = _hint(
        eid="20260425T120005.000-0001-abc0123456789abd",
        target="lem:stuck",
        hint="try induction on n",
    )
    r = projector.apply(body2, raw2)
    assert r.status is ApplyOutcome.APPLIED
    node = kb.node_by_label("lem:stuck")
    assert node is not None
    assert "[user @" in node.repair_hint
    assert "try induction on n" in node.repair_hint


def test_hint_attached_uses_event_body_ts(
    projector: Projector, kb: KuzuBackend
) -> None:
    """The repair_hint section is timestamped from event body ``ts``,
    not the literal placeholder ``"user"`` or any payload-internal ts."""
    body1, raw1 = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="lem:stuck",
        kind="lemma",
        statement="S",
        proof="",
    )
    projector.apply(body1, raw1)

    # _hint sets a payload.ts; we override the body's top-level ts to
    # confirm the body wins. cli/attach_hint.py omits payload.ts entirely
    # — that path is covered by the existing append test.
    body2, raw2 = _event(
        eid="20260425T120005.000-0001-abc0123456789abd",
        etype="user.hint_attached",
        actor="user:alice",
        target="lem:stuck",
        payload={"hint": "try induction on n", "remark": ""},
        ts="2026-04-25T12:34:56.789+00:00",
    )
    r = projector.apply(body2, raw2)
    assert r.status is ApplyOutcome.APPLIED
    node = kb.node_by_label("lem:stuck")
    assert node is not None
    assert "[user @ 2026-04-25T12:34:56.789+00:00]" in node.repair_hint


def test_hint_attached_falls_back_to_user_when_no_ts(
    projector: Projector, kb: KuzuBackend
) -> None:
    """Hand-rolled events that omit body.ts and payload.ts still get a
    readable section header — the legacy ``"user"`` placeholder."""
    body1, raw1 = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="lem:stuck",
        kind="lemma",
        statement="S",
        proof="",
    )
    projector.apply(body1, raw1)

    body2, raw2 = _event(
        eid="20260425T120005.000-0001-abc0123456789abd",
        etype="user.hint_attached",
        actor="user:alice",
        target="lem:stuck",
        payload={"hint": "h", "remark": ""},
        ts="",
    )
    r = projector.apply(body2, raw2)
    assert r.status is ApplyOutcome.APPLIED
    node = kb.node_by_label("lem:stuck")
    assert node is not None
    assert "[user @ user]" in node.repair_hint


def test_hint_target_missing(projector: Projector, kb: KuzuBackend) -> None:
    body, raw = _hint(
        eid="20260425T120005.000-0001-abc0123456789abd",
        target="lem:does_not_exist",
        hint="x",
    )
    r = projector.apply(body, raw)
    assert r.status is ApplyOutcome.APPLY_FAILED
    assert r.reason == REASON_HINT_TARGET_MISSING


def test_hint_target_unreachable(projector: Projector, kb: KuzuBackend) -> None:
    body1, raw1 = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="lem:done",
        kind="lemma",
        statement="S",
        proof="p",
    )
    projector.apply(body1, raw1)
    # Force pass_count to 1 to simulate verified node.
    kb.set_node_fields("lem:done", pass_count=1)

    body2, raw2 = _hint(
        eid="20260425T120005.000-0001-abc0123456789abd",
        target="lem:done",
        hint="x",
    )
    r = projector.apply(body2, raw2)
    assert r.status is ApplyOutcome.APPLY_FAILED
    assert r.reason == REASON_HINT_TARGET_UNREACHABLE


# ---------------------------------------------------------------------------
# verifier.run_completed.
# ---------------------------------------------------------------------------
def _verdict(
    *,
    eid: str,
    target: str,
    verdict: str,
    verification_hash: str,
    repair_hint: str = "",
) -> tuple[dict[str, Any], bytes]:
    return _event(
        eid=eid,
        etype="verifier.run_completed",
        actor="verifier:codex-test",
        target=target,
        payload={
            "verdict": verdict,
            "verification_hash": verification_hash,
            "verification_report": {
                "summary": "ok" if verdict == "accepted" else "nope",
                "checked_items": [],
                "gaps": [],
                "critical_errors": [],
                "external_reference_checks": [],
            },
            "repair_hint": repair_hint,
        },
    )


def test_verifier_accepted_increments_pass_count(
    projector: Projector, kb: KuzuBackend
) -> None:
    body1, raw1 = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="lem:x",
        kind="lemma",
        statement="S",
        proof="p",
    )
    projector.apply(body1, raw1)
    n0 = kb.node_by_label("lem:x")
    assert n0 is not None
    vh = n0.verification_hash

    body2, raw2 = _verdict(
        eid="20260425T120001.000-0001-abc0123456789abd",
        target="lem:x",
        verdict="accepted",
        verification_hash=vh,
    )
    projector.apply(body2, raw2)
    n1 = kb.node_by_label("lem:x")
    assert n1 is not None and n1.pass_count == 1
    assert n1.repair_count == 0


def test_verifier_gap_resets_to_minus_one(
    projector: Projector, kb: KuzuBackend
) -> None:
    body1, raw1 = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="lem:x",
        kind="lemma",
        statement="S",
        proof="p",
    )
    projector.apply(body1, raw1)
    n0 = kb.node_by_label("lem:x")
    vh = n0.verification_hash

    body2, raw2 = _verdict(
        eid="20260425T120001.000-0001-abc0123456789abd",
        target="lem:x",
        verdict="gap",
        verification_hash=vh,
        repair_hint="the step is unjustified",
    )
    projector.apply(body2, raw2)
    n1 = kb.node_by_label("lem:x")
    assert n1.pass_count == -1
    assert n1.repair_count == 1
    assert "the step is unjustified" in n1.repair_hint


def test_verifier_hash_mismatch_rejected(
    projector: Projector, kb: KuzuBackend
) -> None:
    body1, raw1 = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="lem:x",
        kind="lemma",
        statement="S",
        proof="p",
    )
    projector.apply(body1, raw1)

    body2, raw2 = _verdict(
        eid="20260425T120001.000-0001-abc0123456789abd",
        target="lem:x",
        verdict="accepted",
        verification_hash="deadbeef" * 8,
    )
    r = projector.apply(body2, raw2)
    assert r.status is ApplyOutcome.APPLY_FAILED
    assert r.reason == REASON_HASH_MISMATCH
    assert r.detail is not None and "stale=" in r.detail and "current=" in r.detail


# ---------------------------------------------------------------------------
# self-reference and ref_missing.
# ---------------------------------------------------------------------------
def test_self_reference_rejected(projector: Projector, kb: KuzuBackend) -> None:
    body, raw = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="lem:self",
        kind="lemma",
        statement=r"uses \ref{lem:self}",
        proof="p",
    )
    r = projector.apply(body, raw)
    assert r.status is ApplyOutcome.APPLY_FAILED
    assert r.reason == REASON_SELF_REFERENCE


def test_ref_missing_rejected(projector: Projector, kb: KuzuBackend) -> None:
    body, raw = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="lem:x",
        kind="lemma",
        statement=r"relies on \ref{lem:ghost}",
        proof="p",
    )
    r = projector.apply(body, raw)
    assert r.status is ApplyOutcome.APPLY_FAILED
    assert r.reason == REASON_REF_MISSING


# ---------------------------------------------------------------------------
# Cycle.
# ---------------------------------------------------------------------------
def test_cycle_rejected(projector: Projector, kb: KuzuBackend) -> None:
    for i, (tgt, stmt) in enumerate(
        [
            ("lem:a", r"leaf"),
            ("lem:b", r"uses \ref{lem:a}"),
            ("lem:c", r"uses \ref{lem:b}"),
        ]
    ):
        body, raw = _node_added(
            eid=f"20260425T120000.00{i}-0001-abc0123456789a{i:02d}",
            target=tgt,
            kind="lemma",
            statement=stmt,
            proof="p",
        )
        projector.apply(body, raw)

    # Now revise lem:a to reference lem:c — would close a cycle.
    body, raw = _revise(
        eid="20260425T120010.000-0001-abc0123456789abd",
        target="lem:a",
        kind="lemma",
        statement=r"uses \ref{lem:c}",
        proof="p",
    )
    r = projector.apply(body, raw)
    assert r.status is ApplyOutcome.APPLY_FAILED
    assert r.reason == REASON_CYCLE
    assert r.detail is not None and " -> " in r.detail
