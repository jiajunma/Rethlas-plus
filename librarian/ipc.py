"""JSON-line command channel between coordinator and librarian.

ARCHITECTURE §6.5 picks "stdio-pipe JSON-RPC" as the Phase I command
channel. We implement that pattern here: every line on the channel is a
self-contained UTF-8 JSON object terminated by ``\\n``. A line that
fails to parse is a fatal protocol error — the receiver closes the
channel and lets supervise bring the children back.

Commands (coordinator -> librarian)::

    {"cmd": "APPLY", "event_id": "...", "path": "events/.../foo.json"}
    {"cmd": "REBUILD"}
    {"cmd": "PING"}
    {"cmd": "SHUTDOWN"}

Replies (librarian -> coordinator)::

    {"ok": true, "reply": "APPLIED",       "event_id": "..."}
    {"ok": true, "reply": "APPLY_FAILED",  "event_id": "...", "reason": "...", "detail": "..."}
    {"ok": true, "reply": "CORRUPTION",    "event_id": "...", "detail": "..."}
    {"ok": true, "reply": "REBUILD_DONE"}
    {"ok": true, "reply": "PONG", "phase": "replaying"|"reconciling"|"ready"}
    {"ok": false, "error": "..."}

The channel object is binary-safe (BufferedReader / BufferedWriter) and
explicitly flushes after every write so that interactive testing through
``Popen.communicate`` works without surprises.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, BinaryIO


@dataclass(frozen=True, slots=True)
class Message:
    """A parsed JSON-line message. ``raw`` is the original bytes for logging."""

    payload: dict[str, Any]
    raw: bytes


class JsonLineChannel:
    """Bidirectional JSON-line channel.

    One side reads from ``rx`` and writes to ``tx``. We keep a lock per
    instance so concurrent ``send`` calls from threading-based readers do
    not interleave bytes on the wire.
    """

    def __init__(self, rx: BinaryIO, tx: BinaryIO) -> None:
        self._rx = rx
        self._tx = tx
        self._tx_lock = threading.Lock()

    # ---- low-level read / write ---------------------------------------
    def recv(self) -> Message | None:
        line = self._rx.readline()
        if not line:
            return None
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"malformed JSON line: {line!r}") from exc
        if not isinstance(payload, dict):
            raise ProtocolError(f"expected JSON object, got {type(payload).__name__}")
        return Message(payload=payload, raw=line)

    def send(self, payload: dict[str, Any]) -> None:
        line = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        with self._tx_lock:
            self._tx.write(line)
            self._tx.flush()

    def close(self) -> None:
        try:
            self._tx.flush()
        except Exception:
            pass


class ProtocolError(Exception):
    """Raised when a peer sends a non-JSON line or wrong shape."""


# ---------------------------------------------------------------------------
# Convenience factories — used by tests and the librarian entry point.
# ---------------------------------------------------------------------------
def make_apply_command(event_id: str, path: str) -> dict[str, Any]:
    return {"cmd": "APPLY", "event_id": event_id, "path": str(path)}


def make_rebuild_command() -> dict[str, Any]:
    return {"cmd": "REBUILD"}


def make_ping_command() -> dict[str, Any]:
    return {"cmd": "PING"}


def make_shutdown_command() -> dict[str, Any]:
    return {"cmd": "SHUTDOWN"}


__all__ = [
    "JsonLineChannel",
    "Message",
    "ProtocolError",
    "make_apply_command",
    "make_ping_command",
    "make_rebuild_command",
    "make_shutdown_command",
]
