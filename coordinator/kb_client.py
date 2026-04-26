"""Coordinator KB client over librarian's QUERY socket."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cli.workspace import WorkspacePaths


@dataclass(frozen=True, slots=True)
class AppliedRow:
    event_id: str
    status: str
    reason: str
    detail: str


def _query(ws: WorkspacePaths, op: str, args: dict[str, Any] | None = None) -> Any:
    sock_path = ws.librarian_socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5.0)
        sock.connect(str(sock_path))
        payload = {"cmd": "QUERY", "op": op, "args": args or {}}
        sock.sendall((json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        fh = sock.makefile("rb")
        line = fh.readline()
    if not line:
        return None
    reply = json.loads(line)
    if not isinstance(reply, dict) or not reply.get("ok"):
        return None
    return reply.get("result")


def coordinator_snapshot(ws: WorkspacePaths) -> list[dict[str, Any]] | None:
    result = _query(ws, "coordinator_snapshot")
    return result if isinstance(result, list) else None


def applied_event_status(ws: WorkspacePaths, event_id: str) -> AppliedRow | None:
    result = _query(ws, "applied_event_status", {"event_id": event_id})
    if not isinstance(result, dict):
        return None
    status = result.get("status")
    if not isinstance(status, str):
        return None
    return AppliedRow(
        event_id=event_id,
        status=status,
        reason=result.get("reason", "") or "",
        detail=result.get("detail", "") or "",
    )


__all__ = ["AppliedRow", "applied_event_status", "coordinator_snapshot"]
