"""coordinator.main._build_priority_fn — Phase B wiring.

Verifies the toggle:

- ``use_voi_scoring=False`` → returns ``None`` → dispatcher falls back
  to legacy ``(pass_count, label)`` order.
- ``use_voi_scoring=True`` with a non-empty snapshot → returns a
  callable that the dispatcher can use without crashing.
- An empty snapshot returns ``None`` regardless of the toggle.
- A failure inside the scoring layer also yields ``None`` so the
  dispatcher cannot starve.
"""

from __future__ import annotations

from coordinator.main import _KBSnapshot, _build_priority_fn
from coordinator.precheck import CandidateInput


def _ci(label: str, *, deps: tuple[str, ...] = ()) -> CandidateInput:
    return CandidateInput(
        target=label,
        target_kind="lemma",
        statement=f"statement of {label}",
        proof="",
        statement_hash="0" * 64,
        verification_hash="0" * 64,
        pass_count=0,
        repair_count=0,
        repair_hint="",
        verification_report="",
        dep_statement_hashes={d: "0" * 64 for d in deps},
        dep_pass_counts={d: 1 for d in deps},
    )


def test_priority_fn_is_none_when_toggle_off() -> None:
    snap = _KBSnapshot(candidates=[_ci("lem:a"), _ci("lem:b")])
    fn = _build_priority_fn(snap, use_voi_scoring=False)
    assert fn is None


def test_priority_fn_is_none_when_snapshot_empty() -> None:
    snap = _KBSnapshot(candidates=[])
    assert _build_priority_fn(snap, use_voi_scoring=True) is None


def test_priority_fn_callable_when_toggle_on() -> None:
    snap = _KBSnapshot(
        candidates=[
            _ci("lem:a"),
            _ci("lem:b"),
            _ci("thm:main", deps=("lem:a", "lem:b")),
        ]
    )
    fn = _build_priority_fn(snap, use_voi_scoring=True)
    assert fn is not None
    out = fn(["lem:a", "lem:b", "thm:main"], 2)
    assert isinstance(out, list)
    assert all(label in {"lem:a", "lem:b", "thm:main"} for label in out)
    assert len(set(out)) == len(out)  # no duplicates


def test_priority_fn_falls_back_when_construction_fails(monkeypatch) -> None:
    """A failure inside the scoring layer must yield None, not raise."""
    import coordinator.main as cm

    def boom(*_args, **_kwargs):
        raise RuntimeError("scoring layer broken")

    monkeypatch.setattr(cm, "ProofGraph", type("X", (), {"build": staticmethod(boom)}))
    snap = _KBSnapshot(candidates=[_ci("lem:a")])
    fn = _build_priority_fn(snap, use_voi_scoring=True)
    assert fn is None


# ---------------------------------------------------------------------------
# S3 — _evidence_from_candidate + _action_for_candidate scaffold.
# ---------------------------------------------------------------------------
from coordinator.main import _action_for_candidate, _evidence_from_candidate
from rethlas_scoring.policy import Action, EvidenceKind, VerdictKind


def _ci_pc(label: str, pass_count: int, repair_count: int = 0) -> CandidateInput:
    """Helper that lets pass_count / repair_count be set independently."""
    return CandidateInput(
        target=label,
        target_kind="lemma",
        statement=f"statement of {label}",
        proof="",
        statement_hash="0" * 64,
        verification_hash="0" * 64,
        pass_count=pass_count,
        repair_count=repair_count,
        repair_hint="",
        verification_report="",
        dep_statement_hashes={},
        dep_pass_counts={},
    )


def test_evidence_from_candidate_empty_for_zero_pass_count() -> None:
    assert _evidence_from_candidate(_ci_pc("lem:a", pass_count=0)) == []


def test_evidence_from_candidate_synthesises_default_ok_per_pass() -> None:
    cand = _ci_pc("lem:a", pass_count=2)
    evs = _evidence_from_candidate(cand)
    assert len(evs) == 2
    assert all(e.kind is EvidenceKind.DEFAULT for e in evs)
    assert all(e.verdict is VerdictKind.OK for e in evs)
    # Worker IDs must be distinct so pass_count_from_evidence sees each as a
    # separate vote.
    assert len({e.worker_id for e in evs}) == 2


def test_evidence_from_candidate_ignores_repair_count() -> None:
    """Past rejections happened on a *different* statement; honest
    reconstruction excludes them from the current ledger."""
    cand = _ci_pc("lem:a", pass_count=1, repair_count=5)
    evs = _evidence_from_candidate(cand)
    assert len(evs) == 1
    assert evs[0].kind is EvidenceKind.DEFAULT
    assert evs[0].verdict is VerdictKind.OK


def test_action_for_candidate_pending_is_default_verify() -> None:
    cand = _ci_pc("lem:a", pass_count=1)
    assert _action_for_candidate(cand, desired_pass=3) is Action.DEFAULT_VERIFY


def test_action_for_candidate_verified_is_none() -> None:
    cand = _ci_pc("lem:a", pass_count=3)
    assert _action_for_candidate(cand, desired_pass=3) is Action.NONE


def test_action_for_candidate_under_honest_reconstruction_is_always_default_verify() -> None:
    """Property: every candidate that survives the eligibility filters
    (``0 ≤ pass_count < desired``) yields DEFAULT_VERIFY under the
    S3 honest reconstruction. Future S4+ changes may relax this."""
    for pc in range(0, 3):
        cand = _ci_pc(f"lem:p{pc}", pass_count=pc)
        assert _action_for_candidate(cand, desired_pass=3) is Action.DEFAULT_VERIFY
