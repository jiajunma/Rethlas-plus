"""Pool-based candidate ordering (ARCHITECTURE §10.2).

Two pools, two ordering rules:

- **Generator pool**: candidates at ``pass_count = -1``, ordered by
  ``label`` ascending. **No** ``repair_count`` deprioritisation —
  §10.4 keeps the "give up" decision on the generator itself, not the
  scheduler. Guards against the prior starvation regression.
- **Verifier pool**: candidates at ``pass_count >= 0`` AND
  ``pass_count < desired``, ordered by (``pass_count`` asc,
  ``label`` asc). Both proof-requiring kinds *and* axioms (definition,
  external_theorem) are valid verifier targets — `def` / `ext_thm` enter
  the verifier queue at ``pass_count = 0`` and march to ``desired`` like
  any other.

The dispatcher itself is pure: callers feed in candidate lists and
in-flight targets, and the dispatcher returns the slate of labels that
should be dispatched on this tick (capped by pool capacity and the
"no concurrent same-target across pools" rule).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class GeneratorCandidate:
    label: str


@dataclass(frozen=True, slots=True)
class VerifierCandidate:
    label: str
    pass_count: int


def select_generator_targets(
    candidates: Iterable[GeneratorCandidate],
    *,
    capacity: int,
    in_flight_targets: Iterable[str],
) -> list[str]:
    """§10.2.2 generator ordering: label asc."""
    if capacity <= 0:
        return []
    busy = set(in_flight_targets)
    pool = sorted({c.label for c in candidates})
    out: list[str] = []
    for lbl in pool:
        if lbl in busy:
            continue
        out.append(lbl)
        busy.add(lbl)
        if len(out) >= capacity:
            break
    return out


def select_verifier_targets(
    candidates: Iterable[VerifierCandidate],
    *,
    capacity: int,
    in_flight_targets: Iterable[str],
) -> list[str]:
    """§10.2.1 verifier ordering: (pass_count asc, label asc)."""
    if capacity <= 0:
        return []
    busy = set(in_flight_targets)
    # Deduplicate by label, keep min pass_count seen.
    by_label: dict[str, int] = {}
    for c in candidates:
        prev = by_label.get(c.label)
        if prev is None or c.pass_count < prev:
            by_label[c.label] = c.pass_count
    ordered = sorted(by_label.items(), key=lambda kv: (kv[1], kv[0]))
    out: list[str] = []
    for lbl, _pc in ordered:
        if lbl in busy:
            continue
        out.append(lbl)
        busy.add(lbl)
        if len(out) >= capacity:
            break
    return out


__all__ = [
    "GeneratorCandidate",
    "VerifierCandidate",
    "select_generator_targets",
    "select_verifier_targets",
]
