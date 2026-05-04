"""Dashboard HTTP server (ARCHITECTURE §6.7 / §6.7.1).

Pure Python ``http.server`` based — no third-party deps. The handler
class is a thin shell around :class:`DashboardCore`, which holds the
read-only logic so unit tests can exercise endpoints without a socket.

Endpoint inventory (read-only):

- ``GET /api/coordinator``   raw ``runtime/state/coordinator.json``
- ``GET /api/librarian``     raw ``runtime/state/librarian.json``
- ``GET /api/active``        in-flight ``runtime/jobs/*.json`` records
- ``GET /api/overview``      runtime + Kuzu summary
- ``GET /api/tree``          foldable proof-tree roots + dependency children
- ``GET /api/theorems``      ``kind=theorem`` nodes with status
- ``GET /api/nodes``         every kind of node with status
- ``GET /api/node/{label}``  full node info
- ``GET /api/rejected``      rejected_writes + apply_failed + drift_alerts
- ``GET /api/events?limit=N`` reverse-chronological event filenames
- ``GET /events/stream``     SSE stream (typed envelope)

While ``librarian.json.rebuild_in_progress = true`` the Kuzu-dependent
endpoints (``/api/overview``, ``/api/tree``, ``/api/theorems``,
``/api/node/{label}``, ``/api/rejected``) return HTTP 503 +
``Retry-After: 5``. Non-Kuzu endpoints keep serving (§6.7.1).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable

from common.events.filenames import FilenameError, parse_filename
from common.phase3.artifacts import list_learner_batches, list_reviews
from common.runtime.jobs import TERMINAL_STATUSES, list_jobs
from common.runtime.jobs_v2 import list_role_jobs
from coordinator.heartbeat import read_heartbeat as read_coordinator_hb
from dashboard.kb_client import (
    KBUnavailable,
    NodeRow,
    RebuildInProgress,
    dependents_of,
    list_applied_failed,
    list_nodes,
)
from dashboard.state import (
    HEALTHY_S,
    STATUS_DONE,
    STATUS_GENERIC_BACKGROUND_STUCK,
    STATUS_IN_FLIGHT,
    STATUS_SEARCH_BRANCH_STUCK,
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
_REF_RE = re.compile(r"\\ref\{([^}]+)\}")
_PHASE2_ATTENTION_STATUS_BY_KIND = {
    "search_branch_stuck": STATUS_SEARCH_BRANCH_STUCK,
    "generic_background_stuck": STATUS_GENERIC_BACKGROUND_STUCK,
}


def _utc_now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _safe_parse_verification_report(raw: str) -> dict[str, Any] | None:
    """Parse a node row's ``verification_report`` JSON column.

    The report is stored as a serialized JSON string (the verifier
    emits a structured object with ``checked_items``, ``gaps``,
    ``critical_errors``, ``external_reference_checks``, ``summary``).
    Empty strings, non-string values, or parse failures all return
    ``None`` — the caller falls back to displaying the raw column.
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_ref_labels(text: str) -> list[str]:
    """Extract ``\\ref{label}`` labels in first-appearance order."""
    seen: list[str] = []
    for m in _REF_RE.finditer(text or ""):
        label = m.group(1).strip()
        if label and label not in seen:
            seen.append(label)
    return seen


def _summarize_event(body: dict[str, Any]) -> dict[str, Any]:
    """Build the per-event entry for ``recent_events``.

    Carries the bare metadata plus a type-specific ``summary`` field so
    the dashboard can show "verdict=gap, 2 gaps, 0 critical" inline
    instead of forcing the operator to open the raw event file. New
    event types fall through to an empty summary; the metadata fields
    are always present.
    """
    etype = body.get("type", "")
    payload = body.get("payload", {}) or {}
    summary: dict[str, Any] = {}
    if etype == "verifier.run_completed":
        summary["verdict"] = payload.get("verdict", "")
        # Event payloads may carry verification_report as either a
        # serialized JSON string (older runs) or as an object (current
        # writers); handle both shapes.
        report_raw = payload.get("verification_report", "")
        if isinstance(report_raw, dict):
            report: dict[str, Any] | None = report_raw
        elif isinstance(report_raw, str):
            report = _safe_parse_verification_report(report_raw)
        else:
            report = None
        if report is not None:
            summary["gap_count"] = len(report.get("gaps", []) or [])
            summary["critical_count"] = len(
                report.get("critical_errors", []) or []
            )
            summary["report_summary"] = report.get("summary", "")
            ext = report.get("external_reference_checks", []) or []
            summary["ext_ref_issue_count"] = sum(
                1
                for e in ext
                if isinstance(e, dict)
                and e.get("status") in ("missing_from_nodes", "insufficient_information")
            )
    elif etype == "generator.batch_committed":
        nodes = payload.get("nodes", []) or []
        summary["node_count"] = len(nodes)
        summary["target"] = payload.get("target", "")
    elif etype == "user.hint_attached":
        hint = payload.get("hint", "") or ""
        summary["hint_excerpt"] = hint[:200]
    return {
        "event_id": body.get("event_id", ""),
        "type": etype,
        "actor": body.get("actor", ""),
        "ts": body.get("ts", ""),
        "summary": summary,
    }


