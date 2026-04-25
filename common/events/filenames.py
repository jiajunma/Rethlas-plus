"""Event filename format + parsing (ARCHITECTURE §3.2).

Canonical shape::

    {iso_ms}--{event_type}--{target_or_none}--{actor}--{seq}--{uid}.json

- ``iso_ms`` — ``YYYYMMDDTHHMMSS.mmm`` (UTC, no ``Z`` suffix per §3.2)
- ``event_type`` — ``producer_kind.action`` dotted name
- ``target_or_none`` — label with ``:`` replaced by ``_``, or literal ``none``
- ``actor`` — ``kind:instance`` with ``:`` escaped to ``_``
- ``seq`` — zero-padded 4-digit monotone sequence
- ``uid`` — 16 hex characters

Both ``event_id`` and ``event_type`` / ``target`` are authoritative from
the event body (§3.2 "filename metadata is informational") — this module
exists so the linter and directory scans have a cheap shape-level
agreement with the body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

# Separator is a pair of hyphens. Hyphens themselves are allowed inside
# event_type / actor / label-slugs as long as they don't appear twice in a
# row (which would confuse the split).
_SEP: Final[str] = "--"
_SEP_RE: Final[re.Pattern[str]] = re.compile(r"--")

_ISO_MS_RE: Final[re.Pattern[str]] = re.compile(
    r"^\d{8}T\d{6}\.\d{3}$"
)
_EVENT_TYPE_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
# actor after ":"→"_" escape: kind_instance
_ACTOR_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_.-]*$")
_SEQ_RE: Final[re.Pattern[str]] = re.compile(r"^\d{4}$")
_UID_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{16}$")
_TARGET_OR_NONE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:none|[a-z]+_[a-z0-9_]+)$"
)


class FilenameError(ValueError):
    """Raised when an event filename does not match the §3.2 shape."""


@dataclass(frozen=True, slots=True)
class ParsedFilename:
    iso_ms: str
    event_type: str
    target: str | None  # None when the filename component was ``none``
    actor: str  # with ``:`` unescaped back to ``:``
    seq: int
    uid: str


def escape_label(label: str) -> str:
    """Escape a node label (``def:x``) to its filename form (``def_x``)."""
    return label.replace(":", "_")


def _escape_actor(actor: str) -> str:
    return actor.replace(":", "_")


def _unescape_actor(escaped: str) -> str:
    # Actor is ``kind:instance``; the first ``_`` back to ``:`` is enough
    # as long as ``kind`` itself never contains ``_``. §3.5 producer kinds
    # (``user``, ``generator``, ``verifier``) all satisfy that constraint.
    idx = escaped.find("_")
    if idx == -1:
        return escaped
    return escaped[:idx] + ":" + escaped[idx + 1 :]


def format_filename(
    *,
    iso_ms: str,
    event_type: str,
    target: str | None,
    actor: str,
    seq: int,
    uid: str,
) -> str:
    """Build a canonical event filename from its components."""
    if not _ISO_MS_RE.match(iso_ms):
        raise FilenameError(f"iso_ms {iso_ms!r} must match YYYYMMDDTHHMMSS.mmm")
    if not _EVENT_TYPE_RE.match(event_type):
        raise FilenameError(f"event_type {event_type!r} must be dotted lowercase")
    if not _UID_RE.match(uid):
        raise FilenameError(f"uid {uid!r} must be 16 lowercase hex chars")
    if not (0 <= seq <= 9999):
        raise FilenameError(f"seq {seq} must be in 0..9999")
    escaped_target = escape_label(target) if target else "none"
    if not _TARGET_OR_NONE_RE.match(escaped_target):
        raise FilenameError(
            f"target component {escaped_target!r} must be ``none`` or a ``prefix_slug`` label"
        )
    escaped_actor = _escape_actor(actor)
    if not _ACTOR_RE.match(escaped_actor):
        raise FilenameError(f"actor {actor!r} does not match expected shape")

    return _SEP.join(
        [
            iso_ms,
            event_type,
            escaped_target,
            escaped_actor,
            f"{seq:04d}",
            uid,
        ]
    ) + ".json"


def parse_filename(name: str) -> ParsedFilename:
    """Parse a canonical event filename. Opposite of :func:`format_filename`."""
    if not name.endswith(".json"):
        raise FilenameError(f"expected .json extension: {name!r}")
    stem = name[: -len(".json")]
    parts = _SEP_RE.split(stem)
    if len(parts) != 6:
        raise FilenameError(
            f"expected 6 ``--``-separated components, got {len(parts)}: {name!r}"
        )

    iso_ms, event_type, target_escaped, actor_escaped, seq_str, uid = parts
    if not _ISO_MS_RE.match(iso_ms):
        raise FilenameError(f"bad iso_ms {iso_ms!r} in {name!r}")
    if not _EVENT_TYPE_RE.match(event_type):
        raise FilenameError(f"bad event_type {event_type!r} in {name!r}")
    if not _TARGET_OR_NONE_RE.match(target_escaped):
        raise FilenameError(f"bad target {target_escaped!r} in {name!r}")
    if not _ACTOR_RE.match(actor_escaped):
        raise FilenameError(f"bad actor {actor_escaped!r} in {name!r}")
    if not _SEQ_RE.match(seq_str):
        raise FilenameError(f"bad seq {seq_str!r} in {name!r}")
    if not _UID_RE.match(uid):
        raise FilenameError(f"bad uid {uid!r} in {name!r}")

    target = None if target_escaped == "none" else target_escaped.replace("_", ":", 1)
    actor = _unescape_actor(actor_escaped)
    return ParsedFilename(
        iso_ms=iso_ms,
        event_type=event_type,
        target=target,
        actor=actor,
        seq=int(seq_str),
        uid=uid,
    )


def parse_iso_ms(iso_ms: str) -> datetime:
    """Parse a §3.2 ``iso_ms`` string back into a UTC :class:`datetime`."""
    if not _ISO_MS_RE.match(iso_ms):
        raise FilenameError(f"iso_ms {iso_ms!r} must match YYYYMMDDTHHMMSS.mmm")
    dt = datetime.strptime(iso_ms, "%Y%m%dT%H%M%S.%f")
    return dt.replace(tzinfo=timezone.utc)
