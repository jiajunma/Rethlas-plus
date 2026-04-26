"""``runtime/state/coordinator.json`` heartbeat (ARCHITECTURE §6.4.2)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

COORDINATOR_JSON_SCHEMA: Final[str] = "rethlas-coordinator-v1"

# Status values.
STATUS_RUNNING: Final[str] = "running"
STATUS_IDLE: Final[str] = "idle"
STATUS_DEGRADED: Final[str] = "degraded"
STATUS_STOPPING: Final[str] = "stopping"

# §6.4.2 idle_reason_code values.
IDLE_NONE: Final[str] = ""
IDLE_ALL_DONE: Final[str] = "all_done"
IDLE_USER_BLOCKED: Final[str] = "user_blocked"
IDLE_GEN_DEP_BLOCKED: Final[str] = "generation_blocked_on_dependency"
IDLE_VER_DEP_BLOCKED: Final[str] = "verification_dep_blocked"
IDLE_IN_FLIGHT_ONLY: Final[str] = "in_flight_only"
IDLE_CORRUPTION: Final[str] = "corruption_or_drift"
IDLE_LIBRARIAN_STARTING: Final[str] = "librarian_starting"

_DETAIL_CAP: Final[int] = 512


def utc_now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass
class CoordinatorChild:
    pid: int
    status: str
    updated_at: str


@dataclass
class CoordinatorHeartbeat:
    pid: int
    started_at: str
    updated_at: str
    status: str = STATUS_RUNNING
    loop_seq: int = 0
    desired_pass_count: int = 3
    codex_silent_timeout_seconds: int = 1800
    active_generator_jobs: int = 0
    active_verifier_jobs: int = 0
    dispatchable_generator_count: int = 0
    dispatchable_verifier_count: int = 0
    unfinished_node_count: int = 0
    idle_reason_code: str = ""
    idle_reason_detail: str = ""
    user_blocked_count: int = 0
    generation_blocked_on_dependency_count: int = 0
    verification_dep_blocked_count: int = 0
    repair_spinning_count: int = 0
    recent_hash_mismatch_count: int = 0
    children: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Per-target "3x consecutive failure" entries (ARCHITECTURE §6.7
    # "Must prominently surface"). Each item has the keys:
    #   - kind:    "generator" | "verifier"
    #   - target:  node label
    #   - trigger: "crashed" | "timed_out" | "apply_failed"
    #   - reason:  apply_failed reason ("" for crashed/timed_out)
    #   - message: human-readable label per §6.7
    #   - count:   consecutive count (>= 3)
    attention_targets: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["schema"] = COORDINATOR_JSON_SCHEMA
        # Cap idle_reason_detail per §6.4.2.
        if len(d["idle_reason_detail"]) > _DETAIL_CAP:
            d["idle_reason_detail"] = d["idle_reason_detail"][: _DETAIL_CAP - 3] + "..."
        return d


def write_heartbeat(path: Path, hb: CoordinatorHeartbeat) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    body = json.dumps(hb.to_dict(), sort_keys=True, ensure_ascii=False) + "\n"
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)


def read_heartbeat(path: Path) -> dict[str, Any] | None:
    """Return the parsed heartbeat or ``None`` on any filesystem / parse error.

    Heartbeats are observability state; transient read failures should
    not crash the dashboard or other consumers. Catching the broader
    ``OSError`` covers permission errors, EIO, and broken symlinks too.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


__all__ = [
    "COORDINATOR_JSON_SCHEMA",
    "CoordinatorChild",
    "CoordinatorHeartbeat",
    "IDLE_ALL_DONE",
    "IDLE_CORRUPTION",
    "IDLE_GEN_DEP_BLOCKED",
    "IDLE_IN_FLIGHT_ONLY",
    "IDLE_LIBRARIAN_STARTING",
    "IDLE_NONE",
    "IDLE_USER_BLOCKED",
    "IDLE_VER_DEP_BLOCKED",
    "STATUS_DEGRADED",
    "STATUS_IDLE",
    "STATUS_RUNNING",
    "STATUS_STOPPING",
    "read_heartbeat",
    "utc_now_iso",
    "write_heartbeat",
]
