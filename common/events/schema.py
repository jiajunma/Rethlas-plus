"""Structural validation of truth event bodies (ARCHITECTURE §3.4).

This is the **shape-level** admission check — whether the envelope
(``event_id``, ``type``, ``actor``, ``ts``, ``payload``) is well-formed.
Per-type payload validation (``user.node_added.payload.kind in {lemma, ...}``,
``verifier.run_completed.payload.verdict``, etc.) lives in later milestones;
M1 only guarantees the envelope so tests across milestones share a single
parse path.

Phase I event types (§3.5.1):
- ``user.node_added`` / ``user.node_revised`` / ``user.hint_attached``
- ``generator.batch_committed``
- ``verifier.run_completed``
"""

from __future__ import annotations

import re
from typing import Any, Final, Iterable

EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "user.node_added",
        "user.node_revised",
        "user.hint_attached",
        "generator.batch_committed",
        "verifier.run_completed",
    }
)

_EVENT_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"^\d{8}T\d{6}\.\d{3}-\d{4}-[0-9a-f]{16}$"
)
_ISO_FULL_RE: Final[re.Pattern[str]] = re.compile(
    # YYYY-MM-DDTHH:MM:SS(.sss)?(Z|±HH:MM). Local offset (§2.4 trailer, §3.3).
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_ACTOR_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_-]*:[A-Za-z0-9_.-]+$")

_REQUIRED_TOP_LEVEL: Final[frozenset[str]] = frozenset(
    {"event_id", "type", "actor", "ts", "payload"}
)


class SchemaError(ValueError):
    """Raised when a truth event body fails envelope validation."""


def validate_event_schema(body: dict[str, Any]) -> None:
    """Validate the §3.4 envelope in-place. Raises :class:`SchemaError` on failure.

    Does not touch the ``payload`` contents — those are validated by the
    per-type handlers in later milestones.
    """
    if not isinstance(body, dict):
        raise SchemaError(f"event body must be a JSON object, got {type(body).__name__}")

    missing = _REQUIRED_TOP_LEVEL - set(body)
    if missing:
        raise SchemaError(
            f"event missing required fields: {sorted(missing)} (§3.4)"
        )

    event_id = body["event_id"]
    if not isinstance(event_id, str) or not _EVENT_ID_RE.match(event_id):
        raise SchemaError(
            f"event_id {event_id!r} must match iso_ms-seq-uid (§3.2)"
        )

    etype = body["type"]
    if etype not in EVENT_TYPES:
        raise SchemaError(
            f"unknown event type {etype!r}; expected one of {sorted(EVENT_TYPES)}"
        )

    actor = body["actor"]
    if not isinstance(actor, str) or not _ACTOR_RE.match(actor):
        raise SchemaError(f"actor {actor!r} must match kind:instance")

    ts = body["ts"]
    if not isinstance(ts, str) or not _ISO_FULL_RE.match(ts):
        raise SchemaError(
            f"ts {ts!r} must be ISO 8601 with an offset or Z suffix (§3.3)"
        )

    payload = body["payload"]
    if not isinstance(payload, dict):
        raise SchemaError(
            f"payload must be a JSON object, got {type(payload).__name__}"
        )

    # `target` is optional by §3.4 but must be a string when present.
    target = body.get("target")
    if target is not None and not isinstance(target, str):
        raise SchemaError(f"target must be a string or absent, got {type(target).__name__}")

    # `cost` is optional (§3.6); type-check only shape, not contents.
    cost = body.get("cost")
    if cost is not None and not isinstance(cost, dict):
        raise SchemaError(f"cost must be an object or absent, got {type(cost).__name__}")


def extra_keys(body: dict[str, Any], allowed: Iterable[str]) -> list[str]:
    """Return top-level keys that are not in ``allowed``. Helper for linters."""
    allowed_set = set(allowed)
    return sorted(k for k in body if k not in allowed_set)
