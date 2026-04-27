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


def test_hint_attached_before_verifier_survives_subsequent_merge(
    projector: Projector, kb: KuzuBackend
) -> None:
    """User hint attached on a node with empty repair_hint must survive a
    later verifier verdict's ``_merge_verifier_section`` pass.

    Regression: when ``repair_hint`` was empty, the prior implementation
    composed the new section as ``"---\\n[user @ ts]\\n..."``. That string
    starts with ``---``, not ``\\n---\\n``, so the merge's
    ``existing.split("\\n---\\n")`` returned a single section beginning
    with ``---``, which the ``[user @ "`` filter rejected — so the next
    verifier verdict silently dropped the user hint.
    """
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

    after_hint = kb.node_by_label("lem:stuck")
    assert after_hint is not None
    assert "try induction on n" in after_hint.repair_hint

    # Now a verifier gap verdict — its ``_merge_verifier_section`` must
    # preserve the user hint.
    n0 = kb.node_by_label("lem:stuck")
    body3, raw3 = _verdict(
        eid="20260425T120010.000-0001-abc0123456789abe",
        target="lem:stuck",
        verdict="gap",
        verification_hash=n0.verification_hash,
        repair_hint="verifier suggests reviewing the inductive step",
    )
    r = projector.apply(body3, raw3)
    assert r.status is ApplyOutcome.APPLIED

    after_verdict = kb.node_by_label("lem:stuck")
    assert after_verdict is not None
    assert "verifier suggests reviewing the inductive step" in after_verdict.repair_hint
    assert "[user @" in after_verdict.repair_hint, (
        f"user section dropped during merge; repair_hint={after_verdict.repair_hint!r}"
    )
    assert "try induction on n" in after_verdict.repair_hint, (
        f"user hint content dropped; repair_hint={after_verdict.repair_hint!r}"
    )


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


def test_verifier_gap_on_definition_keeps_pass_count_at_zero(
    projector: Projector, kb: KuzuBackend
) -> None:
    """§5.4.1 bugfix regression: rejecting a definition must reset
    pass_count to its initial_count (= 0 for axioms), not to -1.

    Pre-fix, the projector unconditionally set pass_count=-1 on gap
    verdicts; that combined with the dashboard classifier rule made
    every rejected definition look user_blocked, deadlocking the
    coordinator (no further worker dispatch on a chain of
    blocked_on_dependency targets).
    """
    body1, raw1 = _node_added(
        eid="20260427T120000.000-0001-def0123456789abc",
        target="def:x",
        kind="definition",
        statement="A definition statement.",
        proof="",
    )
    projector.apply(body1, raw1)
    n0 = kb.node_by_label("def:x")
    assert n0 is not None and n0.pass_count == 0  # initial_count for axioms

    body2, raw2 = _verdict(
        eid="20260427T120001.000-0001-def0123456789abd",
        target="def:x",
        verdict="gap",
        verification_hash=n0.verification_hash,
        repair_hint="please cite the source definition",
    )
    projector.apply(body2, raw2)
    n1 = kb.node_by_label("def:x")
    assert n1.pass_count == 0, (
        "definition gap must keep pass_count at initial_count=0, not -1"
    )
    assert n1.repair_count == 1
    assert "please cite the source definition" in n1.repair_hint


