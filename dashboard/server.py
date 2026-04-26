"""Dashboard HTTP server (ARCHITECTURE §6.7 / §6.7.1).

Pure Python ``http.server`` based — no third-party deps. The handler
class is a thin shell around :class:`DashboardCore`, which holds the
read-only logic so unit tests can exercise endpoints without a socket.

Endpoint inventory (read-only):

- ``GET /api/coordinator``   raw ``runtime/state/coordinator.json``
- ``GET /api/librarian``     raw ``runtime/state/librarian.json``
- ``GET /api/active``        in-flight ``runtime/jobs/*.json`` records
- ``GET /api/overview``      runtime + Kuzu summary
- ``GET /api/theorems``      ``kind=theorem`` nodes with status
- ``GET /api/node/{label}``  full node info
- ``GET /api/rejected``      rejected_writes + apply_failed + drift_alerts
- ``GET /api/events?limit=N`` reverse-chronological event filenames
- ``GET /events/stream``     SSE stream (typed envelope)

While ``librarian.json.rebuild_in_progress = true`` the Kuzu-dependent
endpoints (``/api/overview``, ``/api/theorems``, ``/api/node/{label}``,
``/api/rejected``) return HTTP 503 + ``Retry-After: 5``. Non-Kuzu
endpoints keep serving (§6.7.1).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable

from common.events.filenames import FilenameError, parse_filename
from common.runtime.jobs import TERMINAL_STATUSES, list_jobs
from coordinator.heartbeat import read_heartbeat as read_coordinator_hb
from dashboard.kuzu_reader import (
    NodeRow,
    RebuildInProgress,
    dependents_of,
    list_applied_failed,
    list_nodes,
)
from dashboard.state import (
    HEALTHY_S,
    classify_theorem,
    liveness_label,
)
from librarian.heartbeat import read_heartbeat as read_librarian_hb


log = logging.getLogger("rethlas.dashboard")


_RETRY_AFTER_S: int = 5
# §6.7.1 `/api/events?limit=N` clamp.
_EVENTS_LIMIT_MAX: int = 500
_EVENTS_LIMIT_DEFAULT: int = 50
_NORMAL_APPLY_FAILED_ATTENTION_REASONS = {"hash_mismatch", "label_conflict"}


def _utc_now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    """Read a JSON file. ``None`` on any failure (missing / parse error).

    Per §6.7.1, dashboard logs the parse error + path to
    ``runtime/logs/dashboard.log`` and treats the component as ``down``
    rather than crashing.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("dashboard: read failed for %s: %s", path, exc)
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("dashboard: json parse failed for %s: %s", path, exc)
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


