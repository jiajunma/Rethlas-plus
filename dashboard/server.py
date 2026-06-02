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
- ``GET /api/study/graph``  learner-proposed candidate dependency graph
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
from common.phase3.artifacts import (
    latest_review_node_repairs_by_label,
    list_bridge_repairs,
    list_learner_batches,
    list_review_node_repairs,
    list_reviews,
    read_json_files,
)
from common.runtime.jobs import STATUS_PUBLISHING, TERMINAL_STATUSES, list_jobs
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
_STUDY_LABEL_RE = re.compile(r"^(?P<prefix>[a-z]+):")
_STUDY_DEPENDENCY_RELATIONS = {
    "depends_on",
    "proof_step",
    "uses",
    "refines",
}
_STUDY_BRIDGE_RELATIONS = {"needs_bridge"}
_STUDY_CONTEXT_RELATIONS = {"local_context"}
_ARTICLE_CITATION_RE = re.compile(
    r"^(?P<authors>.+?),\s*(?P<title>.+?),\s*(?P<venue>[^,]+)\s+"
    r"(?P<volume>\d+)\s*\((?P<year>\d{4})\),\s*(?P<pages>[0-9]+[-–][0-9]+)\.?$"
)
_LOCATOR_PAGE_RE = re.compile(r"PDF page\s+(\d+)", re.IGNORECASE)
_FORMULA_TEXT_RE = re.compile(
    r"[𝜃𝛱𝜓𝜎𝜏𝜖𝑝𝑞𝑛𝑚𝑟𝑆𝒮⊠⊕⨁≅∈→↦∪±]|"
    r"\\[A-Za-z]+|[A-Za-z]+_\{|[A-Za-z]+\\^|Ind|Std|SO|Mp|Sp|GL|O\("
)
_PHASE2_ATTENTION_STATUS_BY_KIND = {
    "search_branch_stuck": STATUS_SEARCH_BRANCH_STUCK,
    "generic_background_stuck": STATUS_GENERIC_BACKGROUND_STUCK,
}


def _study_edge_bucket(edge: dict[str, Any], kinds_by_label: dict[str, str]) -> str:
    relation = str(edge.get("relation", "") or "")
    target = str(edge.get("target", "") or edge.get("dependency", "") or "")
    target_kind = kinds_by_label.get(target, "")
    if relation in _STUDY_CONTEXT_RELATIONS:
        return "context"
    if relation in _STUDY_BRIDGE_RELATIONS or target_kind == "bridge_request":
        return "bridge"
    if relation in _STUDY_DEPENDENCY_RELATIONS or relation.startswith("uses"):
        return "dependency"
    return "relation"


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


def _infer_study_graph_kind(label: str) -> str:
    if label.startswith("bridge_req") or label.startswith("bridge:"):
        return "bridge_request"
    match = _STUDY_LABEL_RE.match(label or "")
    prefix = match.group("prefix") if match else ""
    return {
        "def": "definition",
        "ext": "external_theorem",
        "lem": "lemma",
        "prop": "proposition",
        "thm": "theorem",
        "cor": "corollary",
        "axiom": "axiom",
    }.get(prefix, "missing_candidate")


def _bridge_request_id(req: dict[str, Any]) -> str:
    for key in ("request_id", "label", "id"):
        value = req.get(key)
        if isinstance(value, str) and value:
            return value
    for key in ("for_label", "blocked_node", "node_label", "target_label", "from_label"):
        value = req.get(key)
        if isinstance(value, str) and value:
            return f"bridge_req_{re.sub(r'[^A-Za-z0-9_]+', '_', value).strip('_')}"
    return ""


def _bridge_request_blocks(req: dict[str, Any]) -> list[str]:
    out: list[str] = []
    blocks = req.get("blocks", [])
    if isinstance(blocks, str):
        _append_unique_str(out, blocks)
    elif isinstance(blocks, list):
        for label in blocks:
            _append_unique_str(out, label)
    for key in ("for_label", "blocked_node", "node_label", "target_label", "from_label"):
        _append_unique_str(out, req.get(key))
    return out


def _edge_endpoints(edge_raw: dict[str, Any], candidate_labels: set[str]) -> tuple[str, str]:
    dependent = str(edge_raw.get("dependent", "") or "")
    dependency = str(edge_raw.get("dependency", "") or "")
    if dependent or dependency:
        return dependent, dependency

    source = str(edge_raw.get("source", "") or edge_raw.get("from", "") or "")
    target = str(edge_raw.get("target", "") or edge_raw.get("to", "") or "")
    if not source or not target:
        return "", ""
    source_kind = _infer_study_graph_kind(source)
    target_kind = _infer_study_graph_kind(target)
    if (
        source_kind in {"definition", "external_theorem", "axiom"}
        and target_kind not in {"definition", "external_theorem", "axiom", "missing_candidate"}
    ):
        return target, source
    if target in candidate_labels and source not in candidate_labels:
        return target, source
    return source, target


def _append_unique_str(seq: list[str], value: Any) -> None:
    if not isinstance(value, str) or not value:
        return
    if value not in seq:
        seq.append(value)


def _append_unique_value(seq: list[Any], value: Any) -> None:
    if value in ("", None, [], {}):
        return
    if value not in seq:
        seq.append(value)


