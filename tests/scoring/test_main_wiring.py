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
