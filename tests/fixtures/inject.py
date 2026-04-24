"""Direct-into-storage helpers.

These helpers bypass admission / librarian so tests can set up specific
KB states cheaply. Kuzu-backed helpers (``inject_applied_event``,
``inject_node``) land when M2 implements the backing store; for M1 they
are placeholders that raise a clear error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def inject_event_file(events_dir: Path, name: str, body: bytes) -> Path:
    """Drop a raw event file into ``events/{date}/`` without admission.

    Useful for librarian replay tests that need specific iso_ms ordering.
    ``name`` must be a full canonical filename.
    """
    date_dir = events_dir
    date_dir.mkdir(parents=True, exist_ok=True)
    target = date_dir / name
    target.write_bytes(body)
    return target


def inject_node(*args: Any, **kwargs: Any) -> None:  # pragma: no cover - M2+
    raise NotImplementedError("inject_node requires the Kuzu backend from M2")


def inject_applied_event(*args: Any, **kwargs: Any) -> None:  # pragma: no cover - M2+
    raise NotImplementedError(
        "inject_applied_event requires the Kuzu backend from M2"
    )


__all__ = [
    "inject_event_file",
    "inject_node",
    "inject_applied_event",
]
