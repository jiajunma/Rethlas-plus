"""Pool-based candidate ordering (ARCHITECTURE §10.2).

Two pools, two ordering rules:

- **Generator pool**: candidates at ``pass_count = -1``, ordered by
  ``label`` ascending. **No** ``repair_count`` deprioritisation —
  §10.4 keeps the "give up" decision on the generator itself, not the
  scheduler. Guards against the prior starvation regression.
- **Verifier pool**: candidates at ``pass_count >= 0`` AND
  ``pass_count < desired``, ordered by (``pass_count`` asc,
  ``label`` asc). Both proof-requiring kinds *and* axioms (definition,
  external_theorem) are valid verifier targets — `def` / `ext` enter
  the verifier queue at ``pass_count = 0`` and march to ``desired`` like
  any other.

The dispatcher itself is pure: callers feed in candidate lists and
in-flight targets, and the dispatcher returns the slate of labels that
should be dispatched on this tick (capped by pool capacity and the
"no concurrent same-target across pools" rule).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence


# Optional VOI-based priority hook (rethlas_scoring.scheduler.make_priority_fn).
# Signature: (candidate_labels, capacity) -> labels-in-priority-order.
# When ``None`` (default), the legacy ``(pass_count, label)`` ordering
# applies — see ``docs/SCORING_INTEGRATION.md``.
PriorityFn = Callable[[Sequence[str], int], list[str]]


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
    priority_fn: PriorityFn | None = None,
) -> list[str]:
    """§10.2.1 verifier ordering.

    Default ordering is ``(pass_count asc, label asc)``. When
    ``priority_fn`` is supplied (rollback toggle ``use_voi_scoring=True``),
    the deduplicated label set is reordered by the VOI-aware priority
    function before the busy-target / capacity filter is applied. The
    ``pass_count`` deduplication invariant (one entry per label, min
    pass_count) and the ``in_flight_targets`` skip rule are preserved
    in both branches so the dispatcher contract does not regress.

    See ``docs/SCORING_INTEGRATION.md`` for the rollback procedure.
    """

    if capacity <= 0:
        return []
    busy = set(in_flight_targets)
    # Deduplicate by label, keep min pass_count seen.
    by_label: dict[str, int] = {}
    for c in candidates:
        prev = by_label.get(c.label)
        if prev is None or c.pass_count < prev:
            by_label[c.label] = c.pass_count

    if priority_fn is None:
        ordered_labels = [
            lbl for lbl, _pc in sorted(by_label.items(), key=lambda kv: (kv[1], kv[0]))
        ]
    else:
        # Ask the VOI scorer for a priority order; fall back to legacy
        # if it returns nothing or a label outside the candidate set.
        candidate_labels = list(by_label.keys())
        try:
            voi_order = priority_fn(candidate_labels, capacity + len(busy))
        except Exception:
            voi_order = []
        valid = [lbl for lbl in voi_order if lbl in by_label]
        # Append any candidates the priority_fn omitted, in legacy order,
        # so we never starve a node by accident.
        leftover = sorted(
            (lbl for lbl in by_label if lbl not in valid),
            key=lambda lbl: (by_label[lbl], lbl),
        )
        ordered_labels = valid + leftover

    out: list[str] = []
    for lbl in ordered_labels:
        if lbl in busy:
            continue
        out.append(lbl)
        busy.add(lbl)
        if len(out) >= capacity:
            break
    return out


__all__ = [
    "GeneratorCandidate",
    "PriorityFn",
    "VerifierCandidate",
    "select_generator_targets",
    "select_verifier_targets",
]
