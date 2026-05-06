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
    """§10.2.1 verifier ordering — **tier-strict BFS by ``pass_count``**.

    Hard constraint: walk pass_count tiers from lowest to highest;
    never spill into tier ``k+1`` while tier ``k`` still has eligible
    (non-busy) candidates. Within a single tier, ordering is either
    alphabetical (``priority_fn=None`` legacy path) or VOI-driven
    (``priority_fn`` supplied — typically built by
    ``rethlas_scoring.scheduler.make_priority_fn`` when
    ``use_voi_scoring=True``). See ``docs/SCORING_SCHEDULING.md §3``.

    Recovery / rollback:

    - The ``pass_count`` deduplication (one entry per label, min
      pass_count) and the ``in_flight_targets`` skip are applied in
      every code path.
    - If ``priority_fn`` raises or returns nothing for a given tier,
      that tier falls back to alphabetical ordering. Other tiers are
      unaffected — a buggy scorer cannot starve any node.
    - Labels the priority_fn omits within a tier are appended in
      alphabetical order so they still get dispatched.

    See ``docs/SCORING_INTEGRATION.md`` for the toggle procedure.
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
    if not by_label:
        return []

    # Group by tier (pass_count). Walk tiers low → high.
    by_tier: dict[int, list[str]] = {}
    for lbl, pc in by_label.items():
        by_tier.setdefault(pc, []).append(lbl)

    out: list[str] = []
    for tier in sorted(by_tier):
        tier_labels_sorted = sorted(by_tier[tier])
        if priority_fn is None:
            ordered = tier_labels_sorted
        else:
            try:
                voi_order = priority_fn(
                    tier_labels_sorted, max(1, capacity - len(out) + len(busy))
                )
            except Exception:
                voi_order = []
            tier_set = set(tier_labels_sorted)
            valid = [lbl for lbl in voi_order if lbl in tier_set]
            leftover = [lbl for lbl in tier_labels_sorted if lbl not in valid]
            ordered = valid + leftover

        for lbl in ordered:
            if lbl in busy:
                continue
            out.append(lbl)
            busy.add(lbl)
            if len(out) >= capacity:
                return out
    return out


__all__ = [
    "GeneratorCandidate",
    "PriorityFn",
    "VerifierCandidate",
    "select_generator_targets",
    "select_verifier_targets",
]
