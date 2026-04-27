"""Dashboard liveness classification + theorem-status vocabulary.

Kept separate from the HTTP layer so unit tests can exercise the
classification rules without booting a server.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# §6.7.1 staleness thresholds.
HEALTHY_S = 60.0
DEGRADED_S = 300.0  # 5 minutes


# Theorem status vocabulary from PHASE1 M9.
STATUS_DONE = "done"
STATUS_VERIFIED = "verified"
STATUS_NEEDS_VERIFICATION = "needs_verification"
STATUS_BLOCKED_ON_DEPENDENCY = "blocked_on_dependency"
STATUS_NEEDS_GENERATION = "needs_generation"
STATUS_GEN_BLOCKED_ON_DEPENDENCY = "generation_blocked_on_dependency"
STATUS_USER_BLOCKED = "user_blocked"
STATUS_IN_FLIGHT = "in_flight"


def liveness_label(updated_at: str | None, now: datetime | None = None) -> str:
    """Map ``updated_at`` to ``healthy`` / ``degraded`` / ``down``."""
    if not updated_at:
        return "down"
    try:
        if updated_at.endswith("Z"):
            updated_at = updated_at[:-1] + "+00:00"
        parsed = datetime.fromisoformat(updated_at)
    except ValueError:
        return "down"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(tz=timezone.utc)
    age = (now - parsed).total_seconds()
    if age <= HEALTHY_S:
        return "healthy"
    if age <= DEGRADED_S:
        return "degraded"
    return "down"


def classify_theorem(
    *,
    label: str,
    kind: str,
    pass_count: int,
    desired: int,
    deps: list[str],
    deps_pass_counts: dict[str, int],
    in_flight: bool,
    repair_hint: str = "",
    repair_count: int = 0,
    introduced_by_actor: str = "user:cli",
) -> str:
    """Return the §M9 status keyword for a node."""
    if in_flight:
        return STATUS_IN_FLIGHT
    if pass_count >= desired:
        return STATUS_DONE
    deps_ready = all(deps_pass_counts.get(d, -1) >= 1 for d in deps)
    introduced_by_generator = introduced_by_actor.startswith("generator:")
    if pass_count >= 1:
        # A node verified at least once but whose deps got reset (e.g.
        # an upstream Merkle cascade) cannot progress further until the
        # deps catch up. Per PHASE1 M9 status vocabulary it shows as
        # blocked_on_dependency rather than verified — the latter
        # implies it's actively advancing.
        if not deps_ready:
            return STATUS_BLOCKED_ON_DEPENDENCY
        return STATUS_VERIFIED
    if pass_count == 0:
        # §5.4.1 + provenance: a *user-introduced* axiom that the
        # verifier has already rejected (repair_count > 0) is
        # user_blocked — only the user can rewrite it. Generator-
        # introduced helpers are routed to the generator pool instead
        # (see projector reject branch + coordinator gen_pool).
        if (
            kind in {"definition", "external_theorem"}
            and repair_count > 0
            and not introduced_by_generator
        ):
            return STATUS_USER_BLOCKED
        if not deps_ready:
            return STATUS_BLOCKED_ON_DEPENDENCY
        return STATUS_NEEDS_VERIFICATION
    # pass_count == -1: proof-requiring kinds and generator-introduced
    # axioms both go through the generator. A user-introduced axiom at
    # -1 (legacy state, pre-rebuild) still flags as user_blocked.
    if kind in {"definition", "external_theorem"} and not introduced_by_generator:
        return STATUS_USER_BLOCKED
    if not deps_ready:
        return STATUS_GEN_BLOCKED_ON_DEPENDENCY
    return STATUS_NEEDS_GENERATION


__all__ = [
    "DEGRADED_S",
    "HEALTHY_S",
    "STATUS_BLOCKED_ON_DEPENDENCY",
    "STATUS_DONE",
    "STATUS_GEN_BLOCKED_ON_DEPENDENCY",
    "STATUS_IN_FLIGHT",
    "STATUS_NEEDS_GENERATION",
    "STATUS_NEEDS_VERIFICATION",
    "STATUS_USER_BLOCKED",
    "STATUS_VERIFIED",
    "classify_theorem",
    "liveness_label",
]
