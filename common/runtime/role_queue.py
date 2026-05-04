"""Tiny file queue for Phase 3 learner/referee scheduler lanes."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


QUEUE_SCHEMA = "rethlas-role-queue-v1"


@dataclass
class RoleQueueItem:
    queue_id: str
    kind: str
    mode: str
    target: str
    context_hash: str
    input: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["schema"] = QUEUE_SCHEMA
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoleQueueItem":
        if data.get("schema") != QUEUE_SCHEMA:
            raise ValueError(f"not a {QUEUE_SCHEMA} item")
        raw = {k: v for k, v in data.items() if k != "schema"}
        return cls(**raw)


def role_queue_dir(workspace_root: Path | str, kind: str) -> Path:
    return Path(workspace_root) / "runtime" / "queues" / kind


def _utc_now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _iso_ms_now() -> str:
    now = datetime.now(tz=timezone.utc)
    return now.strftime("%Y%m%dT%H%M%S.") + f"{now.microsecond // 1000:03d}"


def hash_input(data: dict[str, Any]) -> str:
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def enqueue_role_item(
    workspace_root: Path | str,
    *,
    kind: str,
    mode: str,
    target: str,
    input_packet: dict[str, Any],
) -> RoleQueueItem:
    iso_ms = _iso_ms_now()
    queue_id = f"{kind}-{iso_ms}-{secrets.token_hex(8)}"
    item = RoleQueueItem(
        queue_id=queue_id,
        kind=kind,
        mode=mode,
        target=target,
        context_hash=hash_input(input_packet),
        input=dict(input_packet),
        created_at=_utc_now_iso(),
    )
    d = role_queue_dir(workspace_root, kind)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{queue_id}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(item.to_dict(), sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return item


def list_role_queue(workspace_root: Path | str, kind: str) -> list[tuple[Path, RoleQueueItem]]:
    out: list[tuple[Path, RoleQueueItem]] = []
    d = role_queue_dir(workspace_root, kind)
    if not d.is_dir():
        return out
    for path in sorted(d.glob("*.json")):
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                continue
            out.append((path, RoleQueueItem.from_dict(parsed)))
        except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError):
            continue
    return out


def delete_role_queue_item(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


__all__ = [
    "QUEUE_SCHEMA",
    "RoleQueueItem",
    "delete_role_queue_item",
    "enqueue_role_item",
    "hash_input",
    "list_role_queue",
    "role_queue_dir",
]
