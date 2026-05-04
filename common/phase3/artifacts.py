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


def list_learner_batches(ws_root: Path) -> list[dict[str, Any]]:
    rows = read_json_files(learner_batches_dir(ws_root))
    rows.sort(key=lambda d: (d.get("ts", ""), d.get("event_id", "")), reverse=True)
    return rows


def list_reviews(ws_root: Path) -> list[dict[str, Any]]:
    rows = read_json_files(reviews_root(ws_root))
    rows = [r for r in rows if r.get("schema") == "rethlas-referee-artifact-v1"]
    rows.sort(key=lambda d: (d.get("ts", ""), d.get("event_id", "")), reverse=True)
    return rows


__all__ = [
    "event_artifact_hash",
    "learner_batches_dir",
    "list_learner_batches",
    "list_reviews",
    "phase3_root",
    "read_json_files",
    "reviews_root",
    "source_events_dir",
    "write_json_atomic",
    "write_learner_batch_artifact",
    "write_referee_report_artifact",
    "write_source_artifact",
]