def _shared_theorem_parents(
    theorem_roots: list[str],
    deps_by_label: dict[str, list[str]],
) -> dict[str, set[str]]:
    """Map each reachable label to theorem roots that depend on it."""
    out: dict[str, set[str]] = {}

    def visit(label: str, *, root: str, path: set[str]) -> None:
        if label in path:
            return
        next_path = set(path)
        next_path.add(label)
        for dep in deps_by_label.get(label, []):
            out.setdefault(dep, set()).add(root)
            visit(dep, root=root, path=next_path)

    for root in theorem_roots:
        visit(root, root=root, path=set())
    return out


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


def _phase2_attention_by_target(coord: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return Phase II attention entries keyed by target label."""
    out: dict[str, dict[str, Any]] = {}
    for entry in coord.get("attention_targets", []) or []:
        if not isinstance(entry, dict):
            continue
        target = entry.get("target", "")
        kind = entry.get("kind", "")
        if not isinstance(target, str) or not target:
            continue
        if kind in _PHASE2_ATTENTION_STATUS_BY_KIND:
            out[target] = entry
    return out


def _status_with_attention(
    status: str,
    *,
    label: str,
    attention_by_target: dict[str, dict[str, Any]],
) -> str:
    entry = attention_by_target.get(label)
    if not entry:
        return status
    if status in {STATUS_DONE, STATUS_IN_FLIGHT}:
        return status
    return _PHASE2_ATTENTION_STATUS_BY_KIND.get(entry.get("kind", ""), status)


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
        for j in list_role_jobs(self.jobs_dir):
            if j.status in TERMINAL_STATUSES:
                continue
            d = j.to_dict()
            log_age = _log_age_seconds(j.log_path, ws_root=self.ws_root)
            d["codex_log_age_seconds"] = log_age
            d["codex_log_age_color"] = _log_age_color(log_age, timeout_s)
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
        kind_counts: dict[str, int] = {}
        for n in nodes:
            kind_counts[n.kind] = kind_counts.get(n.kind, 0) + 1
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
                "kind_counts": kind_counts,
            },
            "in_flight_target_count": len(in_flight_targets),
        }

    def theorems(self) -> dict[str, Any]:
        return self._collect_nodes(kinds={"theorem"}, key="theorems")

    def nodes(self) -> dict[str, Any]:
        """Return every node in KB grouped by kind with status classification.

        Used by the dashboard's "All nodes" panel so users can watch
        propositions, lemmas, and definitions land alongside theorems —
        especially important after H29, when generator batches routinely
        admit new helper nodes that aren't theorems."""
        return self._collect_nodes(kinds=None, key="nodes")

    def tree(self, root: str | None = None) -> dict[str, Any]:
        """Return theorem-rooted dependency trees for the Phase II dashboard.

        The graph structure primarily follows the projected Kuzu
        ``DependsOn`` edges, but we also re-scan node text for ``\\ref{}``
        labels so references admitted under H29 but still missing from
        ``Node`` appear as explicit ``missing_from_nodes`` children.
        """
        nodes = list_nodes(self.ws_root)
        by_label = {n.label: n for n in nodes}
        passes_by_label = {n.label: n.pass_count for n in nodes}
        in_flight_targets = {
            j.target for j in list_jobs(self.jobs_dir)
            if j.status not in TERMINAL_STATUSES
        }
        coord = _safe_read_json(self.coordinator_path) or {}
        attention_by_target = _phase2_attention_by_target(coord)
        deps_by_label: dict[str, list[str]] = {}
        for n in nodes:
            deps: list[str] = []
            for dep in list(n.deps) + _extract_ref_labels(n.statement + "\n" + n.proof):
                if dep and dep not in deps:
                    deps.append(dep)
            deps_by_label[n.label] = deps

        all_theorem_roots = sorted(n.label for n in nodes if n.kind == "theorem")
        if root is None:
            roots = all_theorem_roots
        elif root in by_label:
            roots = [root]
        else:
            return {"ts": _utc_now_iso(), "trees": [], "node_count": 0, "edge_count": 0}

        shared_parent_map = _shared_theorem_parents(
            all_theorem_roots, deps_by_label
        )
        seen_nodes: set[str] = set()
        seen_edges: set[tuple[str, str]] = set()

        def build(label: str, path: set[str], *, root_label: str) -> dict[str, Any]:
            seen_nodes.add(label)
            row = by_label.get(label)
            shared_parents = (
                []
                if label == root_label
                else sorted(shared_parent_map.get(label, set()) - {root_label})
            )
            if row is None:
                return {
                    "label": label,
                    "kind": None,
                    "status": "missing_from_nodes",
                    "pass_count": None,
                    "repair_count": None,
                    "desired_pass_count": self.desired_pass_count,
                    "in_flight": False,
                    "shared_parents": shared_parents,
                    "children": [],
                }

            node_status = classify_theorem(
                label=row.label,
                kind=row.kind,
                pass_count=row.pass_count,
                desired=self.desired_pass_count,
                deps=list(deps_by_label.get(row.label, [])),
                deps_pass_counts={
                    d: passes_by_label.get(d, -1)
                    for d in deps_by_label.get(row.label, [])
                },
                in_flight=row.label in in_flight_targets,
                repair_hint=row.repair_hint,
                repair_count=row.repair_count,
                introduced_by_actor=row.introduced_by_actor,
            )
            node_attention = attention_by_target.get(row.label)
            node_status = _status_with_attention(
                node_status,
                label=row.label,
                attention_by_target=attention_by_target,
            )
            out = {
                "label": row.label,
                "kind": row.kind,
                "status": node_status,
                "attention": node_attention,
                "pass_count": row.pass_count,
                "repair_count": row.repair_count,
                "desired_pass_count": self.desired_pass_count,
                "in_flight": row.label in in_flight_targets,
                "shared_parents": shared_parents,
                "children": [],
            }
            if label in path:
                out["cycle_detected"] = True
                return out
            child_path = set(path)
            child_path.add(label)
            children: list[dict[str, Any]] = []
            for dep in deps_by_label.get(label, []):
                seen_edges.add((label, dep))
                children.append(build(dep, child_path, root_label=root_label))
            out["children"] = children
            return out

        trees = [build(label, set(), root_label=label) for label in roots]
        return {
            "ts": _utc_now_iso(),
            "trees": trees,
            "node_count": len(seen_nodes),
            "edge_count": len(seen_edges),
        }

    def _collect_nodes(
        self, *, kinds: set[str] | None, key: str
    ) -> dict[str, Any]:
        nodes = list_nodes(self.ws_root)
        in_flight_targets = {
            j.target for j in list_jobs(self.jobs_dir)
            if j.status not in TERMINAL_STATUSES
        }
        passes_by_label = {n.label: n.pass_count for n in nodes}
        coord = _safe_read_json(self.coordinator_path) or {}
        attention_by_target = _phase2_attention_by_target(coord)

        out: list[dict[str, Any]] = []
        for n in nodes:
            if kinds is not None and n.kind not in kinds:
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
                repair_count=n.repair_count,
                introduced_by_actor=n.introduced_by_actor,
            )
            attention = attention_by_target.get(n.label)
            status = _status_with_attention(
                status,
                label=n.label,
                attention_by_target=attention_by_target,
            )
            out.append(
                {
                    "label": n.label,
                    "kind": n.kind,
                    "pass_count": n.pass_count,
                    "repair_count": n.repair_count,
                    "deps": list(n.deps),
                    "status": status,
                    "attention": attention,
                    "introduced_by_actor": n.introduced_by_actor,
                }
            )
        out.sort(key=lambda d: (d["kind"], d["label"]))
        return {key: out, "count": len(out)}

    def node_detail(self, label: str) -> dict[str, Any] | None:
        coord = _safe_read_json(self.coordinator_path) or {}
        timeout_s = float(coord.get("codex_silent_timeout_seconds", 1800.0) or 1800.0)
        attention_by_target = _phase2_attention_by_target(coord)
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
                repair_count=n.repair_count,
                introduced_by_actor=n.introduced_by_actor,
            )
            attention = attention_by_target.get(n.label)
            status = _status_with_attention(
                status,
                label=n.label,
                attention_by_target=attention_by_target,
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
                    recent_events.append(_summarize_event(body))
                    if len(recent_events) >= 20:
                        break
                if len(recent_events) >= 20:
                    break

            # Promote the latest verifier verdict + parse the node-level
            # verification_report so the dashboard can render verdict
            # state without each operator having to open raw event files.
            latest_verifier_event = next(
                (e for e in recent_events if e.get("type") == "verifier.run_completed"),
                None,
            )
            verification_report_parsed = _safe_parse_verification_report(
                n.verification_report
            )
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
                "verification_report_parsed": verification_report_parsed,
                "latest_verifier_event": latest_verifier_event,
                "deps": list(n.deps),
                "dependents": dependents_of(self.ws_root, label),
                "status": status,
                "attention": attention,
                "active_job": active_job,
                "recent_events": recent_events,
                "introduced_by_actor": n.introduced_by_actor,
                "desired_pass_count": self.desired_pass_count,
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
        except KBUnavailable:
            nodes = []
        for n in nodes:
            # Only flag user-introduced axioms as user_blocked. Generator-
            # introduced helper definitions sitting at -1 are awaiting a
            # generator repair round and don't need user attention.
            if (
                n.kind in {"definition", "external_theorem"}
                and not n.introduced_by_actor.startswith("generator:")
                and n.pass_count == -1
            ):
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
        except KBUnavailable:
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
        try:
            apply_failed = list_applied_failed(self.ws_root)
        except KBUnavailable:
            apply_failed = []
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

    def learner_runs(self) -> dict[str, Any]:
        jobs = [j.to_dict() for j in list_role_jobs(self.jobs_dir) if j.kind == "learner"]
        batches = list_learner_batches(self.ws_root)
        runs = []
        for batch in batches:
            payload = batch.get("payload", {}) if isinstance(batch.get("payload"), dict) else {}
            runs.append(
                {
                    "event_id": batch.get("event_id", ""),
                    "source_id": batch.get("source_id", ""),
                    "learner_run": batch.get("learner_run", ""),
                    "context_hash": batch.get("context_hash", ""),
                    "status": batch.get("status", ""),
                    "candidate_count": len(payload.get("candidate_nodes", []) or []),
                    "issue_count": len(payload.get("issues", []) or []),
                    "hash": batch.get("hash", ""),
                    "ts": batch.get("ts", ""),
                }
            )
        return {"runs": runs, "jobs": jobs, "count": len(runs)}

    def learner_run(self, run_id: str) -> dict[str, Any] | None:
        for batch in list_learner_batches(self.ws_root):
            if run_id in {batch.get("learner_run", ""), batch.get("event_id", "")}:
                return batch
        for job in list_role_jobs(self.jobs_dir):
            if job.kind == "learner" and job.job_id == run_id:
                return job.to_dict()
        return None

    def study_kb(self) -> dict[str, Any]:
        """Flatten Phase 3 learner proposals into a read-only study KB view.

        Learner artifacts are intentionally separate from projected Kuzu nodes:
        they are source-backed study proposals, not referee-approved KB facts.
        This endpoint gives operators a browsable surface without promoting
        those proposals into the verified knowledge graph.
        """
        batches = list_learner_batches(self.ws_root)
        nodes: list[dict[str, Any]] = []
        kind_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        issue_counts_by_type: dict[str, int] = {}

        for batch in batches:
            payload = batch.get("payload", {})
            if not isinstance(payload, dict):
                payload = {}
            source_id = batch.get("source_id", "") or payload.get("source_id", "")
            learner_run = batch.get("learner_run", "") or payload.get("learner_run", "")
            issues = payload.get("issues", []) or []
            batch_issue_count = len(issues) if isinstance(issues, list) else 0
            if isinstance(issues, list):
                for issue in issues:
                    if not isinstance(issue, dict):
                        continue
                    issue_type = str(issue.get("issue_type", "") or "unknown")
                    issue_counts_by_type[issue_type] = issue_counts_by_type.get(issue_type, 0) + 1

            for node in payload.get("candidate_nodes", []) or []:
                if not isinstance(node, dict):
                    continue
                kind = str(node.get("kind", "") or "")
                label = str(node.get("label", "") or "")
                refs = node.get("source_refs", []) or []
                span_ids: list[str] = []
                if isinstance(refs, list):
                    for ref in refs:
                        if isinstance(ref, dict) and ref.get("span_id"):
                            span_ids.append(str(ref.get("span_id")))
                kind_counts[kind] = kind_counts.get(kind, 0) + 1
                source_counts[source_id] = source_counts.get(source_id, 0) + 1
                nodes.append(
                    {
                        "label": label,
                        "kind": kind,
                        "statement": node.get("statement", ""),
                        "proof": node.get("proof", ""),
                        "proof_status": node.get("proof_status", ""),
                        "proof_steps": node.get("proof_steps", []) or [],
                        "depends_on": node.get("depends_on", []) or [],
                        "remark": node.get("remark", ""),
                        "source_note": node.get("source_note", ""),
                        "source_id": source_id,
                        "source_refs": refs,
                        "span_ids": span_ids,
                        "learner_run": learner_run,
                        "event_id": batch.get("event_id", ""),
                        "status": batch.get("status", ""),
                        "ts": batch.get("ts", ""),
                        "batch_issue_count": batch_issue_count,
                    }
                )

        nodes.sort(key=lambda d: (d["source_id"], d["kind"], d["label"]))
        return {
            "nodes": nodes,
            "count": len(nodes),
            "batch_count": len(batches),
            "kind_counts": kind_counts,
            "source_counts": source_counts,
            "issue_counts_by_type": issue_counts_by_type,
        }

    def reviews(self) -> dict[str, Any]:
        rows = []
        for review in list_reviews(self.ws_root):
            payload = review.get("payload", {}) if isinstance(review.get("payload"), dict) else {}
            rows.append(
                {
                    "event_id": review.get("event_id", ""),
                    "review_id": payload.get("review_id", ""),
                    "target": review.get("target", ""),
                    "verdict": payload.get("verdict", ""),
                    "issue_summary": payload.get("issue_summary", {}),
                    "hash": review.get("hash", ""),
                    "ts": review.get("ts", ""),
                    "artifact_path": review.get("artifact_path", ""),
                }
            )
        return {"reviews": rows, "count": len(rows)}

    def review(self, review_id: str) -> dict[str, Any] | None:
        for review in list_reviews(self.ws_root):
            payload = review.get("payload", {}) if isinstance(review.get("payload"), dict) else {}
            if review_id in {payload.get("review_id", ""), review.get("event_id", "")}:
                return review
        return None


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
            if path == "/api/learner/runs":
                return self._send_json(200, core.learner_runs())
            if path.startswith("/api/learner/run/"):
                run_id = urllib.parse.unquote(path[len("/api/learner/run/"):])
                if not run_id:
                    return self._send_400("missing run id")
                detail = core.learner_run(run_id)
                if detail is None:
                    return self._send_404()
                return self._send_json(200, detail)
            if path == "/api/study/kb":
                return self._send_json(200, core.study_kb())
            if path == "/api/reviews":
                return self._send_json(200, core.reviews())
            if path.startswith("/api/review/"):
                review_id = urllib.parse.unquote(path[len("/api/review/"):])
                if not review_id:
                    return self._send_400("missing review id")
                detail = core.review(review_id)
                if detail is None:
                    return self._send_404()
                return self._send_json(200, detail)
            if path == "/events/stream":
                return self._handle_sse()
            if path in ("/", "/index.html"):
                return self._send_index()

            # Kuzu-dependent endpoints — gate on rebuild flag.
            if path in (
                "/api/overview", "/api/tree", "/api/theorems", "/api/nodes",
                "/api/rejected", "/api/attention",
            ) or path.startswith("/api/node/"):
                try:
                    if path == "/api/overview":
                        return self._send_json(200, core.overview())
                    if path == "/api/tree":
                        root_raw = qs.get("root", [None])[0]
                        root = root_raw if root_raw else None
                        return self._send_json(200, core.tree(root))
                    if path == "/api/theorems":
                        return self._send_json(200, core.theorems())
                    if path == "/api/nodes":
                        return self._send_json(200, core.nodes())
                    if path == "/api/attention":
                        return self._send_json(200, core.attention())
                    if path == "/api/rejected":
                        return self._send_json(200, core.rejected())
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
                except KBUnavailable as exc:
                    if path in ("/api/attention", "/api/rejected"):
                        return self._send_json(200, core.attention() if path == "/api/attention" else core.rejected())
                    return self._send_json(
                        503,
                        {"status": "librarian_unavailable", "error": str(exc)},
                        extra_headers={"Retry-After": str(_RETRY_AFTER_S)},
                    )

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
