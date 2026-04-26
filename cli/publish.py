"""Shared user-CLI publish path (ARCHITECTURE §9.1 user-CLI branch).

The user CLI:
1. Builds the event body (§3.3 / §3.4 schema).
2. Allocates an ``event_id`` (§3.2) using a fresh :class:`EventIdAllocator`.
3. Runs pre-publish admission (:func:`librarian.validator.validate_admission`)
   with a workspace-backed ``current_kind_of`` lookup so ``user.node_revised``
   can enforce kind-immutability.
4. On rejection — appends a line to ``runtime/state/rejected_writes.jsonl``
   and returns a non-zero exit code. No file enters ``events/``.
5. On success — writes the event atomically under ``events/{YYYY-MM-DD}/``.
6. Polls librarian's read-only `QUERY(applied_event_status)` API for up
   to 30 s. Reports per the §9.1 D2 table.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from cli.workspace import WorkspacePaths
from common.events.filenames import escape_label, format_filename
from common.events.ids import EventIdAllocator
from common.events.io import atomic_write_event
from common.runtime.jsonl import append_jsonl
from librarian.validator import AdmissionError, validate_admission


_POLL_TIMEOUT_S_DEFAULT = 30.0
_POLL_INTERVAL_S = 0.5


def _poll_timeout_s() -> float:
    """Honour ``$RETHLAS_PUBLISH_POLL_TIMEOUT_S`` so tests can run fast.

    The env var is intentional: workers and CI do not need to block for
    30 seconds when we know the AppliedEvent row is never going to land.
    """
    val = os.environ.get("RETHLAS_PUBLISH_POLL_TIMEOUT_S")
    if val is None:
        return _POLL_TIMEOUT_S_DEFAULT
    try:
        return max(0.0, float(val))
    except ValueError:
        return _POLL_TIMEOUT_S_DEFAULT


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    exit_code: int
    message: str
    event_id: str | None = None


def _utc_now_ms_iso() -> str:
    now = datetime.now(tz=timezone.utc)
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _local_offset_iso() -> str:
    # Truth event body's `ts` keeps the local-offset form (§2.4 trailer).
    local = datetime.now().astimezone()
    return local.isoformat(timespec="milliseconds")


def _current_kind_lookup(ws: WorkspacePaths) -> Callable[[str], str | None]:
    """Factory returning a ``current_kind_of(label)`` the admission uses.

    Under the revised §4.1, only librarian opens the DB. User CLI asks
    librarian over its query socket when available; otherwise we return
    ``None`` (admission skips kind-immutability) and rely on the
    projector as the authoritative check.
    """
    def lookup(label: str) -> str | None:
        try:
            result = _query_librarian(ws, "current_kind_of", {"label": label})
        except OSError:
            return None
        return result if isinstance(result, str) else None

    return lookup


def _rejected_writes_append(ws: WorkspacePaths, entry: dict[str, Any]) -> None:
    path = ws.rejected_writes_jsonl
    append_jsonl(path, entry)


def _compose_event(
    *,
    etype: str,
    actor: str,
    target: str | None,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    alloc = EventIdAllocator()
    eid = alloc.allocate()
    body: dict[str, Any] = {
        "event_id": eid.event_id,
        "type": etype,
        "actor": actor,
        "ts": _local_offset_iso(),
        "payload": payload,
    }
    if target is not None:
        body["target"] = target
    return body, eid.iso_ms, eid.uid


def _events_date_dir(ws: WorkspacePaths, iso_ms: str) -> Path:
    # events/{YYYY-MM-DD}/ per §3.2.
    yyyymmdd = iso_ms[:8]
    date = f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
    d = ws.events / date
    d.mkdir(parents=True, exist_ok=True)
    return d


def _poll_applied_event(
    ws: WorkspacePaths, event_id: str, timeout_s: float | None = None
) -> dict[str, Any] | None:
    if timeout_s is None:
        timeout_s = _poll_timeout_s()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            result = _query_librarian(ws, "applied_event_status", {"event_id": event_id})
        except OSError:
            result = None
        if isinstance(result, dict):
            return {
                "status": result.get("status", ""),
                "reason": result.get("reason", ""),
                "detail": result.get("detail", ""),
            }
        time.sleep(_POLL_INTERVAL_S)
    return None


def _query_librarian(
    ws: WorkspacePaths,
    op: str,
    args: dict[str, Any],
) -> Any:
    sock_path = ws.librarian_socket
    if not sock_path.exists():
        raise FileNotFoundError(sock_path)
    payload = {"cmd": "QUERY", "op": op, "args": args}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(2.0)
        sock.connect(str(sock_path))
        sock.sendall((json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        fh = sock.makefile("rb")
        line = fh.readline()
    if not line:
        raise OSError("librarian query EOF")
    reply = json.loads(line)
    if not isinstance(reply, dict) or not reply.get("ok"):
        raise OSError(str(reply))
    return reply.get("result")


def _supervise_lock_held(ws: WorkspacePaths) -> bool:
    """Best-effort check whether someone holds ``runtime/locks/supervise.lock``.

    We try to acquire a non-blocking ``flock`` on the lock file; if the
    acquisition fails with :data:`errno.EWOULDBLOCK` the lock is held.
    If the file doesn't exist at all we treat supervise as not running.
    """
    import errno
    import fcntl

    p = ws.supervise_lock
    if not p.is_file():
        return False
    try:
        fd = os.open(str(p), os.O_RDWR)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # we got the lock — no one else holds it. Release immediately.
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                return True
            return False
    finally:
        os.close(fd)


def publish(
    ws: WorkspacePaths,
    *,
    etype: str,
    actor: str,
    target: str | None,
    payload: dict[str, Any],
) -> PublishOutcome:
    """Admission -> atomic write -> AppliedEvent poll."""
    body, iso_ms, uid = _compose_event(
        etype=etype, actor=actor, target=target, payload=payload
    )

    # Pre-publish admission (§3.1.6).
    try:
        validate_admission(body, current_kind_of=_current_kind_lookup(ws))
    except AdmissionError as exc:
        _rejected_writes_append(
            ws,
            {
                "ts": _utc_now_ms_iso(),
                "event_id": body["event_id"],
                "event_type": etype,
                "actor": actor,
                "target": target,
                "reason": "admission_rejected",
                "detail": str(exc),
            },
        )
        sys.stderr.write(f"rejected: {exc}\n")
        return PublishOutcome(
            exit_code=3,
            message=f"rejected: {exc}",
            event_id=body["event_id"],
        )

    # Write atomically.
    date_dir = _events_date_dir(ws, iso_ms)
    filename = format_filename(
        iso_ms=iso_ms,
        event_type=etype,
        target=target,
        actor=actor,
        seq=_seq_from_event_id(body["event_id"]),
        uid=uid,
    )
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    atomic_write_event(date_dir / filename, raw)
    sys.stdout.write(f"published {body['event_id']} -> {date_dir / filename}\n")

    # Poll AppliedEvent (§9.1 D2).
    applied = _poll_applied_event(ws, body["event_id"])
    if applied is not None:
        status = applied["status"]
        if status == "applied":
            sys.stdout.write(f"applied\n")
            return PublishOutcome(
                exit_code=0, message="applied", event_id=body["event_id"]
            )
        reason = applied["reason"] or "?"
        detail = applied["detail"] or ""
        sys.stdout.write(f"apply_failed: {reason} — {detail}\n")
        return PublishOutcome(
            exit_code=0,
            message=f"apply_failed: {reason}",
            event_id=body["event_id"],
        )

    if _supervise_lock_held(ws):
        sys.stdout.write("queued; librarian is slow / behind — will apply when it catches up\n")
        return PublishOutcome(
            exit_code=0, message="queued_librarian_behind", event_id=body["event_id"]
        )
    sys.stdout.write("queued; run `rethlas supervise` to apply\n")
    return PublishOutcome(
        exit_code=0, message="queued_supervise_not_running", event_id=body["event_id"]
    )


def _seq_from_event_id(event_id: str) -> int:
    # event_id = "{iso_ms}-{seq:04d}-{uid}"; split on "-" and take the
    # second-to-last component. The iso_ms itself contains no "-".
    m = re.match(r"^(?P<iso>\d{8}T\d{6}\.\d{3})-(?P<seq>\d{4})-(?P<uid>[0-9a-f]{16})$", event_id)
    if m is None:
        raise ValueError(f"unparseable event_id: {event_id!r}")
    return int(m.group("seq"))