# ---------------------------------------------------------------------------
# Pure-logic core.
# ---------------------------------------------------------------------------
class DashboardCore:
    """All endpoint logic, no HTTP. Tests instantiate this directly."""

    def __init__(self, ws_root: Path, *, desired_pass_count: int = 3) -> None:
        self.ws_root = Path(ws_root)
        self.desired_pass_count = desired_pass_count

    # --- Helpers ------------------------------------------------------
    @property
    def state_dir(self) -> Path:
        return self.ws_root / "runtime" / "state"

    @property
    def jobs_dir(self) -> Path:
        return self.ws_root / "runtime" / "jobs"

    @property
    def events_dir(self) -> Path:
        return self.ws_root / "events"

    @property
    def coordinator_path(self) -> Path:
        return self.state_dir / "coordinator.json"

    @property
    def librarian_path(self) -> Path:
        return self.state_dir / "librarian.json"

    def _is_rebuilding(self) -> bool:
        hb = read_librarian_hb(self.librarian_path)
        return bool(hb and hb.get("rebuild_in_progress"))

    # --- Endpoints ----------------------------------------------------
    def coordinator(self) -> dict[str, Any]:
        hb = _safe_read_json(self.coordinator_path)
        live = liveness_label(hb.get("updated_at") if hb else None)
        return {
            "coordinator": hb or {},
            "liveness": live,
        }

    def librarian(self) -> dict[str, Any]:
        hb = _safe_read_json(self.librarian_path)
        live = liveness_label(hb.get("updated_at") if hb else None)
        return {
            "librarian": hb or {},
            "liveness": live,
        }

    def dashboard(self) -> dict[str, Any]:
        path = self.state_dir / "dashboard.json"
        hb = _safe_read_json(path)
        live = liveness_label(hb.get("updated_at") if hb else None)
        return {"dashboard": hb or {}, "liveness": live}

    def active(self) -> dict[str, Any]:
        coord = _safe_read_json(self.coordinator_path) or {}
        timeout_s = float(coord.get("codex_silent_timeout_seconds", 1800.0) or 1800.0)
        now = datetime.now(tz=timezone.utc)
        jobs: list[dict[str, Any]] = []
        for j in list_jobs(self.jobs_dir):
            if j.status in TERMINAL_STATUSES:
                continue
            d = j.to_dict()
            log_age = _log_age_seconds(j.log_path, ws_root=self.ws_root)
            d["codex_log_age_seconds"] = log_age
            d["codex_log_age_color"] = _log_age_color(log_age, timeout_s)
            # ARCHITECTURE §6.7 active-jobs panel: surface wrapper
            # heartbeat freshness (§7.4 F4) so dashboard can flag zombie
            # wrappers whose updated_at has gone stale.
            d["wrapper_heartbeat_age_seconds"] = _heartbeat_age_seconds(
                j.updated_at, now=now
            )
            jobs.append(d)
        return {"jobs": jobs, "count": len(jobs)}

    def overview(self) -> dict[str, Any]:
        # Kuzu-dependent.
        nodes = list_nodes(self.ws_root)
        in_flight_targets = {
            j.target for j in list_jobs(self.jobs_dir)
            if j.status not in TERMINAL_STATUSES
        }
        passes_by_label = {n.label: n.pass_count for n in nodes}

        theorem_count = 0
        done_count = 0
        unfinished_count = 0
        for n in nodes:
            if n.kind == "theorem":
                theorem_count += 1
            if n.pass_count >= self.desired_pass_count:
                done_count += 1
            else:
                unfinished_count += 1

        coord = _safe_read_json(self.coordinator_path) or {}
        lib = _safe_read_json(self.librarian_path) or {}

        return {
            "ts": _utc_now_iso(),
            "coordinator": {
                "data": coord,
                "liveness": liveness_label(coord.get("updated_at")),
            },
            "librarian": {
                "data": lib,
                "liveness": liveness_label(lib.get("updated_at")),
            },
            "kb": {
                "node_count": len(nodes),
                "theorem_count": theorem_count,
                "done_count": done_count,
                "unfinished_count": unfinished_count,
            },
            "in_flight_target_count": len(in_flight_targets),
        }

    def theorems(self) -> dict[str, Any]:
        nodes = list_nodes(self.ws_root)
        in_flight_targets = {
            j.target for j in list_jobs(self.jobs_dir)
            if j.status not in TERMINAL_STATUSES
        }
        passes_by_label = {n.label: n.pass_count for n in nodes}

        out: list[dict[str, Any]] = []
        for n in nodes:
            if n.kind != "theorem":
                continue
            status = classify_theorem(
                label=n.label,
                kind=n.kind,
                pass_count=n.pass_count,
                desired=self.desired_pass_count,
                deps=list(n.deps),
                deps_pass_counts={d: passes_by_label.get(d, -1) for d in n.deps},
                in_flight=n.label in in_flight_targets,
                repair_hint=n.repair_hint,
            )
            out.append(
                {
                    "label": n.label,
                    "kind": n.kind,
                    "pass_count": n.pass_count,
                    "repair_count": n.repair_count,
                    "deps": list(n.deps),
                    "status": status,
                }
            )
        out.sort(key=lambda d: d["label"])
        return {"theorems": out, "count": len(out)}

    def node_detail(self, label: str) -> dict[str, Any] | None:
        coord = _safe_read_json(self.coordinator_path) or {}
        timeout_s = float(coord.get("codex_silent_timeout_seconds", 1800.0) or 1800.0)
        now = datetime.now(tz=timezone.utc)
        nodes = list_nodes(self.ws_root)
        passes_by_label = {n.label: n.pass_count for n in nodes}
        all_jobs = [
            j for j in list_jobs(self.jobs_dir)
            if j.status not in TERMINAL_STATUSES
        ]
        in_flight_targets = {j.target for j in all_jobs}
        for n in nodes:
            if n.label != label:
                continue
            status = classify_theorem(
                label=n.label,
                kind=n.kind,
                pass_count=n.pass_count,
                desired=self.desired_pass_count,
                deps=list(n.deps),
                deps_pass_counts={d: passes_by_label.get(d, -1) for d in n.deps},
                in_flight=n.label in in_flight_targets,
                repair_hint=n.repair_hint,
            )
            # ARCHITECTURE §6.7 per-node detail surface.
            active_job: dict[str, Any] | None = None
            for j in all_jobs:
                if j.target != label:
                    continue
                jd = j.to_dict()
                log_age = _log_age_seconds(j.log_path, ws_root=self.ws_root)
                jd["codex_log_age_seconds"] = log_age
                jd["codex_log_age_color"] = _log_age_color(log_age, timeout_s)
                jd["wrapper_heartbeat_age_seconds"] = _heartbeat_age_seconds(
                    j.updated_at, now=now
                )
                active_job = jd
                break
            recent_events: list[dict[str, Any]] = []
            for shard in sorted(
                (p for p in self.events_dir.iterdir() if p.is_dir()),
                reverse=True,
            ) if self.events_dir.is_dir() else []:
                for f in sorted(shard.glob("*.json"), reverse=True):
                    try:
                        body = json.loads(f.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    if body.get("target") != label:
                        # Generator batches reference nested labels too.
                        nested = body.get("payload", {}).get("nodes", []) or []
                        if not any(
                            isinstance(node, dict) and node.get("label") == label
                            for node in nested
                        ):
                            continue
                    recent_events.append(
                        {
                            "event_id": body.get("event_id", ""),
                            "type": body.get("type", ""),
                            "actor": body.get("actor", ""),
                            "ts": body.get("ts", ""),
                        }
                    )
                    if len(recent_events) >= 20:
                        break
                if len(recent_events) >= 20:
                    break
            return {
                "label": n.label,
                "kind": n.kind,
                "statement": n.statement,
                "proof": n.proof,
                "pass_count": n.pass_count,
                "repair_count": n.repair_count,
                "statement_hash": n.statement_hash,
                "verification_hash": n.verification_hash,
                "repair_hint": n.repair_hint,
                "verification_report": n.verification_report,
                "deps": list(n.deps),
                "dependents": dependents_of(self.ws_root, label),
                "status": status,
                "active_job": active_job,
                "recent_events": recent_events,
            }
        return None

    def attention(self) -> dict[str, Any]:
        """Aggregate items that need human attention (ARCHITECTURE §6.7).

        Sources (all read-only, no Kuzu writes):
        - user-blocked nodes (definition / external_theorem at -1)
        - high-``repair_count`` nodes (>= 3): generator wheel-spin
        - recent drift_alerts.jsonl
        - recent ``apply_failed`` events (via AppliedEvent)
        - coordinator ``idle_reason_code == corruption_or_drift``
        - librarian ``status == degraded`` with non-empty ``last_error``
        """
        items: list[dict[str, Any]] = []

        # Coordinator-level alerts.
        coord = _safe_read_json(self.coordinator_path) or {}
        if coord.get("idle_reason_code") == "corruption_or_drift":
            items.append(
                {
                    "kind": "coordinator_corruption_or_drift",
                    "message": "coordinator halted dispatch on corruption/drift",
                    "detail": coord.get("idle_reason_detail", ""),
                }
            )
        # ARCHITECTURE §6.4 dashboard child supervisor: when the
        # coordinator's dashboard supervisor exhausts its restart budget
        # (§6.4 max_restarts) it transitions to ``degraded`` and stops
        # respawning. Surface that as an attention item so the operator
        # knows to investigate; "backoff" / "starting" are auto-recovering
        # so they stay off the attention surface.
        children = coord.get("children", {}) or {}
        dash_child = children.get("dashboard", {}) if isinstance(children, dict) else {}
        if isinstance(dash_child, dict) and dash_child.get("status") == "degraded":
            items.append(
                {
                    "kind": "dashboard_degraded",
                    "message": "dashboard child supervisor is degraded — restart budget exhausted",
                    "detail": dash_child,
                }
            )
        # ARCHITECTURE §6.7 "3x consecutive" labelled attention items.
        for entry in coord.get("attention_targets", []) or []:
            if not isinstance(entry, dict):
                continue
            items.append(
                {
                    "kind": "stuck_target",
                    "trigger": entry.get("trigger", ""),
                    "target": entry.get("target", ""),
                    "node_kind": entry.get("kind", ""),
                    "reason": entry.get("reason", ""),
                    "count": entry.get("count", 0),
                    "message": entry.get("message", ""),
                }
            )

        # Librarian-level alerts.
        lib = _safe_read_json(self.librarian_path) or {}
        if lib.get("status") == "degraded" and (lib.get("last_error") or ""):
            items.append(
                {
                    "kind": "librarian_degraded",
                    "message": "librarian is degraded",
                    "detail": lib.get("last_error", ""),
                }
            )

        # Node-level alerts via Kuzu.
        try:
            nodes = list_nodes(self.ws_root)
        except RebuildInProgress:
            nodes = []
        for n in nodes:
            if n.kind in {"definition", "external_theorem"} and n.pass_count == -1:
                items.append(
                    {
                        "kind": "user_blocked",
                        "message": f"user must revise {n.label}",
                        "label": n.label,
                        "node_kind": n.kind,
                    }
                )
            if n.repair_count >= 3:
                items.append(
                    {
                        "kind": "high_repair_count",
                        "message": f"{n.label} has been re-repaired {n.repair_count} times",
                        "label": n.label,
                        "repair_count": n.repair_count,
                    }
                )

        drift = _read_jsonl_tail(
            self.state_dir / "drift_alerts.jsonl", limit=50
        )
        for entry in drift:
            items.append(
                {"kind": "drift_alert", "message": "runtime drift recorded", "detail": entry}
            )

        # Recent apply_failed events from AppliedEvent.
        try:
            apply_failed = list_applied_failed(self.ws_root)
        except RebuildInProgress:
            apply_failed = []
        for ev in apply_failed[:20]:
            reason = (ev.get("reason") or "").strip()
            if reason in _NORMAL_APPLY_FAILED_ATTENTION_REASONS:
                continue
            items.append(
                {
                    "kind": "apply_failed",
                    "message": f"apply_failed: {reason}",
                    "detail": ev,
                }
            )

        return {"items": items, "count": len(items)}

    def rejected(self) -> dict[str, Any]:
        rejected_writes = _read_jsonl_tail(
            self.state_dir / "rejected_writes.jsonl", limit=200
        )
        drift = _read_jsonl_tail(
            self.state_dir / "drift_alerts.jsonl", limit=200
        )
        apply_failed = list_applied_failed(self.ws_root)
        return {
            "rejected_writes": rejected_writes,
            "apply_failed": apply_failed,
            "drift_alerts": drift,
        }

    def events(
        self,
        limit: int,
        *,
        actor: str | None = None,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        # §6.7.1: walk events/{YYYY-MM-DD}/*.json reverse-chronologically.
        out: list[dict[str, Any]] = []
        if not self.events_dir.is_dir():
            return {"events": out, "count": 0, "limit": limit}
        # Each shard is a directory whose name sorts chronologically.
        shards = sorted(
            (p for p in self.events_dir.iterdir() if p.is_dir()),
            reverse=True,
        )
        for shard in shards:
            if len(out) >= limit:
                break
            files = sorted(shard.glob("*.json"), reverse=True)
            for f in files:
                if len(out) >= limit:
                    break
                try:
                    parsed = parse_filename(f.name)
                except FilenameError:
                    # Malformed filename — surface it but don't crash.
                    if actor or event_type:
                        continue
                    out.append(
                        {
                            "event_id": f.stem,
                            "filename": f.name,
                            "shard": shard.name,
                            "actor": "",
                            "type": "",
                            "target": None,
                        }
                    )
                    continue
                if actor and parsed.actor != actor:
                    continue
                if event_type and parsed.event_type != event_type:
                    continue
                out.append(
                    {
                        "event_id": f"{parsed.iso_ms}-{parsed.seq:04d}-{parsed.uid}",
                        "filename": f.name,
                        "shard": shard.name,
                        "actor": parsed.actor,
                        "type": parsed.event_type,
                        "target": parsed.target,
                    }
                )
        return {"events": out, "count": len(out), "limit": limit}


def _heartbeat_age_seconds(
    updated_at: str, *, now: datetime | None = None
) -> float | None:
    """Return ``now - updated_at`` in seconds, or None if unparseable."""
    if not updated_at:
        return None
    try:
        if updated_at.endswith("Z"):
            updated_at = updated_at[:-1] + "+00:00"
        parsed = datetime.fromisoformat(updated_at)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(tz=timezone.utc)
    return max(0.0, (now - parsed).total_seconds())


def _log_age_seconds(log_path: str, *, ws_root: Path | None = None) -> float | None:
    """Return age (now - mtime) in seconds, or None if the file is missing.

    ``log_path`` is stored relative to the workspace root in the job
    file (``runtime/logs/<job_id>.codex.log`` per §6.7.1); the dashboard
    process's CWD is not the workspace, so callers must pass
    ``ws_root`` for relative-path resolution. Absolute paths are
    accepted and used unchanged.
    """
    if not log_path:
        return None
    p = Path(log_path)
    if not p.is_absolute() and ws_root is not None:
        p = ws_root / p
    try:
        st = os.stat(p)
    except (FileNotFoundError, OSError):
        return None
    return max(0.0, time.time() - st.st_mtime)


def _log_age_color(age: float | None, timeout_s: float) -> str:
    """ARCHITECTURE §6.7 color grading.

    - green:  age <= 5 min
    - yellow: 5 min < age <= min(T/2, 15 min)
    - orange: min(T/2, 15 min) < age < T
    - red:    age >= T (coordinator will SIGINT on next tick)
    """
    if age is None:
        return "unknown"
    if age <= 300.0:
        return "green"
    yellow_cap = min(timeout_s / 2.0, 900.0)
    if age <= yellow_cap:
        return "yellow"
    if age < timeout_s:
        return "orange"
    return "red"


def _read_jsonl_tail(path: Path, *, limit: int) -> list[dict[str, Any]]:
    """Read the last ``limit`` JSON lines from ``path``. Empty list on missing."""
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


# ---------------------------------------------------------------------------
# SSE broadcaster.
# ---------------------------------------------------------------------------
class SseBroker:
    """Thread-safe fan-out for SSE envelopes.

    The watcher thread calls :meth:`publish`; each connected handler
    pulls from its own :class:`queue.Queue` (bounded, drops oldest on
    overflow).
    """

    def __init__(self, max_queue: int = 256) -> None:
        self._max_queue = max_queue
        self._lock = threading.Lock()
        self._subs: list[queue.Queue[dict[str, Any]]] = []

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=self._max_queue)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def publish(self, envelope: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(envelope)
            except queue.Full:
                # Drop oldest to make room.
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(envelope)
                except queue.Full:
                    pass


# ---------------------------------------------------------------------------
# HTTP handler.
# ---------------------------------------------------------------------------
def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def make_handler(core: DashboardCore, broker: SseBroker | None = None):
    """Factory returning a configured :class:`BaseHTTPRequestHandler` class."""

    class _Handler(BaseHTTPRequestHandler):
        # Suppress noisy default access log to stdout; route through `log`.
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401
            log.debug("dashboard http: " + fmt, *args)

        def _send_json(
            self, code: int, payload: Any, *, extra_headers: dict[str, str] | None = None
        ) -> None:
            body = _json_bytes(payload)
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _send_503_rebuild(self) -> None:
            self._send_json(
                503,
                {"status": "rebuild_in_progress"},
                extra_headers={"Retry-After": str(_RETRY_AFTER_S)},
            )

        def _send_400(self, message: str) -> None:
            self._send_json(400, {"status": "error", "error": message})

        def _send_404(self, message: str = "not_found") -> None:
            self._send_json(404, {"status": "error", "error": message})

        # --- Routing ---
        def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)

            # Non-Kuzu endpoints stay up during rebuild.
            if path == "/api/coordinator":
                return self._send_json(200, core.coordinator())
            if path == "/api/librarian":
                return self._send_json(200, core.librarian())
            if path == "/api/dashboard":
                return self._send_json(200, core.dashboard())
            if path == "/api/active":
                return self._send_json(200, core.active())
            if path == "/api/events":
                return self._handle_events(qs)
            if path == "/events/stream":
                return self._handle_sse()
            if path in ("/", "/index.html"):
                return self._send_index()

            # Kuzu-dependent endpoints — gate on rebuild flag.
            if path in (
                "/api/overview", "/api/theorems", "/api/rejected", "/api/attention"
            ) or path.startswith("/api/node/"):
                try:
                    if path == "/api/overview":
                        return self._send_json(200, core.overview())
                    if path == "/api/theorems":
                        return self._send_json(200, core.theorems())
                    if path == "/api/rejected":
                        return self._send_json(200, core.rejected())
                    if path == "/api/attention":
                        return self._send_json(200, core.attention())
                    if path.startswith("/api/node/"):
                        label = urllib.parse.unquote(path[len("/api/node/"):])
                        if not label:
                            return self._send_400("missing label")
                        detail = core.node_detail(label)
                        if detail is None:
                            return self._send_404()
                        return self._send_json(200, detail)
                except RebuildInProgress:
                    return self._send_503_rebuild()

            return self._send_404()

        def _send_index(self) -> None:
            from dashboard.templates import INDEX_HTML

            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _handle_events(self, qs: dict[str, list[str]]) -> None:
            raw = qs.get("limit", [str(_EVENTS_LIMIT_DEFAULT)])[0]
            try:
                limit = int(raw)
            except ValueError:
                return self._send_400(f"invalid limit: {raw!r}")
            if limit < 1:
                return self._send_400("limit must be >= 1")
            if limit > _EVENTS_LIMIT_MAX:
                limit = _EVENTS_LIMIT_MAX
            actor = qs.get("actor", [None])[0]
            event_type = qs.get("type", [None])[0]
            return self._send_json(
                200, core.events(limit, actor=actor, event_type=event_type)
            )

        def _handle_sse(self) -> None:
            if broker is None:
                return self._send_404("sse_disabled")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            q = broker.subscribe()
            try:
                # Initial comment to flush headers immediately.
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    try:
                        env = q.get(timeout=15.0)
                    except queue.Empty:
                        # Keep-alive ping.
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    data = json.dumps(env, ensure_ascii=False)
                    payload = f"event: {env.get('type', 'message')}\ndata: {data}\n\n"
                    try:
                        self.wfile.write(payload.encode("utf-8"))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
            finally:
                broker.unsubscribe(q)

    return _Handler


def serve_forever(
    core: DashboardCore,
    *,
    host: str,
    port: int,
    broker: SseBroker | None = None,
) -> None:  # pragma: no cover — exercised indirectly by CLI.
    handler_cls = make_handler(core, broker)
    server = ThreadingHTTPServer((host, port), handler_cls)
    log.info("dashboard listening on http://%s:%d", host, port)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


__all__ = [
    "DashboardCore",
    "SseBroker",
    "make_handler",
    "serve_forever",
]
