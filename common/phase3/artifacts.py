"""Filesystem read/write helpers for Phase 3 artifacts.

Truth events stay under ``events/``. These files are read-model artifacts that
the dashboard and operators can inspect without letting learner/referee scratch
work masquerade as verified KB nodes.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def phase3_root(ws_root: Path) -> Path:
    return ws_root / "knowledge_base" / "phase3"


def learner_batches_dir(ws_root: Path) -> Path:
    return phase3_root(ws_root) / "learner_batches"


def source_events_dir(ws_root: Path) -> Path:
    return phase3_root(ws_root) / "sources"


def reviews_root(ws_root: Path) -> Path:
    return ws_root / "reviews"


def review_repairs_root(ws_root: Path) -> Path:
    return reviews_root(ws_root) / "_repairs"


def bridge_repairs_dir(ws_root: Path) -> Path:
    return phase3_root(ws_root) / "bridge_repairs"


def event_artifact_hash(data: dict[str, Any]) -> str:
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def write_json_atomic(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


def read_json_files(directory: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not directory.is_dir():
        return out
    for entry in sorted(directory.glob("*.json")):
        try:
            parsed = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            parsed.setdefault("_path", str(entry))
            out.append(parsed)
    return out


def write_learner_batch_artifact(
    ws_root: Path, *, event_id: str, body: dict[str, Any]
) -> Path:
    payload = body.get("payload", {})
    data = {
        "schema": "rethlas-learner-batch-artifact-v1",
        "event_id": event_id,
        "event_type": body.get("type", ""),
        "actor": body.get("actor", ""),
        "ts": body.get("ts", ""),
        "source_id": payload.get("source_id", ""),
        "learner_run": payload.get("learner_run", ""),
        "context_hash": payload.get("context_hash", ""),
        "status": "proposed",
        "payload": payload,
        "hash": event_artifact_hash(payload if isinstance(payload, dict) else {}),
    }
    return write_json_atomic(learner_batches_dir(ws_root) / f"{event_id}.json", data)


def write_source_artifact(
    ws_root: Path, *, event_id: str, body: dict[str, Any]
) -> Path:
    payload = body.get("payload", {})
    data = {
        "schema": "rethlas-source-event-artifact-v1",
        "event_id": event_id,
        "event_type": body.get("type", ""),
        "actor": body.get("actor", ""),
        "ts": body.get("ts", ""),
        "source_id": payload.get("source_id", ""),
        "payload": payload,
        "hash": event_artifact_hash(payload if isinstance(payload, dict) else {}),
    }
    return write_json_atomic(source_events_dir(ws_root) / f"{event_id}.json", data)


def write_referee_report_artifact(
    ws_root: Path, *, event_id: str, body: dict[str, Any]
) -> Path:
    payload = body.get("payload", {})
    review_id = payload.get("review_id") if isinstance(payload, dict) else None
    if not isinstance(review_id, str) or not review_id:
        review_id = event_id
    event_type = body.get("type", "")
    report_dir = reviews_root(ws_root) / review_id
    filename = "review_completed.json" if event_type == "referee.review_completed" else "citation_checked.json"
    data = {
        "schema": "rethlas-referee-artifact-v1",
        "event_id": event_id,
        "event_type": event_type,
        "actor": body.get("actor", ""),
        "ts": body.get("ts", ""),
        "target": body.get("target", ""),
        "payload": payload,
        "hash": event_artifact_hash(payload if isinstance(payload, dict) else {}),
    }
    path = write_json_atomic(report_dir / filename, data)
    # Also keep one flat index file so dashboard scans do not recurse deeply.
    write_json_atomic(reviews_root(ws_root) / f"{event_id}.json", data | {"artifact_path": str(path)})
    return path


def write_review_node_repair_artifact(ws_root: Path, *, body: dict[str, Any]) -> Path:
    repair_id = body.get("repair_id")
    if not isinstance(repair_id, str) or not repair_id:
        raise ValueError("review repair artifact requires repair_id")
    data = {
        "schema": "rethlas-review-node-repair-artifact-v1",
        **body,
        "hash": event_artifact_hash(body),
    }
    return write_json_atomic(review_repairs_root(ws_root) / f"{repair_id}.json", data)


def write_bridge_repair_artifact(ws_root: Path, *, body: dict[str, Any]) -> Path:
    repair_id = body.get("repair_id")
    if not isinstance(repair_id, str) or not repair_id:
        raise ValueError("bridge repair artifact requires repair_id")
    data = {
        "schema": "rethlas-bridge-repair-artifact-v1",
        **body,
        "hash": event_artifact_hash(body),
    }
    return write_json_atomic(bridge_repairs_dir(ws_root) / f"{repair_id}.json", data)


def list_learner_batches(ws_root: Path) -> list[dict[str, Any]]:
    rows = read_json_files(learner_batches_dir(ws_root))
    rows.sort(key=lambda d: (d.get("ts", ""), d.get("event_id", "")), reverse=True)
    return rows


def list_reviews(ws_root: Path) -> list[dict[str, Any]]:
    rows = read_json_files(reviews_root(ws_root))
    rows = [r for r in rows if r.get("schema") == "rethlas-referee-artifact-v1"]
    rows.sort(key=lambda d: (d.get("ts", ""), d.get("event_id", "")), reverse=True)
    return rows


def list_review_node_repairs(ws_root: Path) -> list[dict[str, Any]]:
    rows = read_json_files(review_repairs_root(ws_root))
    rows = [
        r for r in rows
        if r.get("schema") == "rethlas-review-node-repair-artifact-v1"
    ]
    rows.sort(key=lambda d: (d.get("ts", ""), d.get("repair_id", "")), reverse=True)
    return rows


def list_bridge_repairs(ws_root: Path) -> list[dict[str, Any]]:
    rows = read_json_files(bridge_repairs_dir(ws_root))
    rows = [
        r for r in rows
        if r.get("schema") == "rethlas-bridge-repair-artifact-v1"
    ]
    rows.sort(key=lambda d: (d.get("ts", ""), d.get("repair_id", "")), reverse=True)
    return rows


def latest_review_node_repairs_by_label(
    ws_root: Path, *, review_id: str | None = None
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for artifact in reversed(list_review_node_repairs(ws_root)):
        if review_id and artifact.get("review_id") != review_id:
            continue
        for repair in artifact.get("node_repairs", []) or []:
            if not isinstance(repair, dict):
                continue
            label = repair.get("label")
            if isinstance(label, str) and label:
                out[label] = {
                    **repair,
                    "repair_id": artifact.get("repair_id", ""),
                    "repair_ts": artifact.get("ts", ""),
                    "repair_kind": artifact.get("repair_kind", ""),
                    "repair_version": artifact.get("version", None),
                    "repair_active": artifact.get("active", True),
                    "repair_supersedes": artifact.get("supersedes", []) or [],
                    "repair_summary": artifact.get("summary", {}) or {},
                }
    return out


__all__ = [
    "bridge_repairs_dir",
    "event_artifact_hash",
    "latest_review_node_repairs_by_label",
    "learner_batches_dir",
    "list_bridge_repairs",
    "list_learner_batches",
    "list_review_node_repairs",
    "list_reviews",
    "phase3_root",
    "read_json_files",
    "review_repairs_root",
    "reviews_root",
    "source_events_dir",
    "write_json_atomic",
    "write_bridge_repair_artifact",
    "write_learner_batch_artifact",
    "write_referee_report_artifact",
    "write_review_node_repair_artifact",
    "write_source_artifact",
]
