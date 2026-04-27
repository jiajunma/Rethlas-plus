"""Librarian daemon — passive APPLY handler + startup replay/reconciliation.

ARCHITECTURE §6.5: the librarian is a *child subprocess of the
coordinator* connected via a stdio JSON-line channel (:mod:`.ipc`).
This module wires together:

1. **Startup**: detect ``rebuild_in_progress.flag`` → force rebuild;
   else walk ``events/`` in ``(iso_ms, seq, uid)`` order and apply any
   not-yet-decided events; reconcile ``nodes/`` against Kuzu.
2. **Steady state**: read APPLY commands from the coordinator, apply
   each event, render any affected ``nodes/*.md``, reply with the
   outcome. Periodic heartbeats refresh ``runtime/state/librarian.json``.
3. **Queueing**: APPLY commands received during ``replaying`` or
   ``reconciling`` are queued and processed exactly once after
   ``ready``. Tested by M4's "APPLY-during-startup queuing" case.
4. **Shutdown**: a ``SHUTDOWN`` command (or stdin EOF) stops the
   reader loop; the daemon flushes its final heartbeat and exits 0.

Threading model:
- Main thread = startup + APPLY processing.
- A reader thread parses stdin into a :class:`queue.Queue` so the main
  thread can dequeue APPLY commands during reconciliation without
  blocking the channel.
- A heartbeat-pulse thread writes ``runtime/state/librarian.json`` on
  a fixed cadence independent of dispatch, using a non-blocking
  :class:`threading.RLock` acquire on ``_kb_lock`` so a long-running
  apply cannot make the dashboard report "down" while the daemon is
  still healthy and making progress.

The daemon does NOT watch ``events/`` itself (§6.5 paragraph 1) — the
coordinator owns that watchdog and forwards each new event via APPLY.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import queue
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterable

from cli.workspace import WorkspacePaths
from common.events.io import event_sha256, read_event
from common.events.filenames import parse_filename
from common.kb.kuzu_backend import KuzuBackend
from common.kb.types import Node, NodeKind
from librarian.heartbeat import (
    LibrarianHeartbeat,
    PHASE_READY,
    PHASE_RECONCILING,
    PHASE_REPLAYING,
    STATUS_DEGRADED,
    STATUS_REBUILDING,
    STATUS_RUNNING,
    utc_now_iso,
    write_heartbeat,
)
from librarian.ipc import JsonLineChannel, Message, ProtocolError
from librarian.projector import Projector, ProjectionRejection
from librarian.query_server import LibrarianQueryServer, QueryServerError
from librarian.renderer import node_filename, write_node_file


# Idle heartbeat cadence (§6.5). Tests can shrink this via
# ``RETHLAS_LIBRARIAN_HEARTBEAT_S``.
_DEFAULT_HEARTBEAT_S = 30.0

# Reconciliation ignores any file under nodes/ that does not have the
# expected ``{prefix}_{slug}.md`` shape — leaving operator notes alone.
_KNOWN_PREFIXES = {"def", "ext", "lem", "thm", "prop"}


@dataclass(slots=True)
class _Counters:
    applied: int = 0
    failed: int = 0
    last_seen: str = ""
    last_applied: str = ""
    last_error: str = ""


class _CorruptionHalt(RuntimeError):
    """Canonical event corruption must halt projection immediately."""


def _heartbeat_interval() -> float:
    raw = os.environ.get("RETHLAS_LIBRARIAN_HEARTBEAT_S")
    if raw is None:
        return _DEFAULT_HEARTBEAT_S
    try:
        return max(0.05, float(raw))
    except ValueError:
        return _DEFAULT_HEARTBEAT_S


# ---------------------------------------------------------------------------
# Reader thread — drains stdin into a queue.
# ---------------------------------------------------------------------------
def _reader_thread(channel: JsonLineChannel, q: "queue.Queue[Message | None]") -> None:
    try:
        while True:
            msg = channel.recv()
            if msg is None:
                q.put(None)
                return
            q.put(msg)
    except ProtocolError as exc:
        q.put(None)
        sys.stderr.write(f"librarian: protocol error: {exc}\n")


# ---------------------------------------------------------------------------
# Daemon.
# ---------------------------------------------------------------------------
class LibrarianDaemon:
    """One librarian process. ``run()`` blocks until shutdown / EOF."""

    def __init__(
        self,
        ws: WorkspacePaths,
        *,
        rx: BinaryIO,
        tx: BinaryIO,
        heartbeat_interval: float | None = None,
    ) -> None:
        self.ws = ws
        self.channel = JsonLineChannel(rx=rx, tx=tx)
        self.heartbeat_interval = (
            heartbeat_interval if heartbeat_interval is not None else _heartbeat_interval()
        )
        self._cmd_queue: "queue.Queue[Message | None]" = queue.Queue()
        self._reader: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._shutdown = threading.Event()

        self.started_at = utc_now_iso()
        self.counters = _Counters()
        self.phase = PHASE_REPLAYING
        self.status = STATUS_RUNNING
        self.last_rebuild_at: str | None = None
        self.rebuild_in_progress = False

        self.backend: KuzuBackend | None = None
        self.projector: Projector | None = None
        self._query_server: LibrarianQueryServer | None = None
        self._kb_lock = threading.RLock()

        self._lock_fd: int | None = None

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------
    def _heartbeat(self) -> None:
        events_root = self.ws.events
        files = list(events_root.rglob("*.json")) if events_root.is_dir() else []
        decided = self.counters.applied + self.counters.failed
        applied_total = self.counters.applied
        failed_total = self.counters.failed
        last_applied = self.counters.last_applied
        # Try to refresh from KB, but never block — a heavy apply
        # holding ``_kb_lock`` must not stall heartbeat (otherwise the
        # dashboard reports "down" even though the librarian is busy
        # making progress). Fall back to the in-memory counters when the
        # lock is contended.
        if self.backend is not None and self._kb_lock.acquire(blocking=False):
            try:
                decided, applied_total, failed_total = self.backend.applied_event_counts()
                last_applied = self.backend.last_applied_event_id() or last_applied
            except Exception:
                pass
            finally:
                self._kb_lock.release()
        backlog = max(0, len(files) - decided)
        hb = LibrarianHeartbeat(
            pid=os.getpid(),
            started_at=self.started_at,
            updated_at=utc_now_iso(),
            status=self.status,
            startup_phase=self.phase,
            last_seen_event_id=self.counters.last_seen,
            last_applied_event_id=last_applied,
            events_applied_total=applied_total,
            events_apply_failed_total=failed_total,
            projection_backlog=backlog,
            rebuild_in_progress=self.rebuild_in_progress,
            last_rebuild_at=self.last_rebuild_at,
            last_error=self.counters.last_error,
        )
        write_heartbeat(self.ws.runtime_state / "librarian.json", hb)

    def _heartbeat_pulse(self) -> None:
        """Tick a fresh heartbeat every ``heartbeat_interval`` seconds.

        Runs in its own thread so a long-running apply holding
        ``_kb_lock`` cannot stall liveness reporting. Heartbeat itself
        uses a non-blocking lock acquire (see ``_heartbeat``), so the
        pulse never waits on the dispatcher.
        """
        while not self._shutdown.is_set():
            if self._shutdown.wait(self.heartbeat_interval):
                return
            try:
                self._heartbeat()
            except Exception:
                # Heartbeat must never kill the daemon. Lose this tick
                # and try again on the next interval.
                pass

    # ------------------------------------------------------------------
    # Lock
    # ------------------------------------------------------------------
    def _take_runtime_lock(self) -> None:
        """A lighter-weight lock that prevents two librarians on one workspace.

        Coordinator-level locking lives at ``runtime/locks/supervise.lock``;
        this runtime lock guards against a librarian started outside
        coordinator (test harnesses, manual invocation) racing a real
        supervise. We use a dedicated file so we do not interfere with
        the supervise lock's own contract.
        """
        self.ws.runtime_locks.mkdir(parents=True, exist_ok=True)
        path = self.ws.runtime_locks / "librarian.lock"
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise RuntimeError(
                    "another librarian holds runtime/locks/librarian.lock"
                )
            raise
        self._lock_fd = fd

    def _release_runtime_lock(self) -> None:
        if self._lock_fd is None:
            return
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(self._lock_fd)
            self._lock_fd = None

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------
    def run(self) -> int:
        try:
            self._take_runtime_lock()
        except RuntimeError as exc:
            sys.stderr.write(f"librarian: {exc}\n")
            return 1

        # Start reader before doing anything heavy; that way a coordinator
        # that rushed APPLY commands before we were ready does not block
        # on its write.
        self._reader = threading.Thread(
            target=_reader_thread, args=(self.channel, self._cmd_queue), daemon=True
        )
        self._reader.start()

        # Background heartbeat pulse. Dispatching a single APPLY can hold
        # the loop thread for many seconds (Merkle cascade, BFS cycle
        # check on a large graph), and the dashboard's 60s healthy / 300s
        # down thresholds make a long apply look like a crashed daemon.
        # The pulse thread writes a fresh ``librarian.json`` independently
        # of dispatch so liveness reflects "the process is alive" rather
        # than "the process is also currently idle".
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_pulse, daemon=True, name="librarian-hb"
        )
        self._heartbeat_thread.start()

        try:
            self.backend = KuzuBackend(self.ws.dag_kz)
            self.projector = Projector(self.backend)
            self._query_server = LibrarianQueryServer(
                self.ws.librarian_socket, self._dispatch_query
            )
            self._query_server.start()

            # Detect interrupted rebuild and force a clean rebuild before
            # accepting any normal APPLY commands.
            if self.ws.rebuild_flag.is_file():
                self.rebuild_in_progress = True
                self.status = STATUS_REBUILDING
                self._heartbeat()
                self._do_rebuild()

            # Phase 1: replay.
            self.phase = PHASE_REPLAYING
            self._heartbeat()
            self._startup_replay()

            # Phase 2: reconcile.
            self.phase = PHASE_RECONCILING
            self._heartbeat()
            self._reconcile_nodes_dir()

            # Phase 3: ready — drain queued APPLYs and process new ones.
            self.phase = PHASE_READY
            self._heartbeat()

            self._steady_state_loop()
        except Exception as exc:  # pragma: no cover — surfaces as last_error
            sys.stderr.write(f"librarian: fatal error: {exc}\n")
            self.status = STATUS_DEGRADED
            self.counters.last_error = str(exc)
            try:
                self._heartbeat()
            except Exception:
                pass
            return 1
        finally:
            try:
                if self.backend is not None:
                    self.backend.close()
            finally:
                if self._query_server is not None:
                    self._query_server.stop()
                self._release_runtime_lock()
        return 0

    # ------------------------------------------------------------------
    # Startup helpers
    # ------------------------------------------------------------------
    def _do_rebuild(self) -> None:
        """Wipe + replay every event in ``events/``. Called when the
        ``rebuild_in_progress.flag`` is found (interrupted rebuild)."""
        assert self.backend is not None
        # Wipe Kuzu projection + nodes/ render.
        with self._kb_lock:
            self.backend.wipe()
        if self.ws.nodes_dir.is_dir():
            for child in self.ws.nodes_dir.iterdir():
                try:
                    if child.is_file():
                        child.unlink()
                    else:
                        shutil.rmtree(child)
                except FileNotFoundError:
                    pass
        # Replay every event using the same machinery.
        for path in _events_in_order(self.ws.events):
            status, _reason, detail = self._apply_path(path, render_nodes=False)
            if status == "corruption":
                raise _CorruptionHalt(detail or f"workspace corruption at {path}")
        # Render nodes/ from final Kuzu state.
        self._render_all_published_nodes()
        # Clear flag.
        try:
            self.ws.rebuild_flag.unlink()
        except FileNotFoundError:
            pass
        self.last_rebuild_at = utc_now_iso()
        self.rebuild_in_progress = False
        self.status = STATUS_RUNNING

    def _startup_replay(self) -> None:
        """Idempotent walk over ``events/``; skip already-decided rows."""
        assert self.backend is not None
        for path in _events_in_order(self.ws.events):
            status, _reason, detail = self._apply_path(path, render_nodes=True)
            self._heartbeat()
            if status == "corruption":
                raise _CorruptionHalt(detail or f"workspace corruption at {path}")

    def _reconcile_nodes_dir(self) -> None:
        """Heal stale ``nodes/*.md`` (crash window) and delete orphans.

        For every ``Node`` with ``pass_count >= 1``, render and write.
        For every file under ``nodes/`` that is not a known prefix or
        does not match an active node, delete it.
        """
        assert self.backend is not None
        self.ws.nodes_dir.mkdir(parents=True, exist_ok=True)

        active_files: set[str] = set()
        with self._kb_lock:
            for label in self.backend.node_labels():
                row = self.backend.node_by_label(label)
                if row is None or row.pass_count < 1:
                    continue
                node = _row_to_node(row, deps=self.backend.dependencies_of(label))
                try:
                    fname = node_filename(node)
                except ValueError:
                    continue
                written = write_node_file(self.ws.nodes_dir, node)
                active_files.add(written.name)

        for entry in self.ws.nodes_dir.iterdir():
            if not entry.is_file():
                continue
            if entry.name.endswith(".tmp"):
                # Stray tmp from an interrupted write.
                try:
                    entry.unlink()
                except FileNotFoundError:
                    pass
                continue
            if entry.name in active_files:
                continue
            prefix = entry.name.split("_", 1)[0]
            if prefix not in _KNOWN_PREFIXES:
                # Operator-owned notes: leave alone.
                continue
            try:
                entry.unlink()
            except FileNotFoundError:
                pass

    def _render_all_published_nodes(self) -> None:
        """Re-render every ``pass_count >= 1`` node into ``nodes/``."""
        assert self.backend is not None
        self.ws.nodes_dir.mkdir(parents=True, exist_ok=True)
        with self._kb_lock:
            for label in self.backend.node_labels():
                row = self.backend.node_by_label(label)
                if row is None or row.pass_count < 1:
                    continue
                node = _row_to_node(row, deps=self.backend.dependencies_of(label))
                try:
                    write_node_file(self.ws.nodes_dir, node)
                except ValueError:
                    continue

    # ------------------------------------------------------------------
    # APPLY processing
    # ------------------------------------------------------------------
    def _apply_path(self, path: Path, *, render_nodes: bool) -> tuple[str, str | None, str | None]:
        """Apply a single event file. Returns ``(status, reason, detail)``.

        ``status`` is one of ``"applied"`` / ``"apply_failed"`` /
        ``"corruption"`` (the latter is also reflected in the heartbeat
        as ``status=degraded`` and never marked applied).
        """
        assert self.backend is not None and self.projector is not None
        try:
            raw, body = read_event(path)
        except FileNotFoundError:
            self.counters.last_error = f"corruption: canonical event vanished: {path}"
            self.status = STATUS_DEGRADED
            return ("corruption", "missing_event_file", str(path))
        except ValueError as exc:
            self.counters.last_error = f"malformed event {path}: {exc}"
            self.status = STATUS_DEGRADED
            return ("corruption", "malformed_event", str(exc))

        event_id = body.get("event_id", "")
        self.counters.last_seen = event_id
        with self._kb_lock:
            try:
                outcome = self.projector.apply(body, raw)
            except ProjectionRejection as rej:
                # Only ``workspace_corruption`` bubbles out of projector — all
                # routine rejection reasons are already converted to apply_failed
                # rows inside ``Projector.apply``. Surface as degraded.
                self.counters.last_error = f"corruption: {rej.detail}"
                self.status = STATUS_DEGRADED
                return ("corruption", rej.reason, rej.detail)
            except Exception as exc:
                self.counters.last_error = f"apply error: {exc}"
                self.status = STATUS_DEGRADED
                return ("corruption", "apply_exception", str(exc))

            if outcome.status.value == "applied":
                self.counters.applied += 1
                self.counters.last_applied = event_id
                if render_nodes:
                    self._render_for_event(body)
            else:
                self.counters.failed += 1

        return (outcome.status.value, outcome.reason, outcome.detail)

    def _render_for_event(self, body: dict[str, Any]) -> None:
        """Re-render every node touched (directly or via Merkle cascade) by
        the just-applied event. Conservative: re-render every ``pass_count
        >= 1`` node referenced by the event plus its dependents. The full
        reconciliation pass on startup is the safety net for misses.
        """
        assert self.backend is not None
        affected: set[str] = set()
        target = body.get("target")
        if isinstance(target, str):
            affected.add(target)
        payload = body.get("payload", {})
        nodes = payload.get("nodes") if isinstance(payload, dict) else None
        if isinstance(nodes, list):
            for entry in nodes:
                lbl = (entry or {}).get("label")
                if isinstance(lbl, str):
                    affected.add(lbl)

        # BFS dependents of every affected node so a Merkle cascade is rendered.
        seen: set[str] = set()
        to_visit = list(affected)
        while to_visit:
            lbl = to_visit.pop()
            if lbl in seen:
                continue
            seen.add(lbl)
            to_visit.extend(self.backend.dependents_of(lbl))

        for lbl in seen:
            row = self.backend.node_by_label(lbl)
            if row is None:
                # Could be a brand-new dep that never made it to KB —
                # nothing to render.
                continue
            try:
                fname = node_filename(_row_to_node(row, deps=[]))
            except ValueError:
                continue
            target_path = self.ws.nodes_dir / fname
            if row.pass_count >= 1:
                node = _row_to_node(row, deps=self.backend.dependencies_of(lbl))
                write_node_file(self.ws.nodes_dir, node)
            else:
                # Verified -> not verified: delete the rendered file.
                try:
                    target_path.unlink()
                except FileNotFoundError:
                    pass

    # ------------------------------------------------------------------
    # Steady-state loop
    # ------------------------------------------------------------------
    def _steady_state_loop(self) -> None:
        last_hb = time.monotonic()
        while not self._shutdown.is_set():
            timeout = max(0.0, self.heartbeat_interval - (time.monotonic() - last_hb))
            try:
                msg = self._cmd_queue.get(timeout=timeout)
            except queue.Empty:
                self._heartbeat()
                last_hb = time.monotonic()
                continue
            if msg is None:
                # EOF on stdin — coordinator exited; we should too.
                self._heartbeat()
                return
            self._dispatch_command(msg)
            self._heartbeat()
            last_hb = time.monotonic()

    def _dispatch_command(self, msg: Message) -> None:
        cmd = msg.payload.get("cmd")
        if cmd == "APPLY":
            self._handle_apply(msg.payload)
        elif cmd == "REBUILD":
            self._handle_rebuild()
        elif cmd == "PING":
            self.channel.send({"ok": True, "reply": "PONG", "phase": self.phase})
        elif cmd == "SHUTDOWN":
            self.channel.send({"ok": True, "reply": "BYE"})
            self._shutdown.set()
        else:
            self.channel.send({"ok": False, "error": f"unknown cmd {cmd!r}"})

    def _handle_apply(self, payload: dict[str, Any]) -> None:
        event_id = payload.get("event_id", "")
        path = payload.get("path", "")
        if not isinstance(event_id, str) or not isinstance(path, str) or not path:
            self.channel.send(
                {"ok": False, "error": "APPLY requires event_id + path"}
            )
            return
        status, reason, detail = self._apply_path(Path(path), render_nodes=True)
        if status == "applied":
            self.channel.send({"ok": True, "reply": "APPLIED", "event_id": event_id})
        elif status == "apply_failed":
            self.channel.send(
                {
                    "ok": True,
                    "reply": "APPLY_FAILED",
                    "event_id": event_id,
                    "reason": reason or "",
                    "detail": detail or "",
                }
            )
        else:  # corruption
            self.channel.send(
                {
                    "ok": True,
                    "reply": "CORRUPTION",
                    "event_id": event_id,
                    "reason": reason or "",
                    "detail": detail or "",
                }
            )

    def _handle_rebuild(self) -> None:
        try:
            self.rebuild_in_progress = True
            self.status = STATUS_REBUILDING
            self.ws.runtime_state.mkdir(parents=True, exist_ok=True)
            self.ws.rebuild_flag.write_text(
                json.dumps({"started_at": utc_now_iso()}, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            self._heartbeat()
            self._do_rebuild()
            self.channel.send({"ok": True, "reply": "REBUILD_DONE"})
        except Exception as exc:
            self.counters.last_error = f"rebuild failed: {exc}"
            self.status = STATUS_DEGRADED
            self.channel.send(
                {"ok": True, "reply": "REBUILD_FAILED", "error": str(exc)}
            )

    # ------------------------------------------------------------------
    # Query server
    # ------------------------------------------------------------------
    def _dispatch_query(self, payload: dict[str, Any]) -> Any:
        cmd = payload.get("cmd")
        if cmd != "QUERY":
            raise QueryServerError(f"unknown cmd {cmd!r}")
        op = payload.get("op")
        args = payload.get("args", {})
        if not isinstance(op, str):
            raise QueryServerError("QUERY requires string op")
        if not isinstance(args, dict):
            raise QueryServerError("QUERY args must be an object")
        if self.backend is None:
            raise QueryServerError("backend_unavailable")
        with self._kb_lock:
            if op == "list_nodes":
                return self.backend.dashboard_node_rows()
            if op == "coordinator_snapshot":
                return self.backend.coordinator_candidate_rows()
            if op == "list_applied_failed":
                return self.backend.applied_failed_rows()
            if op == "current_kind_of":
                label = args.get("label")
                if not isinstance(label, str):
                    raise QueryServerError("current_kind_of requires label")
                row = self.backend.node_by_label(label)
                return row.kind if row is not None else None
            if op == "applied_event_status":
                event_id = args.get("event_id")
                if not isinstance(event_id, str):
                    raise QueryServerError("applied_event_status requires event_id")
                row = self.backend.applied_event(event_id)
                if row is None:
                    return None
                return {
                    "status": row.status.value,
                    "reason": row.reason or "",
                    "detail": row.detail or "",
                }
            if op == "dependents_of":
                label = args.get("label")
                if not isinstance(label, str):
                    raise QueryServerError("dependents_of requires label")
                return self.backend.dependents_of(label)
            if op == "list_applied_since":
                watermark = args.get("watermark", ["", ""])
                if (
                    not isinstance(watermark, list)
                    or len(watermark) != 2
                    or not all(isinstance(x, str) for x in watermark)
                ):
                    raise QueryServerError("list_applied_since requires [applied_at, event_id] watermark")
                return self.backend.applied_since_rows((watermark[0], watermark[1]))
        raise QueryServerError(f"unknown query op {op!r}")


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------
def _events_in_order(events_root: Path) -> list[Path]:
    if not events_root.is_dir():
        return []
    files = [p for p in events_root.rglob("*.json") if p.is_file()]
    return sorted(files, key=_event_sort_key)


def _event_sort_key(path: Path) -> tuple[str, int, str]:
    try:
        parsed = parse_filename(path.name)
    except Exception as exc:
        raise _CorruptionHalt(
            f"canonical event filename invalid during replay: {path.name}: {exc}"
        ) from exc
    return (parsed.iso_ms, parsed.seq, parsed.uid)


def _row_to_node(row, deps: list[str]) -> Node:
    """Build a (renderable) Node from a backend RawNodeRow + its deps."""
    return Node(
        label=row.label,
        kind=NodeKind(row.kind),
        statement=row.statement,
        proof=row.proof,
        remark=row.remark,
        source_note=row.source_note,
        pass_count=row.pass_count,
        repair_count=row.repair_count,
        statement_hash=row.statement_hash,
        verification_hash=row.verification_hash,
        depends_on=tuple(deps),
    )


__all__ = ["LibrarianDaemon"]
