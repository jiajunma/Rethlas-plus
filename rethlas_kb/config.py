"""Config-validation hooks for rethlas-kb (issue #13).

Currently the only validation here is the **cross-backend isolation
constraint** for Mode B: pairs of agents that benefit from
independent perspectives must use different LLM backends. Without
this, a single biased LLM produces both the proof AND the
verification, which destroys the assurance signal.

## The constraint

These pairs must use DIFFERENT backends in production:

- ``proof-gap-filler`` ≠ ``proof-verifier``
- ``proof-gap-filler`` ≠ ``source-claim-verifier``

The set is small — same-source-bias matters most when a generator
writes content that a verifier later judges. statement-verifier and
counterexample-hunter are excluded from the constraint (the former
judges content the user already wrote; the latter does inverse
search where bias risks are different in kind).

## Override

``--allow-same-backend`` on a Mode B subcommand suppresses the
error at startup (for single-machine debugging when you only have
one CLI installed). The override is logged loudly to stderr so it
can't slip past CI silently.

## Mode A

Mode A relies on **convention**, not enforcement: the user picks
which agentic CLI to invoke for each /verb. The slash-command file
naming and AGENTS.md document the recommended pairings. If the user
runs both `/fill-gap` and `/verify-proof-detailed` from the same
agentic CLI process, they violate the convention but the tooling
won't stop them. That's a deliberate choice — Mode A's value comes
from the agentic CLI's freedom of action.
"""

from __future__ import annotations

from dataclasses import dataclass


class ConfigError(Exception):
    """Raised when a config violates an invariant rethlas-kb depends on."""


# Roles whose pairings carry same-source-bias risk. Each tuple is an
# unordered pair: if two roles in a pair are bound to the same
# backend, the user must opt out via --allow-same-backend.
ISOLATED_ROLE_PAIRS: tuple[tuple[str, str], ...] = (
    ("proof-gap-filler", "proof-verifier"),
    ("proof-gap-filler", "source-claim-verifier"),
)


@dataclass(frozen=True)
class AgentBinding:
    """One agent role pinned to one backend name (for Mode B config)."""

    role: str
    backend_name: str


def validate_backend_isolation(
    bindings: dict[str, str],
    *,
    allow_same_backend: bool = False,
) -> list[str]:
    """Check the cross-backend isolation rule for a Mode B config.

    Parameters
    ----------
    bindings:
        Mapping ``{role_name: backend_name}``. Only the roles named in
        :data:`ISOLATED_ROLE_PAIRS` are inspected; extra roles in the
        mapping are ignored, missing roles are ignored. This makes the
        validator usable for partial configs (e.g. only running
        proof-verifier today).
    allow_same_backend:
        When True, violations are recorded but **not** raised —
        returned as a list of warning strings instead.

    Returns
    -------
    list[str]
        Empty list when the config is clean. Otherwise: each entry
        is a one-line description of one violated pair.

    Raises
    ------
    ConfigError
        When ``allow_same_backend`` is False and at least one pair
        is violated. The message names every violated pair.
    """
    violations: list[str] = []
    for role_a, role_b in ISOLATED_ROLE_PAIRS:
        backend_a = bindings.get(role_a)
        backend_b = bindings.get(role_b)
        # Skip pairs where either role isn't bound — partial configs
        # are legal.
        if not backend_a or not backend_b:
            continue
        if backend_a == backend_b:
            violations.append(
                f"{role_a!r} and {role_b!r} are both bound to backend "
                f"{backend_a!r} — same-source-bias risk. Use a different "
                f"backend for one of them, or pass --allow-same-backend."
            )

    if violations and not allow_same_backend:
        raise ConfigError(_format_violations(violations))
    return violations


def _format_violations(violations: list[str]) -> str:
    if len(violations) == 1:
        return f"cross-backend isolation violated: {violations[0]}"
    lines = [
        f"cross-backend isolation violated ({len(violations)} pairs):",
    ]
    lines.extend(f"  - {v}" for v in violations)
    return "\n".join(lines)


__all__ = [
    "AgentBinding",
    "ConfigError",
    "ISOLATED_ROLE_PAIRS",
    "validate_backend_isolation",
]
