"""Write an event file with precisely specified raw bytes.

Used by ``AppliedEvent.event_sha256`` determinism tests and linter
category-F tampering fixtures.
"""

from __future__ import annotations

from pathlib import Path


def write_event_with_bytes(
    canonical_path: Path | str,
    raw_bytes: bytes,
) -> Path:
    """Write ``raw_bytes`` verbatim to ``canonical_path``.

    Does **not** go through :func:`common.events.io.atomic_write_event` —
    callers that want to simulate tampering need byte control, not the
    fsync dance.
    """
    p = Path(canonical_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(raw_bytes)
    return p


__all__ = ["write_event_with_bytes"]