def _source_ref_span_ids(refs: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(refs, list):
        return out
    for ref in refs:
        if isinstance(ref, dict):
            _append_unique_str(out, ref.get("span_id"))
    return out


def _notation_context_id(ctx: Any) -> str:
    if not isinstance(ctx, dict):
        return ""
    value = ctx.get("context_id") or ctx.get("id")
    return value if isinstance(value, str) else ""


def _safe_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _has_meaningful_value(value: Any) -> bool:
    return value not in ("", None, [], {})


def _merge_dicts(base: dict[str, Any], extra: Any) -> dict[str, Any]:
    if not isinstance(extra, dict):
        return base
    for key, value in extra.items():
        if value in ("", None, [], {}):
            continue
        if key not in base or base.get(key) in ("", None, [], {}):
            base[key] = value
    return base


def _count_kinds(nodes: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in nodes:
        kind = str(node.get("kind", "") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _active_bridge_repair_actions(ws_root: Path) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for artifact in reversed(list_bridge_repairs(ws_root)):
        if artifact.get("active", True) is False:
            continue
        repair_id = str(artifact.get("repair_id", "") or "")
        repair_ts = str(artifact.get("ts", "") or "")
        for action in artifact.get("actions", []) or []:
            if not isinstance(action, dict):
                continue
            action_copy = dict(action)
            action_copy.setdefault("repair_id", repair_id)
            action_copy.setdefault("repair_ts", repair_ts)
            actions.append(action_copy)
    return actions


def _bridge_repair_redirects(actions: list[dict[str, Any]]) -> dict[str, str]:
    redirects: dict[str, str] = {}
    for action in actions:
        action_kind = str(action.get("action", "") or action.get("action_type", "") or "")
        if action_kind != "redirect":
            continue
        source = str(action.get("source_label", "") or action.get("from_label", "") or "")
        target = str(action.get("target_label", "") or action.get("to_label", "") or "")
        if source and target and source != target:
            redirects[source] = target
    return redirects


def _bridge_repair_closed_ids(actions: list[dict[str, Any]]) -> set[str]:
    closed: set[str] = set()
    for action in actions:
        action_kind = str(action.get("action", "") or action.get("action_type", "") or "")
        if action_kind != "close":
            continue
        bridge_id = str(action.get("bridge_id", "") or action.get("label", "") or "")
        if bridge_id:
            closed.add(bridge_id)
    return closed


def _canonical_bridge_label(label: str, redirects: dict[str, str]) -> str:
    current = str(label or "")
    seen: set[str] = set()
    while current in redirects and current not in seen:
        seen.add(current)
        current = redirects[current]
    return current


def _bridge_repair_summary(actions: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for action in actions:
        kind = str(action.get("action", "") or action.get("action_type", "") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "action_count": len(actions),
        "action_counts": counts,
        "redirect_count": counts.get("redirect", 0),
        "close_count": counts.get("close", 0),
        "materialize_plan_count": counts.get("materialize_plan", 0),
    }


def _merge_study_graph_node(base: dict[str, Any], extra: dict[str, Any]) -> None:
    for key in (
        "source_ids",
        "learner_runs",
        "event_ids",
        "span_ids",
        "notation_context_ids",
        "learning_contracts",
        "aliases",
        "bridge_repair_ids",
    ):
        values = extra.get(key, [])
        if isinstance(values, list):
            base.setdefault(key, [])
            for value in values:
                _append_unique_str(base[key], value)
    for key in ("external_source_refs",):
        values = extra.get(key, [])
        if isinstance(values, list):
            base.setdefault(key, [])
            for value in values:
                _append_unique_value(base[key], value)
    for key in (
        "statement",
        "source_excerpt",
        "formula_excerpt",
        "display_source_excerpt",
        "display_formula_excerpt",
        "display_render_mode",
        "formula_display_render_mode",
        "typesetting_notes",
        "remark",
        "source_note",
        "extraction_kind",
        "source_locator",
        "citation_status",
        "source_type",
        "review_id",
        "review_verdict",
        "title",
    ):
        if base.get(key) in ("", None, [], {}) and extra.get(key) not in ("", None, [], {}):
            base[key] = extra.get(key)
    for key in ("bibliography", "external_citation"):
        base.setdefault(key, {})
        _merge_dicts(base[key], extra.get(key))
    for key in ("versions", "issue_count"):
        base[key] = int(base.get(key, 0) or 0) + int(extra.get(key, 0) or 0)
    if extra.get("needs_visual_check"):
        base["needs_visual_check"] = True
    if not extra.get("virtual"):
        base["virtual"] = False
    if base.get("status") in ("", "virtual") and extra.get("status"):
        base["status"] = extra.get("status")
    if base.get("proof_status") in ("", "statement_only") and extra.get("proof_status"):
        base["proof_status"] = extra.get("proof_status")


def _canonicalize_study_node(node: dict[str, Any], redirects: dict[str, str]) -> dict[str, Any]:
    label = str(node.get("label", "") or "")
    canonical = _canonical_bridge_label(label, redirects)
    if not canonical or canonical == label:
        return node
    out = dict(node)
    aliases = list(out.get("aliases", []) or [])
    if label not in aliases:
        aliases.append(label)
    out["aliases"] = aliases
    out["original_label"] = label
    out["label"] = canonical
    if "id" in out:
        out["id"] = canonical
    inferred = _infer_study_graph_kind(canonical)
    if inferred != "missing_candidate":
        out["kind"] = inferred
    depends_on = out.get("depends_on", [])
    if isinstance(depends_on, list):
        out["depends_on"] = [
            _canonical_bridge_label(str(dep or ""), redirects)
            for dep in depends_on
            if str(dep or "")
        ]
    proof_steps = out.get("proof_steps", [])
    if isinstance(proof_steps, list):
        repaired_steps: list[dict[str, Any]] = []
        for step in proof_steps:
            if not isinstance(step, dict):
                continue
            step_copy = dict(step)
            step_deps = step_copy.get("depends_on", [])
            if isinstance(step_deps, list):
                step_copy["depends_on"] = [
                    _canonical_bridge_label(str(dep or ""), redirects)
                    for dep in step_deps
                    if str(dep or "")
                ]
            repaired_steps.append(step_copy)
        out["proof_steps"] = repaired_steps
    return out


def _apply_bridge_repairs_to_study_nodes(
    nodes: list[dict[str, Any]], actions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    redirects = _bridge_repair_redirects(actions)
    closed = _bridge_repair_closed_ids(actions)
    repaired_nodes: list[dict[str, Any]] = []
    for node in nodes:
        repaired = _canonicalize_study_node(node, redirects)
        label = str(repaired.get("label", "") or "")
        if not label or label in closed:
            continue
        repaired_nodes.append(repaired)
    return repaired_nodes


def _apply_bridge_repairs_to_study_graph(
    nodes_by_label: dict[str, dict[str, Any]],
    edges_by_key: dict[tuple[str, str, str], dict[str, Any]],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    redirects = _bridge_repair_redirects(actions)
    closed = _bridge_repair_closed_ids(actions)
    repair_summary = _bridge_repair_summary(actions)

    for source, target in sorted(redirects.items()):
        source_node = nodes_by_label.pop(source, None)
        if source_node is None:
            continue
        source_node = _canonicalize_study_node(source_node, redirects)
        source_node.setdefault("aliases", [])
        _append_unique_str(source_node["aliases"], source)
        source_node.setdefault("bridge_repair_ids", [])
        target_node = nodes_by_label.get(target)
        if target_node is None:
            target_node = source_node
            target_node["id"] = target
            target_node["label"] = target
            inferred = _infer_study_graph_kind(target)
            if inferred != "missing_candidate":
                target_node["kind"] = inferred
            nodes_by_label[target] = target_node
        else:
            target_node.setdefault("aliases", [])
            _append_unique_str(target_node["aliases"], source)
            _merge_study_graph_node(target_node, source_node)
        for action in actions:
            if (
                str(action.get("action", "") or action.get("action_type", "") or "") == "redirect"
                and str(action.get("source_label", "") or action.get("from_label", "") or "") == source
            ):
                target_node.setdefault("bridge_repair_ids", [])
                _append_unique_str(target_node["bridge_repair_ids"], action.get("repair_id"))
                target_node["latest_bridge_repair"] = {
                    "repair_id": action.get("repair_id", ""),
                    "repair_ts": action.get("repair_ts", ""),
                    "action": "redirect",
                    "source_label": source,
                    "target_label": target,
                    "reason": action.get("reason", ""),
                }

    new_edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    for edge in edges_by_key.values():
        source = _canonical_bridge_label(str(edge.get("source", "") or ""), redirects)
        target = _canonical_bridge_label(str(edge.get("target", "") or ""), redirects)
        if not source or not target or source == target or source in closed or target in closed:
            continue
        relation = str(edge.get("relation", "") or "depends_on")
        new_key = (source, target, relation)
        existing = new_edges.get(new_key)
        if existing is None:
            existing = dict(edge)
            existing["source"] = source
            existing["target"] = target
            existing["dependent"] = source
            existing["dependency"] = target
            existing.setdefault("source_types", [])
            existing.setdefault("event_ids", [])
            existing.setdefault("learner_runs", [])
            new_edges[new_key] = existing
        else:
            for key in ("source_types", "event_ids", "learner_runs"):
                for value in edge.get(key, []) or []:
                    _append_unique_str(existing[key], value)
    edges_by_key.clear()
    edges_by_key.update(new_edges)
    for label in closed:
        nodes_by_label.pop(label, None)
    repair_summary["closed_bridge_ids"] = sorted(closed)
    repair_summary["redirects"] = [
        {"source_label": source, "target_label": target}
        for source, target in sorted(redirects.items())
    ]
    return repair_summary


def _parse_article_citation(text: str) -> dict[str, Any]:
    text = " ".join(_safe_text(text).split())
    match = _ARTICLE_CITATION_RE.match(text)
    if not match:
        return {"citation_text": text} if text else {}
    authors_raw = match.group("authors")
    authors = [
        part.strip()
        for part in re.split(r"\s+and\s+|\s*&\s*|;\s*", authors_raw)
        if part.strip()
    ]
    return {
        "citation_text": text,
        "authors": authors or [authors_raw],
        "title": match.group("title").strip(),
        "venue": match.group("venue").strip(),
        "volume": match.group("volume"),
        "year": match.group("year"),
        "pages": match.group("pages").replace("–", "-"),
    }


def _span_source_ref(ref: Any, span_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(ref, dict):
        return {}
    span_id = _safe_text(ref.get("span_id"))
    span = span_index.get(span_id, {})
    out = {
        "span_id": span_id,
        "span_hash": _safe_text(ref.get("span_hash") or span.get("span_hash") or span.get("text_hash")),
    }
    for field in ("source_id", "page_range", "locator", "description"):
        value = _safe_text(span.get(field))
        if value:
            out[field] = value
    return {k: v for k, v in out.items() if v}


def _source_span_text(span: dict[str, Any]) -> str:
    return _safe_text(
        span.get("text_excerpt")
        or span.get("excerpt")
        or span.get("text")
        or span.get("raw_text")
    )


def _locator_pages(locator: str) -> set[int]:
    out: set[int] = set()
    for match in _LOCATOR_PAGE_RE.finditer(locator or ""):
        try:
            out.add(int(match.group(1)))
        except ValueError:
            pass
    return out


def _source_span_matches_locator(span: dict[str, Any], locator: str) -> bool:
    span_locator = _safe_text(span.get("locator"))
    if not locator:
        return False
    if span_locator and (span_locator in locator or locator in span_locator):
        return True
    wanted_pages = _locator_pages(locator)
    if not wanted_pages:
        return False
    try:
        page = int(span.get("page"))
    except (TypeError, ValueError):
        return False
    return page in wanted_pages


def _source_excerpt_for_node(
    node: dict[str, Any], span_index: dict[str, dict[str, Any]]
) -> tuple[str, list[str], bool]:
    explicit = _safe_text(node.get("source_excerpt") or node.get("quoted_source") or node.get("source_text"))
    if explicit:
        return explicit, [], bool(node.get("needs_visual_check"))
    locator = _safe_text(node.get("source_locator"))
    excerpts: list[str] = []
    span_ids: list[str] = []
    needs_visual_check = False
    for span_id, span in sorted(span_index.items()):
        if not isinstance(span, dict) or not _source_span_matches_locator(span, locator):
            continue
        excerpt = _source_span_text(span)
        if not excerpt:
            continue
        if any(excerpt in existing for existing in excerpts):
            continue
        excerpts = [existing for existing in excerpts if existing not in excerpt]
        excerpts.append(excerpt)
        _append_unique_str(span_ids, span_id)
        extraction = span.get("extraction", {})
        if isinstance(extraction, dict) and bool(extraction.get("needs_visual_check")):
            needs_visual_check = True
        if len("\n\n".join(excerpts)) >= 6000:
            break
    return "\n\n".join(excerpts), span_ids, needs_visual_check


def _formula_excerpt_from_text(text: str) -> str:
    lines = (text or "").splitlines()
    selected: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if _FORMULA_TEXT_RE.search(line):
            selected.append((i, line))
    if not selected:
        return ""
    keep: set[int] = set()
    for i, _line in selected:
        keep.update(range(max(0, i - 1), min(len(lines), i + 2)))
    chunks: list[str] = []
    last = -10
    for i in sorted(keep):
        if i != last + 1 and chunks:
            chunks.append("...")
        chunks.append(lines[i])
        last = i
    return "\n".join(chunks).strip()


def _apply_review_node_repair(
    node: dict[str, Any], repair: dict[str, Any] | None
) -> dict[str, Any]:
    if not repair:
        return node
    repaired = dict(node)
    for field in (
        "source_excerpt",
        "formula_excerpt",
        "display_source_excerpt",
        "display_formula_excerpt",
        "display_render_mode",
        "formula_display_render_mode",
        "typesetting_notes",
        "source_locator",
        "source_refs",
    ):
        value = repair.get(field)
        if value not in ("", None, [], {}):
            repaired[field] = value
    if "needs_visual_check" in repair:
        repaired["needs_visual_check"] = bool(repair.get("needs_visual_check"))
    repaired["repair_id"] = repair.get("repair_id", "")
    repaired["repair_ts"] = repair.get("repair_ts", "")
    repaired["repair_kind"] = repair.get("repair_kind", "")
    repaired["repair_version"] = repair.get("repair_version", None)
    repaired["repair_active"] = bool(repair.get("repair_active", True))
    repaired["repair_supersedes"] = repair.get("repair_supersedes", []) or []
    repaired["repair_summary"] = repair.get("repair_summary", {}) or {}
    repaired["repair_actions"] = repair.get("repair_actions", [])
    repaired["has_repair_overlay"] = True
    return repaired


def _source_provenance_index(ws_root: Path, batches: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build source bibliographic/read-model metadata from local Phase 3 artifacts.

    Learner batches may predate a strict bibliography schema. This index
    accepts structured source records when present and otherwise recovers a
    conservative citation from notation_context.article, source spans, and local
    artifact metadata.
    """
    by_source: dict[str, dict[str, Any]] = {}

    def ensure(source_id: str) -> dict[str, Any]:
        source_id = str(source_id or "")
        row = by_source.get(source_id)
        if row is None:
            row = {
                "source_id": source_id,
                "bibliography": {},
                "local_artifact": {},
                "spans": {},
                "context_paths": [],
            }
            by_source[source_id] = row
        return row

    for source_event in read_json_files(ws_root / "knowledge_base" / "phase3" / "sources"):
        payload = source_event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        source_id = str(source_event.get("source_id", "") or payload.get("source_id", "") or "")
        if not source_id:
            continue
        row = ensure(source_id)
        _merge_dicts(row["bibliography"], payload.get("bibliography"))
        _merge_dicts(row["local_artifact"], payload.get("local_artifact"))

    for source_json in sorted((ws_root / "sources").glob("**/*.json")):
        try:
            payload = json.loads(source_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        source_id = str(payload.get("source_id", "") or "")
        if not source_id:
            continue
        row = ensure(source_id)
        _append_unique_str(row["context_paths"], str(source_json))
        _merge_dicts(row["bibliography"], payload.get("bibliography"))
        _merge_dicts(row["local_artifact"], payload.get("local_artifact"))
        notation = payload.get("notation_context", {})
        if isinstance(notation, dict):
            article = _safe_text(notation.get("article"))
            if article:
                _merge_dicts(row["bibliography"], _parse_article_citation(article))
        for span in payload.get("source_spans", []) or []:
            if not isinstance(span, dict):
                continue
            span_id = _safe_text(span.get("span_id") or span.get("id"))
            if span_id:
                row["spans"][span_id] = span
                if not row["bibliography"].get("title") and str(span.get("description", "")).lower().startswith("title"):
                    _merge_dicts(row["bibliography"], {"title_span": span_id})

    for batch in batches:
        payload = batch.get("payload", {})
        if not isinstance(payload, dict):
            continue
        source_id = str(batch.get("source_id", "") or payload.get("source_id", "") or "")
        if not source_id:
            continue
        row = ensure(source_id)
        _merge_dicts(row["bibliography"], payload.get("bibliography"))
        _merge_dicts(row["local_artifact"], payload.get("local_artifact"))
        notation = payload.get("notation_context", {})
        if isinstance(notation, dict):
            article = _safe_text(notation.get("article"))
            if article:
                _merge_dicts(row["bibliography"], _parse_article_citation(article))
        for span in payload.get("source_spans", []) or []:
            if not isinstance(span, dict):
                continue
            span_id = _safe_text(span.get("span_id") or span.get("id"))
            if span_id and span_id not in row["spans"]:
                row["spans"][span_id] = span

    for context_json in sorted((ws_root / "sources").glob("**/referee_context*.json")):
        try:
            payload = json.loads(context_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        source_id = str(payload.get("source_id", "") or payload.get("target", "") or "")
        if not source_id:
            continue
        row = ensure(source_id)
        _append_unique_str(row["context_paths"], str(context_json))
        metadata = payload.get("source_metadata", {})
        if isinstance(metadata, dict):
            _merge_dicts(row["local_artifact"], metadata)
        for span in payload.get("source_spans", []) or []:
            if not isinstance(span, dict):
                continue
            span_id = _safe_text(span.get("span_id") or span.get("id"))
            if span_id and span_id not in row["spans"]:
                row["spans"][span_id] = span

    return by_source


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


def _pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _inactive_role_job_reason(job: Any, *, pid_alive: bool) -> str:
    if pid_alive:
        return ""
    status = str(getattr(job, "status", "") or "")
    if status == STATUS_PUBLISHING:
        return "published event but wrapper exited before coordinator reconciliation"
    if status in {"starting", "running"}:
        return "wrapper process is no longer alive"
    return ""


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
        stale_jobs: list[dict[str, Any]] = []
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
            pid_alive = _pid_alive(j.pid)
            d["pid_alive"] = pid_alive
            log_age = _log_age_seconds(j.log_path, ws_root=self.ws_root)
            d["codex_log_age_seconds"] = log_age
            d["codex_log_age_color"] = _log_age_color(log_age, timeout_s)
            d["wrapper_heartbeat_age_seconds"] = _heartbeat_age_seconds(
                j.updated_at, now=now
            )
            inactive_reason = _inactive_role_job_reason(j, pid_alive=pid_alive)
            if inactive_reason:
                d["stale_reason"] = inactive_reason
                stale_jobs.append(d)
                continue
            jobs.append(d)
        return {"jobs": jobs, "count": len(jobs), "stale_jobs": stale_jobs, "stale_count": len(stale_jobs)}

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
        """Flatten Phase 3 learner proposals into a read-only Phase 3 KB view.

        Learner artifacts are intentionally separate from projected Kuzu nodes:
        they are source-backed study proposals, not referee-approved KB facts.
        This endpoint gives operators a browsable knowledge-base read model
        without promoting those proposals into the verified knowledge graph.
        """
        batches = list_learner_batches(self.ws_root)
        reviews = list_reviews(self.ws_root)
        review_repairs = latest_review_node_repairs_by_label(self.ws_root)
        bridge_repair_actions = _active_bridge_repair_actions(self.ws_root)
        nodes: list[dict[str, Any]] = []
        kind_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        issue_counts_by_type: dict[str, int] = {}
        notation_contexts_by_id: dict[str, dict[str, Any]] = {}
        source_notation_contexts: dict[str, list[str]] = {}
        learning_contract_counts: dict[str, int] = {}
        source_index = _source_provenance_index(self.ws_root, batches)

        for batch in batches:
            payload = batch.get("payload", {})
            if not isinstance(payload, dict):
                payload = {}
            source_id = batch.get("source_id", "") or payload.get("source_id", "")
            learner_run = batch.get("learner_run", "") or payload.get("learner_run", "")
            contract = payload.get("learning_contract", {})
            if isinstance(contract, dict) and contract:
                contract_key = json.dumps(contract, sort_keys=True, ensure_ascii=False)
                learning_contract_counts[contract_key] = learning_contract_counts.get(contract_key, 0) + 1
            for ctx in payload.get("notation_contexts", []) or []:
                if not isinstance(ctx, dict):
                    continue
                context_id = _notation_context_id(ctx)
                if not context_id:
                    continue
                existing = notation_contexts_by_id.setdefault(context_id, {"context_id": context_id})
                _merge_dicts(existing, ctx)
                _append_unique_str(source_notation_contexts.setdefault(str(source_id), []), context_id)
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
                source_meta = source_index.get(source_id, {})
                span_index = source_meta.get("spans", {}) if isinstance(source_meta, dict) else {}
                external_source_refs = [
                    _span_source_ref(ref, span_index if isinstance(span_index, dict) else {})
                    for ref in refs
                    if isinstance(ref, dict)
                ]
                bibliography = dict(source_meta.get("bibliography", {})) if isinstance(source_meta, dict) else {}
                _merge_dicts(bibliography, node.get("bibliography"))
                external_citation = node.get("external_citation") if isinstance(node.get("external_citation"), dict) else {}
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
                        "extraction_kind": node.get("extraction_kind", ""),
                        "source_locator": node.get("source_locator", ""),
                        "notation_context_ids": [
                            _notation_context_id(ctx)
                            for ctx in (payload.get("notation_contexts", []) or [])
                            if _notation_context_id(ctx)
                        ],
                        "learning_contract": contract if isinstance(contract, dict) else {},
                        "source_id": source_id,
                        "source_refs": refs,
                        "external_source_refs": [ref for ref in external_source_refs if ref],
                        "bibliography": bibliography if kind == "external_theorem" else {},
                        "external_citation": external_citation,
                        "citation_status": (
                            str(node.get("citation_status") or external_citation.get("status") or "source_provisional")
                            if kind == "external_theorem"
                            else ""
                        ),
                        "span_ids": span_ids,
                        "learner_run": learner_run,
                        "event_id": batch.get("event_id", ""),
                        "status": batch.get("status", ""),
                        "ts": batch.get("ts", ""),
                        "batch_issue_count": batch_issue_count,
                    }
                )

        for review in reviews:
            payload = review.get("payload", {}) if isinstance(review.get("payload"), dict) else {}
            report = payload.get("report", {}) if isinstance(payload.get("report"), dict) else {}
            theorem_nodes = report.get("theorem_nodes", []) if isinstance(report, dict) else []
            if not isinstance(theorem_nodes, list):
                theorem_nodes = []
            review_id = str(payload.get("review_id", "") or review.get("event_id", "") or "")
            target = str(review.get("target", "") or report.get("target", "") or "")
            source_meta = source_index.get(target, {})
            span_index = source_meta.get("spans", {}) if isinstance(source_meta, dict) else {}
            issue_summary = payload.get("issue_summary", {})
            issue_count = (
                int(issue_summary.get("issue_count", 0) or 0)
                if isinstance(issue_summary, dict)
                else 0
            )
            for node in theorem_nodes:
                if not isinstance(node, dict):
                    continue
                node = _apply_review_node_repair(
                    node, review_repairs.get(str(node.get("label", "") or ""))
                )
                kind = str(node.get("kind", "") or "")
                label = str(node.get("label", "") or "")
                if not label:
                    continue
                source_excerpt, source_span_ids, needs_visual_check = _source_excerpt_for_node(
                    node,
                    span_index if isinstance(span_index, dict) else {},
                )
                formula_excerpt = _safe_text(node.get("formula_excerpt"))
                if not formula_excerpt:
                    formula_excerpt = _formula_excerpt_from_text(source_excerpt)
                display_source_excerpt = _safe_text(node.get("display_source_excerpt"))
                display_formula_excerpt = _safe_text(node.get("display_formula_excerpt"))
                kind_counts[kind] = kind_counts.get(kind, 0) + 1
                if target:
                    source_counts[target] = source_counts.get(target, 0) + 1
                nodes.append(
                    {
                        "label": label,
                        "title": node.get("title", ""),
                        "kind": kind,
                        "statement": node.get("statement", ""),
                        "source_excerpt": source_excerpt,
                        "formula_excerpt": formula_excerpt,
                        "display_source_excerpt": display_source_excerpt,
                        "display_formula_excerpt": display_formula_excerpt,
                        "display_render_mode": node.get("display_render_mode", ""),
                        "formula_display_render_mode": node.get("formula_display_render_mode", ""),
                        "typesetting_notes": node.get("typesetting_notes", ""),
                        "has_repair_overlay": bool(node.get("has_repair_overlay")),
                        "repair_id": node.get("repair_id", ""),
                        "repair_ts": node.get("repair_ts", ""),
                        "repair_kind": node.get("repair_kind", ""),
                        "repair_version": node.get("repair_version", None),
                        "repair_active": node.get("repair_active", True),
                        "repair_supersedes": node.get("repair_supersedes", []) or [],
                        "repair_summary": node.get("repair_summary", {}) or {},
                        "repair_actions": node.get("repair_actions", []) or [],
                        "proof": "",
                        "proof_status": node.get("status", ""),
                        "proof_steps": [],
                        "depends_on": [],
                        "remark": node.get("remark", ""),
                        "source_note": node.get("source_note", ""),
                        "extraction_kind": node.get("extraction_kind", "review_inferred"),
                        "source_locator": node.get("source_locator", ""),
                        "promotion_confidence": node.get("promotion_confidence", None),
                        "overpromotion_risk": node.get("overpromotion_risk", None),
                        "scope": node.get("scope", ""),
                        "needs_visual_check": needs_visual_check,
                        "notation_context_ids": [],
                        "learning_contract": {},
                        "source_id": target,
                        "source_refs": node.get("source_refs", []) or [],
                        "external_source_refs": [],
                        "bibliography": {},
                        "external_citation": {},
                        "citation_status": "review_only" if kind == "external_theorem" else "",
                        "span_ids": source_span_ids,
                        "learner_run": review_id,
                        "review_id": review_id,
                        "review_verdict": payload.get("verdict", ""),
                        "event_id": review.get("event_id", ""),
                        "status": "referee_graph",
                        "source_type": "referee_graph",
                        "ts": review.get("ts", ""),
                        "batch_issue_count": issue_count,
                    }
                )

        nodes = _apply_bridge_repairs_to_study_nodes(nodes, bridge_repair_actions)
        kind_counts = _count_kinds(nodes)
        nodes.sort(key=lambda d: (d["source_id"], d["kind"], d["label"]))
        graph = self._study_graph_full()
        review_rows_all = self.reviews()["reviews"]
        reviews_by_target = _reviews_by_target(review_rows_all)
        reviews_by_id = {
            str(r.get("review_id", "") or r.get("event_id", "") or ""): r
            for r in review_rows_all
            if str(r.get("review_id", "") or r.get("event_id", "") or "")
        }
        versions_by_label: dict[str, list[dict[str, Any]]] = {}
        for node in nodes:
            label = str(node.get("label", "") or "")
            if label:
                versions_by_label.setdefault(label, []).append(node)
        for versions in versions_by_label.values():
            versions.sort(key=lambda n: (n.get("ts", ""), n.get("event_id", "")), reverse=True)

        graph_edges = list(graph["edges"])
        kinds_by_label = {
            str(n.get("label", "") or ""): str(n.get("kind", "") or "")
            for n in graph["nodes"]
        }
        dependencies_by_label: dict[str, list[str]] = {}
        dependents_by_label: dict[str, list[str]] = {}
        bridges_by_label: dict[str, list[str]] = {}
        bridge_users_by_label: dict[str, list[str]] = {}
        context_by_label: dict[str, list[str]] = {}
        context_for_label: dict[str, list[str]] = {}
        relations_by_pair: dict[tuple[str, str], list[str]] = {}
        for edge in graph_edges:
            source = str(edge.get("source", "") or "")
            target = str(edge.get("target", "") or "")
            relation = str(edge.get("relation", "") or "depends_on")
            if not source or not target:
                continue
            bucket = _study_edge_bucket(edge, kinds_by_label)
            if bucket == "dependency":
                dependencies_by_label.setdefault(source, [])
                dependents_by_label.setdefault(target, [])
                if target not in dependencies_by_label[source]:
                    dependencies_by_label[source].append(target)
                if source not in dependents_by_label[target]:
                    dependents_by_label[target].append(source)
            elif bucket == "bridge":
                bridges_by_label.setdefault(source, [])
                bridge_users_by_label.setdefault(target, [])
                if target not in bridges_by_label[source]:
                    bridges_by_label[source].append(target)
                if source not in bridge_users_by_label[target]:
                    bridge_users_by_label[target].append(source)
            elif bucket == "context":
                context_by_label.setdefault(source, [])
                context_for_label.setdefault(target, [])
                if target not in context_by_label[source]:
                    context_by_label[source].append(target)
                if source not in context_for_label[target]:
                    context_for_label[target].append(source)
            pair = (source, target)
            relations_by_pair.setdefault(pair, [])
            if relation not in relations_by_pair[pair]:
                relations_by_pair[pair].append(relation)

        entries: list[dict[str, Any]] = []
        knowledge_status_counts: dict[str, int] = {}
        review_status_counts: dict[str, int] = {}
        for graph_node in graph["nodes"]:
            label = str(graph_node.get("label", "") or "")
            if not label:
                continue
            versions = versions_by_label.get(label, [])
            latest_version = versions[0] if versions else {}
            raw_reviews = list(reviews_by_target.get(label, []))
            review_id = str(graph_node.get("review_id", "") or "")
            if review_id and review_id in reviews_by_id and reviews_by_id[review_id] not in raw_reviews:
                raw_reviews.append(reviews_by_id[review_id])
            raw_reviews.sort(
                key=lambda r: (
                    str(r.get("ts", "") or ""),
                    str(r.get("event_id", "") or ""),
                ),
                reverse=True,
            )
            review_rows = [_review_summary(r) for r in raw_reviews]
            latest_review = review_rows[0] if review_rows else None
            knowledge_status = _phase3_knowledge_status(graph_node, latest_review)
            review_status = _review_status(latest_review)
            knowledge_status_counts[knowledge_status] = knowledge_status_counts.get(knowledge_status, 0) + 1
            review_status_counts[review_status] = review_status_counts.get(review_status, 0) + 1
            deps = sorted(dependencies_by_label.get(label, []))
            dependents = sorted(dependents_by_label.get(label, []))
            bridges = sorted(bridges_by_label.get(label, []))
            bridge_users = sorted(bridge_users_by_label.get(label, []))
            contexts = sorted(context_by_label.get(label, []))
            context_users = sorted(context_for_label.get(label, []))
            entry = {
                **graph_node,
                "dependency_count": len(deps),
                "dependent_count": len(dependents),
                "bridge_count": len(bridges),
                "bridge_user_count": len(bridge_users),
                "context_count": len(contexts),
                "context_user_count": len(context_users),
                "relation_count": int(graph_node.get("relation_count", 0) or 0),
                "knowledge_status": knowledge_status,
                "review_status": review_status,
                "review_count": len(review_rows),
                "latest_review": latest_review,
                "reviews": review_rows[:12],
                "version_count": len(versions),
                "latest_version": latest_version,
                "versions": [
                    {
                        "event_id": v.get("event_id", ""),
                        "learner_run": v.get("learner_run", ""),
                        "source_id": v.get("source_id", ""),
                        "proof_status": v.get("proof_status", ""),
                        "status": v.get("status", ""),
                        "ts": v.get("ts", ""),
                        "span_ids": v.get("span_ids", []),
                        "batch_issue_count": v.get("batch_issue_count", 0),
                    }
                    for v in versions[:20]
                ],
                "dependencies": [
                    {
                        "label": dep,
                        "relations": sorted(relations_by_pair.get((label, dep), [])),
                    }
                    for dep in deps
                ],
                "dependents": [
                    {
                        "label": dep,
                        "relations": sorted(relations_by_pair.get((dep, label), [])),
                    }
                    for dep in dependents
                ],
                "bridges": [
                    {
                        "label": dep,
                        "relations": sorted(relations_by_pair.get((label, dep), [])),
                    }
                    for dep in bridges
                ],
                "bridge_users": [
                    {
                        "label": dep,
                        "relations": sorted(relations_by_pair.get((dep, label), [])),
                    }
                    for dep in bridge_users
                ],
                "context": [
                    {
                        "label": dep,
                        "relations": sorted(relations_by_pair.get((label, dep), [])),
                    }
                    for dep in contexts
                ],
                "context_users": [
                    {
                        "label": dep,
                        "relations": sorted(relations_by_pair.get((dep, label), [])),
                    }
                    for dep in context_users
                ],
            }
            if latest_version:
                # Keep the best human-facing extracted text on the entry, while
                # preserving graph-level merged fields for missing/virtual nodes.
                for field in (
                    "proof",
                    "proof_steps",
                    "depends_on",
                    "source_refs",
                    "external_source_refs",
                    "bibliography",
                    "external_citation",
                    "citation_status",
                    "extraction_kind",
                    "source_locator",
                    "source_excerpt",
                    "formula_excerpt",
                    "display_source_excerpt",
                    "display_formula_excerpt",
                    "display_render_mode",
                    "formula_display_render_mode",
                    "typesetting_notes",
                    "has_repair_overlay",
                    "repair_id",
                    "repair_ts",
                    "repair_kind",
                    "repair_version",
                    "repair_active",
                    "repair_supersedes",
                    "repair_summary",
                    "repair_actions",
                    "needs_visual_check",
                    "notation_context_ids",
                    "learning_contract",
                    "title",
                    "promotion_confidence",
                    "overpromotion_risk",
                    "scope",
                    "review_id",
                    "review_verdict",
                    "source_type",
                ):
                    latest_value = latest_version.get(field, None)
                    if _has_meaningful_value(latest_value):
                        if isinstance(latest_value, list) and isinstance(entry.get(field), list):
                            for value in latest_value:
                                _append_unique_value(entry[field], value)
                        elif isinstance(latest_value, dict) and isinstance(entry.get(field), dict):
                            _merge_dicts(entry[field], latest_value)
                        else:
                            entry[field] = latest_value
            entries.append(entry)
        entries.sort(key=_study_entry_sort_key)
        return {
            "nodes": nodes,
            "entries": entries,
            "count": len(nodes),
            "entry_count": len(entries),
            "batch_count": len(batches),
            "review_count": len(reviews),
            "kind_counts": kind_counts,
            "source_counts": source_counts,
            "issue_counts_by_type": issue_counts_by_type,
            "knowledge_status_counts": knowledge_status_counts,
            "review_status_counts": review_status_counts,
            "notation_contexts": sorted(
                notation_contexts_by_id.values(),
                key=lambda ctx: str(ctx.get("context_id", "")),
            ),
            "source_notation_contexts": source_notation_contexts,
            "learning_contract_counts": learning_contract_counts,
            "bridge_repair_summary": graph.get("bridge_repair_summary", {}),
        }

    def _study_graph_full(self) -> dict[str, Any]:
        """Return learner-proposed candidates as a dependency graph.

        This is intentionally not the verified KB proof tree. It is a
        read-only operator view over Phase 3 artifacts: candidate nodes,
        candidate-to-candidate dependencies, external theorem references, and
        bridge requests. Labels are merged across learner batches so repeated
        enrichment runs become versions of the same graph node.
        """
        batches = list_learner_batches(self.ws_root)
        reviews = list_reviews(self.ws_root)
        review_repairs = latest_review_node_repairs_by_label(self.ws_root)
        bridge_repair_actions = _active_bridge_repair_actions(self.ws_root)
        source_index = _source_provenance_index(self.ws_root, batches)
        nodes_by_label: dict[str, dict[str, Any]] = {}
        bridge_requests_by_id: dict[str, dict[str, Any]] = {}
        edges_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}

        def ensure_node(label: str, *, kind: str | None = None, virtual: bool = False) -> dict[str, Any]:
            label = str(label or "")
            node = nodes_by_label.get(label)
            if node is None:
                node = {
                    "id": label,
                    "label": label,
                    "kind": kind or _infer_study_graph_kind(label),
                    "status": "virtual" if virtual else "proposed",
                    "proof_status": "",
                    "source_ids": [],
                    "learner_runs": [],
                    "event_ids": [],
                    "span_ids": [],
                    "versions": 0,
                    "issue_count": 0,
                    "statement": "",
                    "source_excerpt": "",
                    "formula_excerpt": "",
                    "display_source_excerpt": "",
                    "display_formula_excerpt": "",
                    "display_render_mode": "",
                    "formula_display_render_mode": "",
                    "typesetting_notes": "",
                    "remark": "",
                    "source_note": "",
                    "extraction_kind": "",
                    "source_locator": "",
                    "needs_visual_check": False,
                    "bibliography": {},
                    "external_source_refs": [],
                    "citation_status": "",
                    "notation_context_ids": [],
                    "learning_contracts": [],
                    "virtual": virtual,
                }
                nodes_by_label[label] = node
            elif kind and node.get("kind") in {"missing_candidate", ""}:
                node["kind"] = kind
            if not virtual:
                node["virtual"] = False
                if node.get("status") == "virtual":
                    node["status"] = "proposed"
            return node

        def add_edge(
            dependent: str,
            dependency: str,
            *,
            relation: str,
            source_type: str,
            event_id: str,
            learner_run: str,
        ) -> None:
            dependent = str(dependent or "")
            dependency = str(dependency or "")
            if not dependent or not dependency:
                return
            ensure_node(dependent)
            dep_kind = "bridge_request" if dependency in bridge_requests_by_id else None
            ensure_node(dependency, kind=dep_kind, virtual=dependency not in nodes_by_label)
            key = (dependent, dependency, relation or source_type)
            edge = edges_by_key.get(key)
            if edge is None:
                edge = {
                    "source": dependent,
                    "target": dependency,
                    "dependent": dependent,
                    "dependency": dependency,
                    "relation": relation or "depends_on",
                    "source_types": [],
                    "event_ids": [],
                    "learner_runs": [],
                }
                edges_by_key[key] = edge
            _append_unique_str(edge["source_types"], source_type)
            _append_unique_str(edge["event_ids"], event_id)
            _append_unique_str(edge["learner_runs"], learner_run)

        def merge_review_node(
            *,
            node_raw: dict[str, Any],
            target: str,
            event_id: str,
            review_id: str,
            issue_count: int,
            verdict: str,
            span_index: dict[str, dict[str, Any]],
        ) -> None:
            label = str(node_raw.get("label", "") or "")
            if not label:
                return
            node = ensure_node(label, kind=str(node_raw.get("kind", "") or ""), virtual=False)
            node["versions"] = int(node.get("versions", 0) or 0) + 1
            node["issue_count"] = int(node.get("issue_count", 0) or 0) + issue_count
            node["status"] = "referee_graph"
            node["proof_status"] = str(node_raw.get("status", "") or node.get("proof_status", ""))
            node["statement"] = str(node_raw.get("statement", "") or node.get("statement", ""))
            source_excerpt, source_span_ids, needs_visual_check = _source_excerpt_for_node(
                node_raw, span_index
            )
            if source_excerpt:
                node["source_excerpt"] = source_excerpt
            formula_excerpt = _safe_text(node_raw.get("formula_excerpt"))
            if not formula_excerpt:
                formula_excerpt = _formula_excerpt_from_text(source_excerpt)
            if formula_excerpt:
                node["formula_excerpt"] = formula_excerpt
            display_source_excerpt = _safe_text(node_raw.get("display_source_excerpt"))
            if display_source_excerpt:
                node["display_source_excerpt"] = display_source_excerpt
            display_formula_excerpt = _safe_text(node_raw.get("display_formula_excerpt"))
            if display_formula_excerpt:
                node["display_formula_excerpt"] = display_formula_excerpt
            display_render_mode = _safe_text(node_raw.get("display_render_mode"))
            if display_render_mode:
                node["display_render_mode"] = display_render_mode
            formula_display_render_mode = _safe_text(node_raw.get("formula_display_render_mode"))
            if formula_display_render_mode:
                node["formula_display_render_mode"] = formula_display_render_mode
            typesetting_notes = _safe_text(node_raw.get("typesetting_notes"))
            if typesetting_notes:
                node["typesetting_notes"] = typesetting_notes
            if node_raw.get("has_repair_overlay"):
                node["has_repair_overlay"] = True
                node["repair_id"] = str(node_raw.get("repair_id", "") or "")
                node["repair_ts"] = str(node_raw.get("repair_ts", "") or "")
                node["repair_kind"] = str(node_raw.get("repair_kind", "") or "")
                node["repair_version"] = node_raw.get("repair_version", None)
                node["repair_active"] = bool(node_raw.get("repair_active", True))
                node["repair_supersedes"] = node_raw.get("repair_supersedes", []) or []
                node["repair_summary"] = node_raw.get("repair_summary", {}) or {}
                node["repair_actions"] = node_raw.get("repair_actions", []) or []
            node["title"] = str(node_raw.get("title", "") or node.get("title", ""))
            node["remark"] = str(node_raw.get("remark", "") or node.get("remark", ""))
            node["source_note"] = str(node_raw.get("source_note", "") or node.get("source_note", ""))
            node["extraction_kind"] = str(
                node_raw.get("extraction_kind", "") or node.get("extraction_kind", "")
            )
            node["source_locator"] = str(
                node_raw.get("source_locator", "") or node.get("source_locator", "")
            )
            if "promotion_confidence" in node_raw:
                node["promotion_confidence"] = node_raw.get("promotion_confidence")
            if "overpromotion_risk" in node_raw:
                node["overpromotion_risk"] = node_raw.get("overpromotion_risk")
            if needs_visual_check or bool(node_raw.get("needs_visual_check")):
                node["needs_visual_check"] = True
            if node_raw.get("scope"):
                node["scope"] = str(node_raw.get("scope"))
            if str(node.get("kind", "") or "") == "external_theorem":
                node["citation_status"] = str(node_raw.get("scope") or "review_only")
            node["source_type"] = "referee_graph"
            node["review_id"] = review_id
            node["review_verdict"] = verdict
            _append_unique_str(node["source_ids"], target)
            _append_unique_str(node["learner_runs"], review_id)
            _append_unique_str(node["event_ids"], event_id)
            for span_id in source_span_ids:
                _append_unique_str(node["span_ids"], span_id)

        for batch in batches:
            payload = batch.get("payload", {})
            if not isinstance(payload, dict):
                payload = {}
            for req in payload.get("bridge_requests", []) or []:
                if not isinstance(req, dict):
                    continue
                request_id = _bridge_request_id(req)
                if not request_id:
                    continue
                bridge_requests_by_id[request_id] = req

        for batch in batches:
            payload = batch.get("payload", {})
            if not isinstance(payload, dict):
                payload = {}
            source_id = str(batch.get("source_id", "") or payload.get("source_id", "") or "")
            learner_run = str(batch.get("learner_run", "") or payload.get("learner_run", "") or "")
            event_id = str(batch.get("event_id", "") or "")
            issues = payload.get("issues", []) or []
            issue_count = len(issues) if isinstance(issues, list) else 0
            candidate_labels = {
                str(node.get("label", "") or "")
                for node in payload.get("candidate_nodes", []) or []
                if isinstance(node, dict) and str(node.get("label", "") or "")
            }

            for node_raw in payload.get("candidate_nodes", []) or []:
                if not isinstance(node_raw, dict):
                    continue
                label = str(node_raw.get("label", "") or "")
                if not label:
                    continue
                node = ensure_node(label, kind=str(node_raw.get("kind", "") or ""), virtual=False)
                first_version_for_label = int(node.get("versions", 0) or 0) == 0
                node["versions"] = int(node.get("versions", 0) or 0) + 1
                node["issue_count"] = int(node.get("issue_count", 0) or 0) + issue_count
                if first_version_for_label:
                    node["status"] = str(batch.get("status", "") or "proposed")
                    node["proof_status"] = str(
                        node_raw.get("proof_status", "") or node.get("proof_status", "")
                    )
                    node["statement"] = str(
                        node_raw.get("statement", "") or node.get("statement", "")
                    )
                    node["remark"] = str(node_raw.get("remark", "") or node.get("remark", ""))
                    node["source_note"] = str(
                        node_raw.get("source_note", "") or node.get("source_note", "")
                    )
                    node["extraction_kind"] = str(
                        node_raw.get("extraction_kind", "") or node.get("extraction_kind", "")
                    )
                    node["source_locator"] = str(
                        node_raw.get("source_locator", "") or node.get("source_locator", "")
                    )
                contract = payload.get("learning_contract", {})
                if isinstance(contract, dict) and contract:
                    contract_key = json.dumps(contract, sort_keys=True, ensure_ascii=False)
                    if contract_key not in node["learning_contracts"]:
                        node["learning_contracts"].append(contract_key)
                for ctx in payload.get("notation_contexts", []) or []:
                    _append_unique_str(node["notation_context_ids"], _notation_context_id(ctx))
                if str(node.get("kind", "") or "") == "external_theorem":
                    source_meta = source_index.get(source_id, {})
                    span_index = source_meta.get("spans", {}) if isinstance(source_meta, dict) else {}
                    bibliography = dict(source_meta.get("bibliography", {})) if isinstance(source_meta, dict) else {}
                    _merge_dicts(bibliography, node_raw.get("bibliography"))
                    if bibliography:
                        _merge_dicts(node["bibliography"], bibliography)
                    citation = node_raw.get("external_citation")
                    if isinstance(citation, dict):
                        node["external_citation"] = citation
                    node["citation_status"] = str(
                        node_raw.get("citation_status")
                        or (citation.get("status") if isinstance(citation, dict) else "")
                        or node.get("citation_status")
                        or "source_provisional"
                    )
                    for ref in node_raw.get("source_refs", []) or []:
                        source_ref = _span_source_ref(ref, span_index if isinstance(span_index, dict) else {})
                        if source_ref and source_ref not in node["external_source_refs"]:
                            node["external_source_refs"].append(source_ref)
                _append_unique_str(node["source_ids"], source_id)
                _append_unique_str(node["learner_runs"], learner_run)
                _append_unique_str(node["event_ids"], event_id)
                for span_id in _source_ref_span_ids(node_raw.get("source_refs", []) or []):
                    _append_unique_str(node["span_ids"], span_id)

                for dependency in node_raw.get("depends_on", []) or []:
                    add_edge(
                        label,
                        dependency,
                        relation="depends_on",
                        source_type="candidate.depends_on",
                        event_id=event_id,
                        learner_run=learner_run,
                    )
                for step in node_raw.get("proof_steps", []) or []:
                    if not isinstance(step, dict):
                        continue
                    for dependency in step.get("depends_on", []) or []:
                        add_edge(
                            label,
                            dependency,
                            relation="proof_step",
                            source_type="candidate.proof_steps",
                            event_id=event_id,
                            learner_run=learner_run,
                        )

            for edge_raw in payload.get("dependency_edges", []) or []:
                if not isinstance(edge_raw, dict):
                    continue
                dependent, dependency = _edge_endpoints(edge_raw, candidate_labels)
                add_edge(
                    dependent,
                    dependency,
                    relation=str(edge_raw.get("relation", "") or "uses"),
                    source_type="batch.dependency_edges",
                    event_id=event_id,
                    learner_run=learner_run,
                )

        for request_id, req in bridge_requests_by_id.items():
            node = ensure_node(request_id, kind="bridge_request", virtual=True)
            node["status"] = "bridge_request"
            node["proof_status"] = "needs_bridge"
            node["statement"] = str(
                req.get("to_claim", "")
                or req.get("request", "")
                or req.get("target", "")
                or req.get("from_claim", "")
                or ""
            )
            node["remark"] = str(req.get("reason", "") or req.get("from_claim", "") or "")
            for blocked in _bridge_request_blocks(req):
                add_edge(
                    str(blocked or ""),
                    request_id,
                    relation="needs_bridge",
                    source_type="bridge_request.blocks",
                    event_id="",
                    learner_run="",
                )
            for local_label in req.get("local_context_labels", []) or []:
                add_edge(
                    request_id,
                    str(local_label or ""),
                    relation="local_context",
                    source_type="bridge_request.local_context",
                    event_id="",
                    learner_run="",
                )

        for review in reviews:
            payload = review.get("payload", {}) if isinstance(review.get("payload"), dict) else {}
            report = payload.get("report", {}) if isinstance(payload.get("report"), dict) else {}
            if not isinstance(report, dict):
                continue
            review_id = str(payload.get("review_id", "") or review.get("event_id", "") or "")
            event_id = str(review.get("event_id", "") or "")
            target = str(review.get("target", "") or report.get("target", "") or "")
            verdict = str(payload.get("verdict", "") or "")
            source_meta = source_index.get(target, {})
            span_index = source_meta.get("spans", {}) if isinstance(source_meta, dict) else {}
            issue_summary = payload.get("issue_summary", {})
            issue_count = (
                int(issue_summary.get("issue_count", 0) or 0)
                if isinstance(issue_summary, dict)
                else 0
            )
            theorem_nodes = report.get("theorem_nodes", [])
            if isinstance(theorem_nodes, list):
                for node_raw in theorem_nodes:
                    if isinstance(node_raw, dict):
                        node_raw = _apply_review_node_repair(
                            node_raw,
                            review_repairs.get(str(node_raw.get("label", "") or "")),
                        )
                        merge_review_node(
                            node_raw=node_raw,
                            target=target,
                            event_id=event_id,
                            review_id=review_id,
                            issue_count=issue_count,
                            verdict=verdict,
                            span_index=span_index if isinstance(span_index, dict) else {},
                        )
            theorem_edges = report.get("theorem_dependency_edges", [])
            if isinstance(theorem_edges, list):
                for edge_raw in theorem_edges:
                    if not isinstance(edge_raw, dict):
                        continue
                    add_edge(
                        str(edge_raw.get("dependent", "") or ""),
                        str(edge_raw.get("dependency", "") or ""),
                        relation=str(edge_raw.get("relation", "") or "depends_on"),
                        source_type="referee.theorem_dependency_edges",
                        event_id=event_id,
                        learner_run=review_id,
                    )

        bridge_repair_summary = _apply_bridge_repairs_to_study_graph(
            nodes_by_label,
            edges_by_key,
            bridge_repair_actions,
        )
        edges = sorted(
            edges_by_key.values(),
            key=lambda e: (e["source"], e["target"], e["relation"]),
        )
        nodes = sorted(
            nodes_by_label.values(),
            key=lambda n: (str(n.get("kind", "")), str(n.get("label", ""))),
        )
        relation_counts: dict[str, int] = {}
        relation_counts_by_source: dict[str, int] = {}
        relation_counts_by_target: dict[str, int] = {}
        kind_counts: dict[str, int] = {}
        for edge in edges:
            relation = str(edge.get("relation", "") or "unknown")
            source = str(edge.get("source", "") or "")
            target = str(edge.get("target", "") or "")
            relation_counts[relation] = relation_counts.get(relation, 0) + 1
            relation_counts_by_source[source] = relation_counts_by_source.get(source, 0) + 1
            relation_counts_by_target[target] = relation_counts_by_target.get(target, 0) + 1
        for node in nodes:
            kind = str(node.get("kind", "") or "unknown")
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
        dependency_counts: dict[str, int] = {}
        dependent_counts: dict[str, int] = {}
        dependency_labels: dict[str, set[str]] = {}
        dependent_labels: dict[str, set[str]] = {}
        bridge_counts: dict[str, int] = {}
        bridge_user_counts: dict[str, int] = {}
        bridge_labels: dict[str, set[str]] = {}
        bridge_user_labels: dict[str, set[str]] = {}
        kinds_by_label = {
            str(node.get("label", "") or ""): str(node.get("kind", "") or "")
            for node in nodes
        }
        for edge in edges:
            bucket = _study_edge_bucket(edge, kinds_by_label)
            if bucket == "dependency":
                dependency_labels.setdefault(edge["source"], set()).add(edge["target"])
                dependent_labels.setdefault(edge["target"], set()).add(edge["source"])
                dependency_counts[edge["source"]] = dependency_counts.get(edge["source"], 0) + 1
                dependent_counts[edge["target"]] = dependent_counts.get(edge["target"], 0) + 1
            elif bucket == "bridge":
                bridge_labels.setdefault(edge["source"], set()).add(edge["target"])
                bridge_user_labels.setdefault(edge["target"], set()).add(edge["source"])
                bridge_counts[edge["source"]] = bridge_counts.get(edge["source"], 0) + 1
                bridge_user_counts[edge["target"]] = bridge_user_counts.get(edge["target"], 0) + 1
        for node in nodes:
            label = str(node.get("label", "") or "")
            node["relation_count"] = relation_counts_by_source.get(label, 0)
            node["relation_user_count"] = relation_counts_by_target.get(label, 0)
            node["relation_dependency_count"] = dependency_counts.get(label, 0)
            node["relation_dependent_count"] = dependent_counts.get(label, 0)
            node["relation_bridge_count"] = bridge_counts.get(label, 0)
            node["relation_bridge_user_count"] = bridge_user_counts.get(label, 0)
            node["dependency_count"] = len(dependency_labels.get(label, set()))
            node["dependent_count"] = len(dependent_labels.get(label, set()))
            node["bridge_count"] = len(bridge_labels.get(label, set()))
            node["bridge_user_count"] = len(bridge_user_labels.get(label, set()))
        return {
            "nodes": nodes,
            "edges": edges,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "batch_count": len(batches),
            "review_count": len(reviews),
            "kind_counts": kind_counts,
            "relation_counts": relation_counts,
            "bridge_repair_summary": bridge_repair_summary,
            "truncated": False,
        }

    def study_graph(
        self, *, root: str | None = None, depth: int = 1, limit: int = 80
    ) -> dict[str, Any]:
        """Compatibility graph endpoint.

        Without a root this returns a bounded overview of root-like nodes; with
        a root it returns a bounded local neighborhood. Large clients should use
        ``study_roots`` and ``study_neighborhood`` directly.
        """
        graph = self._study_graph_full()
        nodes = list(graph["nodes"])
        edges = list(graph["edges"])
        if root:
            nodes, edges, truncated = _slice_study_graph(
                nodes=nodes,
                edges=edges,
                root=root,
                depth=max(0, min(depth, 4)),
                limit=max(1, min(limit, 300)),
                direction="both",
            )
        else:
            truncated = len(nodes) > limit
            if truncated:
                rootish = _study_graph_root_labels(nodes, edges)
                keep = set(rootish[: max(1, min(limit, len(rootish)))])
                nodes = [n for n in nodes if n["label"] in keep]
                edges = [
                    e
                    for e in edges
                    if e["source"] in keep and e["target"] in keep
                ]
        return graph | {
            "nodes": nodes,
            "edges": edges,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "root": root or "",
            "depth": depth,
            "limit": limit,
            "truncated": truncated,
        }

    def study_search(
        self,
        *,
        q: str = "",
        kind: str = "",
        proof_status: str = "",
        source: str = "",
        run: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        graph = self._study_graph_full()
        query = q.strip().lower()
        rows = [
            n for n in graph["nodes"]
            if _study_node_matches(
                n,
                query=query,
                kind=kind,
                proof_status=proof_status,
                source=source,
                run=run,
            )
        ]
        rows.sort(key=_study_attention_sort_key)
        truncated = len(rows) > limit
        return {
            "nodes": rows[:limit],
            "count": min(len(rows), limit),
            "total": len(rows),
            "limit": limit,
            "truncated": truncated,
        }

    def study_node(self, label: str) -> dict[str, Any] | None:
        graph = self._study_graph_full()
        by_label = {n["label"]: n for n in graph["nodes"]}
        node = by_label.get(label)
        if node is None:
            return None
        kinds_by_label = {
            str(n.get("label", "") or ""): str(n.get("kind", "") or "")
            for n in graph["nodes"]
        }
        outgoing = [e for e in graph["edges"] if e["source"] == label]
        incoming = [e for e in graph["edges"] if e["target"] == label]
        dependencies = [
            e for e in outgoing if _study_edge_bucket(e, kinds_by_label) == "dependency"
        ]
        dependents = [
            e for e in incoming if _study_edge_bucket(e, kinds_by_label) == "dependency"
        ]
        bridges = [
            e for e in outgoing if _study_edge_bucket(e, kinds_by_label) == "bridge"
        ]
        bridge_users = [
            e for e in incoming if _study_edge_bucket(e, kinds_by_label) == "bridge"
        ]
        context = [
            e for e in outgoing if _study_edge_bucket(e, kinds_by_label) == "context"
        ]
        context_users = [
            e for e in incoming if _study_edge_bucket(e, kinds_by_label) == "context"
        ]
        versions = [n for n in self.study_kb()["nodes"] if n.get("label") == label]
        versions.sort(key=lambda n: (n.get("ts", ""), n.get("event_id", "")), reverse=True)
        reviews = [_review_summary(r) for r in _reviews_by_target(self.reviews()["reviews"]).get(label, [])]
        return {
            "node": node,
            "dependencies": dependencies,
            "dependents": dependents,
            "bridges": bridges,
            "bridge_users": bridge_users,
            "context": context,
            "context_users": context_users,
            "relations": outgoing,
            "relation_users": incoming,
            "versions": versions,
            "version_count": len(versions),
            "reviews": reviews,
            "review_count": len(reviews),
            "latest_review": reviews[0] if reviews else None,
            "knowledge_status": _phase3_knowledge_status(node, reviews[0] if reviews else None),
        }

    def study_neighborhood(
        self,
        *,
        root: str,
        direction: str = "dependencies",
        depth: int = 1,
        limit: int = 80,
    ) -> dict[str, Any]:
        graph = self._study_graph_full()
        nodes, edges, truncated = _slice_study_graph(
            nodes=list(graph["nodes"]),
            edges=list(graph["edges"]),
            root=root,
            depth=max(0, min(depth, 4)),
            limit=max(1, min(limit, 300)),
            direction=direction,
        )
        return {
            "root": root,
            "direction": direction,
            "depth": depth,
            "limit": limit,
            "nodes": nodes,
            "edges": edges,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "truncated": truncated,
        }

    def study_roots(
        self,
        *,
        q: str = "",
        kind: str = "",
        proof_status: str = "",
        source: str = "",
        run: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        graph = self._study_graph_full()
        query = q.strip().lower()
        candidates = {
            n["label"]: n for n in graph["nodes"]
            if _study_node_matches(
                n,
                query=query,
                kind=kind,
                proof_status=proof_status,
                source=source,
                run=run,
            )
        }
        root_labels = [
            label for label in _study_graph_root_labels(list(candidates.values()), graph["edges"])
            if label in candidates
        ]
        if not root_labels:
            root_labels = [n["label"] for n in sorted(candidates.values(), key=_study_attention_sort_key)]
        rows = [candidates[label] for label in root_labels]
        truncated = len(rows) > limit
        return {
            "nodes": rows[:limit],
            "count": min(len(rows), limit),
            "total": len(rows),
            "limit": limit,
            "truncated": truncated,
        }

    def study_attention(self, *, limit: int = 80) -> dict[str, Any]:
        graph = self._study_graph_full()
        rows: list[dict[str, Any]] = []
        for node in graph["nodes"]:
            kind = str(node.get("kind", "") or "")
            proof_status = str(node.get("proof_status", "") or "")
            reasons: list[str] = []
            if kind == "bridge_request":
                reasons.append("bridge_request")
            if proof_status in {"proof_incomplete", "statement_only"} and kind in {
                "lemma", "proposition", "theorem", "corollary"
            }:
                reasons.append(proof_status)
            if int(node.get("issue_count", 0) or 0) > 0:
                reasons.append("learner_issues")
            if bool(node.get("virtual")):
                reasons.append("virtual_or_missing")
            if reasons:
                rows.append(node | {"attention_reasons": reasons})
        rows.sort(key=_study_attention_sort_key)
        truncated = len(rows) > limit
        return {
            "nodes": rows[:limit],
            "count": min(len(rows), limit),
            "total": len(rows),
            "limit": limit,
            "truncated": truncated,
        }

    def study_discovery(self) -> dict[str, Any]:
        """Return Phase 3 knowledge-organization signals.

        This is a compact operator surface for "what did learner cover, what is
        duplicated, and what still lacks referee/review attention?" It is
        derived from artifacts and does not promote anything into the formal KB.
        """
        graph = self._study_graph_full()
        kb = self.study_kb()
        reviews = self.reviews()["reviews"]

        versions_by_label: dict[str, list[dict[str, Any]]] = {}
        for node in kb["nodes"]:
            label = str(node.get("label", "") or "")
            if not label:
                continue
            versions_by_label.setdefault(label, []).append(node)
        duplicates = []
        for label, versions in versions_by_label.items():
            if len(versions) <= 1:
                continue
            versions.sort(key=lambda n: (n.get("ts", ""), n.get("event_id", "")), reverse=True)
            duplicates.append(
                {
                    "label": label,
                    "version_count": len(versions),
                    "kinds": sorted({str(v.get("kind", "") or "") for v in versions}),
                    "proof_statuses": sorted({str(v.get("proof_status", "") or "") for v in versions}),
                    "learner_runs": [v.get("learner_run", "") for v in versions],
                    "event_ids": [v.get("event_id", "") for v in versions],
                    "latest_statement": versions[0].get("statement", ""),
                }
            )
        duplicates.sort(key=lambda d: (-int(d["version_count"]), d["label"]))

        dispatched_spans: dict[str, dict[str, Any]] = {}
        cited_by_span: dict[str, set[str]] = {}
        issue_by_span: dict[str, int] = {}
        for batch in list_learner_batches(self.ws_root):
            payload = batch.get("payload", {})
            if not isinstance(payload, dict):
                continue
            for span in payload.get("source_spans", []) or []:
                if not isinstance(span, dict):
                    continue
                span_id = str(span.get("span_id", "") or span.get("id", "") or "")
                if not span_id:
                    continue
                dispatched_spans[span_id] = {
                    "span_id": span_id,
                    "span_hash": span.get("span_hash", "") or span.get("text_hash", ""),
                    "source_id": span.get("source_id", "") or batch.get("source_id", ""),
                }
            for node in payload.get("candidate_nodes", []) or []:
                if not isinstance(node, dict):
                    continue
                label = str(node.get("label", "") or "")
                for span_id in _source_ref_span_ids(node.get("source_refs", []) or []):
                    dispatched_spans.setdefault(
                        span_id,
                        {
                            "span_id": span_id,
                            "span_hash": "",
                            "source_id": batch.get("source_id", ""),
                        },
                    )
                    cited_by_span.setdefault(span_id, set()).add(label)
            for issue in payload.get("issues", []) or []:
                if not isinstance(issue, dict):
                    continue
                ref = issue.get("source_ref", {})
                if not isinstance(ref, dict):
                    continue
                span_id = str(ref.get("span_id", "") or "")
                if span_id:
                    issue_by_span[span_id] = issue_by_span.get(span_id, 0) + 1

        source_coverage = []
        for span_id, span in sorted(dispatched_spans.items()):
            labels = sorted(cited_by_span.get(span_id, set()))
            source_coverage.append(
                {
                    **span,
                    "candidate_count": len(labels),
                    "candidate_labels": labels[:20],
                    "issue_count": issue_by_span.get(span_id, 0),
                    "covered": bool(labels),
                }
            )

        reviewed_targets = {str(r.get("target", "") or "") for r in reviews if r.get("target")}
        review_coverage = {
            "review_count": len(reviews),
            "reviewed_count": len(reviewed_targets),
            "unreviewed_count": len([n for n in graph["nodes"] if n["label"] not in reviewed_targets]),
            "unreviewed_attention": [
                n for n in self.study_attention(limit=200)["nodes"]
                if n["label"] not in reviewed_targets
            ][:30],
        }

        return {
            "duplicates": duplicates,
            "duplicate_count": len(duplicates),
            "source_coverage": source_coverage,
            "source_span_count": len(source_coverage),
            "uncovered_source_span_count": len([s for s in source_coverage if not s["covered"]]),
            "review_coverage": review_coverage,
        }

    def reviews(self) -> dict[str, Any]:
        rows = []
        repairs_by_review: dict[str, list[dict[str, Any]]] = {}
        for repair in list_review_node_repairs(self.ws_root):
            rid = str(repair.get("review_id", "") or "")
            if rid:
                repairs_by_review.setdefault(rid, []).append(repair)
        for review in list_reviews(self.ws_root):
            payload = review.get("payload", {}) if isinstance(review.get("payload"), dict) else {}
            report = payload.get("report", {}) if isinstance(payload.get("report"), dict) else {}
            review_id = str(payload.get("review_id", "") or review.get("event_id", "") or "")
            repair_versions = repairs_by_review.get(review_id, [])
            active_repair = repair_versions[0] if repair_versions else {}
            active_summary = (
                active_repair.get("summary", {})
                if isinstance(active_repair.get("summary"), dict)
                else {}
            )
            repair_count = int(active_summary.get("node_count", 0) or 0)
            rows.append(
                {
                    "event_id": review.get("event_id", ""),
                    "review_id": review_id,
                    "target": review.get("target", ""),
                    "verdict": payload.get("verdict", ""),
                    "issue_summary": payload.get("issue_summary", {}),
                    "theorem_node_count": len(report.get("theorem_nodes", [])) if isinstance(report.get("theorem_nodes"), list) else 0,
                    "dependency_edge_count": len(report.get("theorem_dependency_edges", [])) if isinstance(report.get("theorem_dependency_edges"), list) else 0,
                    "typo_count": len(report.get("typo_findings", [])) if isinstance(report.get("typo_findings"), list) else 0,
                    "repair_count": repair_count,
                    "repair_version_count": len(repair_versions),
                    "latest_repair_id": active_repair.get("repair_id", ""),
                    "latest_repair_ts": active_repair.get("ts", ""),
                    "latest_repair_version": active_repair.get("version", None),
                    "repair_needs_visual_check_count": int(
                        active_summary.get("needs_visual_check_count", 0) or 0
                    ),
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

    def review_graph(self, review_id: str) -> dict[str, Any] | None:
        review = self.review(review_id)
        if review is None:
            return None
        payload = review.get("payload", {}) if isinstance(review.get("payload"), dict) else {}
        report = payload.get("report", {}) if isinstance(payload.get("report"), dict) else {}
        nodes = report.get("theorem_nodes", [])
        repairs = latest_review_node_repairs_by_label(self.ws_root, review_id=review_id)
        if isinstance(nodes, list):
            nodes = [
                _apply_review_node_repair(node, repairs.get(str(node.get("label", "") or "")))
                if isinstance(node, dict) else node
                for node in nodes
            ]
        edges = report.get("theorem_dependency_edges", [])
        notes = report.get("node_location_notes", [])
        return {
            "review_id": str(payload.get("review_id", "") or review.get("event_id", "") or ""),
            "target": str(review.get("target", "") or ""),
            "verdict": str(payload.get("verdict", "") or ""),
            "theorem_nodes": nodes if isinstance(nodes, list) else [],
            "theorem_dependency_edges": edges if isinstance(edges, list) else [],
            "node_location_notes": notes if isinstance(notes, list) else [],
        }

    def review_repairs(self, review_id: str) -> dict[str, Any] | None:
        if self.review(review_id) is None:
            return None
        rows = []
        artifacts = [
            repair for repair in list_review_node_repairs(self.ws_root)
            if repair.get("review_id") == review_id
        ]
        for index, repair in enumerate(artifacts):
            summary = repair.get("summary", {}) if isinstance(repair.get("summary"), dict) else {}
            rows.append(
                {
                    "repair_id": repair.get("repair_id", ""),
                    "review_id": repair.get("review_id", ""),
                    "repair_kind": repair.get("repair_kind", ""),
                    "version": repair.get("version", None),
                    "active": index == 0,
                    "actor": repair.get("actor", ""),
                    "ts": repair.get("ts", ""),
                    "node_count": int(summary.get("node_count", 0) or 0),
                    "formula_node_count": int(summary.get("formula_node_count", 0) or 0),
                    "needs_visual_check_count": int(
                        summary.get("needs_visual_check_count", 0) or 0
                    ),
                    "supersedes": repair.get("supersedes", []) or [],
                    "artifact_path": repair.get("_path", ""),
                }
            )
        return {"review_id": review_id, "repairs": rows, "count": len(rows)}

    def review_typos(self, review_id: str) -> dict[str, Any] | None:
        review = self.review(review_id)
        if review is None:
            return None
        payload = review.get("payload", {}) if isinstance(review.get("payload"), dict) else {}
        report = payload.get("report", {}) if isinstance(payload.get("report"), dict) else {}
        findings = report.get("typo_findings", [])
        return {
            "review_id": str(payload.get("review_id", "") or review.get("event_id", "") or ""),
            "target": str(review.get("target", "") or ""),
            "typo_findings": findings if isinstance(findings, list) else [],
        }


def _study_graph_root_labels(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> list[str]:
    """Return labels that are useful graph entry points.

    Edges point from dependent -> dependency, so conclusion-like roots are
    nodes nobody else depends on. When the graph has cycles or only isolated
    nodes, fall back to propositions/theorems/lemmas before definitions.
    """
    labels = {str(n.get("label", "") or "") for n in nodes}
    dependencies = {str(e.get("target", "") or "") for e in edges}
    roots = sorted(labels - dependencies)
    if roots:
        return roots
    priority = {
        "theorem": 0,
        "proposition": 1,
        "lemma": 2,
        "external_theorem": 3,
        "definition": 4,
        "bridge_request": 5,
    }
    return [
        str(n.get("label", "") or "")
        for n in sorted(
            nodes,
            key=lambda n: (
                priority.get(str(n.get("kind", "") or ""), 9),
                str(n.get("label", "") or ""),
            ),
        )
        if n.get("label")
    ]


def _study_node_matches(
    node: dict[str, Any],
    *,
    query: str = "",
    kind: str = "",
    proof_status: str = "",
    source: str = "",
    run: str = "",
) -> bool:
    if kind and node.get("kind") != kind:
        return False
    if proof_status and node.get("proof_status") != proof_status:
        return False
    if source and source not in (node.get("source_ids") or []):
        return False
    if run and run not in (node.get("learner_runs") or []):
        return False
    if not query:
        return True
    hay = " ".join(
        str(x)
        for x in [
            node.get("kind", ""),
            node.get("label", ""),
            node.get("statement", ""),
            node.get("source_excerpt", ""),
            node.get("formula_excerpt", ""),
            node.get("display_source_excerpt", ""),
            node.get("display_formula_excerpt", ""),
            node.get("typesetting_notes", ""),
            node.get("remark", ""),
            node.get("source_note", ""),
            node.get("proof_status", ""),
            *(node.get("source_ids") or []),
            *(node.get("learner_runs") or []),
            *(node.get("span_ids") or []),
        ]
    ).lower()
    return query in hay


def _study_attention_sort_key(node: dict[str, Any]) -> tuple[int, str, str]:
    kind = str(node.get("kind", "") or "")
    proof_status = str(node.get("proof_status", "") or "")
    priority = 9
    if kind == "bridge_request":
        priority = 0
    elif proof_status == "proof_incomplete":
        priority = 1
    elif int(node.get("issue_count", 0) or 0) > 0:
        priority = 2
    elif proof_status == "statement_only":
        priority = 3
    return (priority, kind, str(node.get("label", "") or ""))


def _review_summary(review: dict[str, Any]) -> dict[str, Any]:
    issue_summary = review.get("issue_summary", {})
    if not isinstance(issue_summary, dict):
        issue_summary = {}
    return {
        "event_id": str(review.get("event_id", "") or ""),
        "review_id": str(review.get("review_id", "") or review.get("event_id", "") or ""),
        "target": str(review.get("target", "") or ""),
        "verdict": str(review.get("verdict", "") or ""),
        "issue_summary": issue_summary,
        "issue_count": int(issue_summary.get("issue_count", 0) or 0),
        "blocks_acceptance": bool(issue_summary.get("blocks_acceptance")),
        "hash": str(review.get("hash", "") or ""),
        "ts": str(review.get("ts", "") or ""),
        "artifact_path": str(review.get("artifact_path", "") or ""),
    }


def _reviews_by_target(reviews: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for review in reviews:
        target = str(review.get("target", "") or "")
        if target:
            grouped.setdefault(target, []).append(review)
    for rows in grouped.values():
        rows.sort(key=lambda r: (str(r.get("ts", "") or ""), str(r.get("event_id", "") or "")), reverse=True)
    return grouped


def _review_status(latest_review: dict[str, Any] | None) -> str:
    if not latest_review:
        return "unreviewed"
    verdict = str(latest_review.get("verdict", "") or "").lower()
    if bool(latest_review.get("blocks_acceptance")):
        return "blocks_acceptance"
    if verdict in {"accepted", "approved", "verified", "source_ok"}:
        return "accepted"
    if verdict in {"needs_revision", "rejected", "critical", "gap"}:
        return "needs_revision"
    return "reviewed"


def _phase3_knowledge_status(
    node: dict[str, Any], latest_review: dict[str, Any] | None
) -> str:
    review_status = _review_status(latest_review)
    if review_status in {"blocks_acceptance", "needs_revision"}:
        return "needs_revision"
    if review_status == "accepted":
        return "reviewed"
    if bool(node.get("virtual")):
        return "missing_candidate"
    kind = str(node.get("kind", "") or "")
    if kind == "bridge_request":
        return "needs_bridge"
    proof_status = str(node.get("proof_status", "") or "")
    if proof_status:
        return proof_status
    return "proposed"


def _study_entry_sort_key(entry: dict[str, Any]) -> tuple[int, str, str]:
    status_priority = {
        "needs_bridge": 0,
        "needs_revision": 1,
        "missing_candidate": 2,
        "proof_incomplete": 3,
        "statement_only": 4,
        "proof_sketch_extracted": 5,
        "source_proof_extracted": 6,
        "reviewed": 7,
        "proposed": 8,
    }
    kind_priority = {
        "theorem": 0,
        "proposition": 1,
        "lemma": 2,
        "corollary": 3,
        "definition": 4,
        "external_theorem": 5,
        "bridge_request": 6,
    }
    return (
        status_priority.get(str(entry.get("knowledge_status", "") or ""), 9),
        kind_priority.get(str(entry.get("kind", "") or ""), 9),
        str(entry.get("label", "") or ""),
    )


def _slice_study_graph(
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    root: str,
    depth: int,
    limit: int,
    direction: str = "dependencies",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    by_label = {str(n.get("label", "") or ""): n for n in nodes}
    if root not in by_label:
        return [], [], False
    outward: dict[str, list[str]] = {}
    inward: dict[str, list[str]] = {}
    for edge in edges:
        source = str(edge.get("source", "") or "")
        target = str(edge.get("target", "") or "")
        if source and target:
            outward.setdefault(source, []).append(target)
            inward.setdefault(target, []).append(source)

    keep: set[str] = {root}
    frontier: list[tuple[str, int]] = [(root, 0)]
    truncated = False
    while frontier:
        label, dist = frontier.pop(0)
        if dist >= depth:
            continue
        if direction == "dependents":
            neighbors = sorted(set(inward.get(label, [])))
        elif direction == "both":
            neighbors = sorted(set(outward.get(label, []) + inward.get(label, [])))
        else:
            neighbors = sorted(set(outward.get(label, [])))
        for nxt in neighbors:
            if nxt not in by_label:
                continue
            if nxt not in keep:
                if len(keep) >= limit:
                    truncated = True
                    continue
                keep.add(nxt)
                frontier.append((nxt, dist + 1))
    sliced_nodes = [n for n in nodes if str(n.get("label", "") or "") in keep]
    sliced_edges = [
        e
        for e in edges
        if str(e.get("source", "") or "") in keep
        and str(e.get("target", "") or "") in keep
    ]
    return sliced_nodes, sliced_edges, truncated


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


def _parse_int_query(
    qs: dict[str, list[str]], key: str, *, default: int, min_value: int, max_value: int
) -> int:
    raw = qs.get(key, [str(default)])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, value))


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
            if path == "/api/study/graph":
                root = (qs.get("root") or [""])[0].strip() or None
                depth = _parse_int_query(qs, "depth", default=1, min_value=0, max_value=4)
                limit = _parse_int_query(qs, "limit", default=80, min_value=1, max_value=300)
                return self._send_json(
                    200, core.study_graph(root=root, depth=depth, limit=limit)
                )
            if path == "/api/study/search":
                limit = _parse_int_query(qs, "limit", default=50, min_value=1, max_value=300)
                return self._send_json(
                    200,
                    core.study_search(
                        q=(qs.get("q") or [""])[0],
                        kind=(qs.get("kind") or [""])[0],
                        proof_status=(qs.get("proof_status") or [""])[0],
                        source=(qs.get("source") or [""])[0],
                        run=(qs.get("run") or [""])[0],
                        limit=limit,
                    ),
                )
            if path == "/api/study/roots":
                limit = _parse_int_query(qs, "limit", default=50, min_value=1, max_value=300)
                return self._send_json(
                    200,
                    core.study_roots(
                        q=(qs.get("q") or [""])[0],
                        kind=(qs.get("kind") or [""])[0],
                        proof_status=(qs.get("proof_status") or [""])[0],
                        source=(qs.get("source") or [""])[0],
                        run=(qs.get("run") or [""])[0],
                        limit=limit,
                    ),
                )
            if path == "/api/study/neighborhood":
                root = (qs.get("root") or [""])[0].strip()
                if not root:
                    return self._send_400("missing root")
                depth = _parse_int_query(qs, "depth", default=1, min_value=0, max_value=4)
                limit = _parse_int_query(qs, "limit", default=80, min_value=1, max_value=300)
                direction = (qs.get("direction") or ["dependencies"])[0]
                if direction not in {"dependencies", "dependents", "both"}:
                    direction = "dependencies"
                return self._send_json(
                    200,
                    core.study_neighborhood(
                        root=root, direction=direction, depth=depth, limit=limit
                    ),
                )
            if path == "/api/study/attention":
                limit = _parse_int_query(qs, "limit", default=80, min_value=1, max_value=300)
                return self._send_json(200, core.study_attention(limit=limit))
            if path == "/api/study/discovery":
                return self._send_json(200, core.study_discovery())
            if path.startswith("/api/study/node/"):
                label = urllib.parse.unquote(path[len("/api/study/node/"):])
                if not label:
                    return self._send_400("missing label")
                detail = core.study_node(label)
                if detail is None:
                    return self._send_404()
                return self._send_json(200, detail)
            if path == "/api/reviews":
                return self._send_json(200, core.reviews())
            if path.startswith("/api/review/"):
                rest = urllib.parse.unquote(path[len("/api/review/"):])
                if rest.endswith("/graph"):
                    review_id = rest[: -len("/graph")]
                    if not review_id:
                        return self._send_400("missing review id")
                    graph = core.review_graph(review_id)
                    if graph is None:
                        return self._send_404()
                    return self._send_json(200, graph)
                if rest.endswith("/typos"):
                    review_id = rest[: -len("/typos")]
                    if not review_id:
                        return self._send_400("missing review id")
                    typos = core.review_typos(review_id)
                    if typos is None:
                        return self._send_404()
                    return self._send_json(200, typos)
                if rest.endswith("/repairs"):
                    review_id = rest[: -len("/repairs")]
                    if not review_id:
                        return self._send_400("missing review id")
                    repairs = core.review_repairs(review_id)
                    if repairs is None:
                        return self._send_404()
                    return self._send_json(200, repairs)
                review_id = rest
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
