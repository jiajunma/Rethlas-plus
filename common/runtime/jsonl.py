"""Atomic append helpers for runtime JSONL histories.

ARCHITECTURE §6.7.1 requires ``rejected_writes.jsonl`` and
``drift_alerts.jsonl`` writers to:

- cap ``detail`` at 1024 bytes
- cap the full line at 2048 bytes
- use ``O_APPEND | O_CLOEXEC``
- emit each record with a single ``write(2)`` call

This module centralises that contract so all producers behave the same.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DETAIL_CAP_BYTES = 1024
LINE_CAP_BYTES = 2048
TRUNCATION_SUFFIX = "...(truncated)"


def _truncate_utf8(text: str, max_bytes: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    suffix = TRUNCATION_SUFFIX.encode("utf-8")
    if max_bytes <= len(suffix):
        return TRUNCATION_SUFFIX[: max(0, max_bytes)]
    clipped = raw[: max_bytes - len(suffix)].decode("utf-8", errors="ignore")
    return clipped + TRUNCATION_SUFFIX


def _serialise_entry(entry: dict[str, Any]) -> bytes:
    body = json.dumps(
        entry, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ) + "\n"
    return body.encode("utf-8")


def append_jsonl(path: Path | str, entry: dict[str, Any]) -> None:
    """Append one JSON object as a single bounded JSONL line."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(entry)
    detail = payload.get("detail")
    if isinstance(detail, str):
        payload["detail"] = _truncate_utf8(detail, DETAIL_CAP_BYTES)

    raw = _serialise_entry(payload)
    if len(raw) > LINE_CAP_BYTES and isinstance(payload.get("detail"), str):
        detail_text = payload["detail"]
        lo = 0
        hi = len(detail_text.encode("utf-8"))
        best = ""
        while lo <= hi:
            mid = (lo + hi) // 2
            payload["detail"] = _truncate_utf8(detail_text, mid)
            candidate = _serialise_entry(payload)
            if len(candidate) <= LINE_CAP_BYTES:
                best = payload["detail"]
                raw = candidate
                lo = mid + 1
            else:
                hi = mid - 1
        payload["detail"] = best
        raw = _serialise_entry(payload)

    if len(raw) > LINE_CAP_BYTES:
        raise ValueError(
            f"jsonl line exceeds {LINE_CAP_BYTES} bytes even after truncation"
        )

    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    fd = os.open(str(p), flags, 0o644)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)


__all__ = [
    "DETAIL_CAP_BYTES",
    "LINE_CAP_BYTES",
    "TRUNCATION_SUFFIX",
    "append_jsonl",
]
