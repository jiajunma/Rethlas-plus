"""Dashboard KB client over librarian's QUERY IPC."""

from __future__ import annotations

import errno
import json
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cli.workspace import workspace_paths
from librarian.heartbeat import read_heartbeat as read_librarian_hb


class RebuildInProgress(RuntimeError):
    """Signals that the projection is being rebuilt."""


class KBUnavailable(RuntimeError):
    """Librarian query endpoint is not available."""


@dataclass(frozen=True, slots=True)
class NodeRow:
    label: str
    kind: str
    statement: str
    proof: str
    pass_count: int
    repair_count: int
    statement_hash: str
    verification_hash: str
    repair_hint: str
    verification_report: str
    deps: tuple[str, ...]
    introduced_by_actor: str = "user:cli"


def _check_rebuild(state_dir: Path) -> None:
    hb = read_librarian_hb(state_dir / "librarian.json")
    if hb and hb.get("rebuild_in_progress"):
        raise RebuildInProgress("rebuild_in_progress")


def _query(ws_root: Path, op: str, args: dict[str, Any] | None = None) -> Any:
    ws = workspace_paths(str(ws_root))
    sock_path = ws.librarian_socket
    payload = {"cmd": "QUERY", "op": op, "args": args or {}}
    deadline = time.monotonic() + 1.0
    line = b""
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(5.0)
                sock.connect(str(sock_path))
                raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
                sock.sendall(raw)
                fh = sock.makefile("rb")
                line = fh.readline()
            break
        except OSError as exc:
            last_error = str(exc)
            if exc.errno not in {errno.ENOENT, errno.ECONNREFUSED}:
                break
            time.sleep(0.05)
    if not line:
        if last_error:
            raise KBUnavailable(f"librarian query socket unavailable: {sock_path} ({last_error})")
        raise KBUnavailable("librarian query returned EOF")
    reply = json.loads(line)
    if not isinstance(reply, dict):
        raise KBUnavailable("invalid query reply")
    if not reply.get("ok"):
        raise KBUnavailable(reply.get("detail") or reply.get("error") or "query_failed")
    return reply.get("result")


def list_nodes(ws_root: Path) -> list[NodeRow]:
    _check_rebuild(ws_root / "runtime" / "state")
    rows = _query(ws_root, "list_nodes")
    out: list[NodeRow] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        deps = tuple(d for d in (row.get("deps") or []) if isinstance(d, str))
        out.append(
            NodeRow(
                label=row.get("label", ""),
                kind=row.get("kind", ""),
                statement=row.get("statement", ""),
                proof=row.get("proof", ""),
                pass_count=int(row.get("pass_count", -1)),
                repair_count=int(row.get("repair_count", 0)),
                statement_hash=row.get("statement_hash", ""),
                verification_hash=row.get("verification_hash", ""),
                repair_hint=row.get("repair_hint", ""),
                verification_report=row.get("verification_report", ""),
                deps=deps,
                introduced_by_actor=row.get("introduced_by_actor", "user:cli")
                or "user:cli",
            )
        )
    return out


def list_applied_failed(ws_root: Path) -> list[dict[str, Any]]:
    _check_rebuild(ws_root / "runtime" / "state")
    rows = _query(ws_root, "list_applied_failed")
    return rows if isinstance(rows, list) else []


def dependents_of(ws_root: Path, label: str) -> list[str]:
    _check_rebuild(ws_root / "runtime" / "state")
    rows = _query(ws_root, "dependents_of", {"label": label})
    return [r for r in (rows or []) if isinstance(r, str)]


def list_applied_since(
    ws_root: Path,
    watermark: tuple[str, str],
) -> list[dict[str, Any]]:
    _check_rebuild(ws_root / "runtime" / "state")
    rows = _query(ws_root, "list_applied_since", {"watermark": list(watermark)})
    return rows if isinstance(rows, list) else []


__all__ = [
    "KBUnavailable",
    "NodeRow",
    "RebuildInProgress",
    "dependents_of",
    "list_applied_failed",
    "list_applied_since",
    "list_nodes",
]