def test_verifier_gap_on_generator_introduced_axiom_resets_to_minus_one(
    projector: Projector, kb: KuzuBackend
) -> None:
    """Generator-introduced helper definitions that the verifier rejects
    must reset to ``pass_count = -1`` so they re-enter the generator
    pool and the system stays autonomous. The user-introduced
    counterpart still resets to 0 (user_blocked).
    """
    # Seed a target lemma for the generator batch (target must already
    # exist; brand-new helper labels appear inside the same batch).
    body0, raw0 = _node_added(
        eid="20260427T120000.000-0001-aaaaaaaaaaaaaaaa",
        target="thm:induced_orbit_target",
        kind="theorem",
        statement="A target theorem.",
        proof="",
    )
    projector.apply(body0, raw0)

    # Generator commits a brand-new helper definition + the target proof.
    body_gen, raw_gen = _event(
        eid="20260427T120001.000-0001-bbbbbbbbbbbbbbbb",
        etype="generator.batch_committed",
        actor="generator:codex-default",
        payload={
            "target": "thm:induced_orbit_target",
            "nodes": [
                {
                    "label": "def:helper",
                    "kind": "definition",
                    "statement": "A helper definition introduced by the generator.",
                    "proof": "",
                    "remark": "",
                    "source_note": "",
                },
                {
                    "label": "thm:induced_orbit_target",
                    "kind": "theorem",
                    "statement": "A target theorem.",
                    "proof": r"By \ref{def:helper}, the result follows.",
                    "remark": "",
                    "source_note": "",
                },
            ],
        },
    )
    r_gen = projector.apply(body_gen, raw_gen)
    assert r_gen.status is ApplyOutcome.APPLIED
    helper = kb.node_by_label("def:helper")
    assert helper is not None
    assert helper.introduced_by_actor == "generator:codex-default"
    assert helper.pass_count == 0  # axiom initial_count

    # Verifier rejects the helper.
    body_v, raw_v = _verdict(
        eid="20260427T120002.000-0001-cccccccccccccccc",
        target="def:helper",
        verdict="gap",
        verification_hash=helper.verification_hash,
        repair_hint="please clarify the helper definition",
    )
    projector.apply(body_v, raw_v)
    rejected = kb.node_by_label("def:helper")
    assert rejected is not None
    assert rejected.pass_count == -1, (
        "generator-introduced axiom must reset to -1 so the generator "
        "pool can pick it up for repair"
    )
    assert rejected.repair_count == 1
    assert rejected.introduced_by_actor == "generator:codex-default"


def test_user_introduced_definition_keeps_provenance_across_revision(
    projector: Projector, kb: KuzuBackend
) -> None:
    """Revisions must not transfer provenance: a user revising a
    generator-introduced helper must not strip its generator
    ownership (otherwise verifier rejection would leak back to the
    user). Mirrored: a user-introduced definition stays user-owned.
    """
    body, raw = _node_added(
        eid="20260427T120000.000-0001-dddddddddddddddd",
        target="def:user_def",
        kind="definition",
        statement="An original user-authored definition.",
        proof="",
        actor="user:cli",
    )
    projector.apply(body, raw)
    n0 = kb.node_by_label("def:user_def")
    assert n0 is not None and n0.introduced_by_actor == "user:cli"

    revise_body, revise_raw = _event(
        eid="20260427T120001.000-0001-eeeeeeeeeeeeeeee",
        etype="user.node_revised",
        actor="user:bob",
        target="def:user_def",
        payload={
            "kind": "definition",
            "statement": "Revised user-authored definition.",
            "proof": "",
            "remark": "",
            "source_note": "",
        },
    )
    projector.apply(revise_body, revise_raw)
    n1 = kb.node_by_label("def:user_def")
    assert n1 is not None
    assert n1.introduced_by_actor == "user:cli", (
        "introduced_by_actor records first introducer, not the latest reviser"
    )


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
# H29: self-loops are caught as REASON_CYCLE (length-1 cycle); dangling
# `\ref{}` to non-existent labels are ADMITTED, with the missing edge
# silently skipped by Cypher MATCH-CREATE. The verifier later flags the
# unresolved reference via ``external_reference_checks[]``.
# ---------------------------------------------------------------------------
def test_self_reference_rejected_as_cycle(
    projector: Projector, kb: KuzuBackend
) -> None:
    body, raw = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="lem:self",
        kind="lemma",
        statement=r"uses \ref{lem:self}",
        proof="p",
    )
    r = projector.apply(body, raw)
    assert r.status is ApplyOutcome.APPLY_FAILED
    assert r.reason == REASON_CYCLE


def test_ref_missing_admitted_with_dangling_dep(
    projector: Projector, kb: KuzuBackend
) -> None:
    """H29: post-boundary the projector no longer rejects ``node_added``
    events whose body references labels that aren't in KB yet. The Node
    row lands committed with the dangling label still in ``depends_on``;
    the actual ``DependsOn`` edge is silently skipped at write time
    (Cypher MATCH-CREATE pattern). Verifier later flags this via
    ``external_reference_checks[].status="missing_from_nodes"``."""
    body, raw = _node_added(
        eid="20260425T120000.000-0001-abc0123456789abc",
        target="lem:x",
        kind="lemma",
        statement=r"relies on \ref{lem:ghost}",
        proof="p",
    )
    r = projector.apply(body, raw)
    assert r.status is ApplyOutcome.APPLIED
    row = kb.node_by_label("lem:x")
    assert row is not None
    # Cypher's MATCH-CREATE pattern in `_set_dependencies` silently
    # skips the dangling edge — no `DependsOn` lands in the graph.
    # The verifier flags the unresolved reference content-side.
    assert kb.dependencies_of("lem:x") == []


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
