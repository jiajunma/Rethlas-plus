from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional


RETHLAS_ROOT = Path(__file__).resolve().parents[2]
COORD_DIR = RETHLAS_ROOT / "coordination"
BUDGET_FILE = COORD_DIR / "codex_budget.json"
SLOTS_DIR = COORD_DIR / "codex_slots"
LOCK_FILE = COORD_DIR / "codex_budget.lock"

DEFAULT_LIMIT = int(os.getenv("CODEX_DEFAULT_CONCURRENCY", "3"))
RATE_LIMIT_LIMIT = int(os.getenv("CODEX_RATE_LIMIT_CONCURRENCY", "1"))
RATE_LIMIT_COOLDOWN_SECONDS = int(os.getenv("CODEX_RATE_LIMIT_COOLDOWN_SECONDS", "900"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dirs() -> None:
    COORD_DIR.mkdir(parents=True, exist_ok=True)
    SLOTS_DIR.mkdir(parents=True, exist_ok=True)


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@contextmanager
def _file_lock(timeout_seconds: int = 60) -> Iterator[None]:
    _ensure_dirs()
    start = time.monotonic()
    while True:
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() - start > timeout_seconds:
                raise TimeoutError("Timed out acquiring codex budget lock")
            time.sleep(0.2)
    try:
        yield
    finally:
        try:
            LOCK_FILE.unlink()
        except FileNotFoundError:
            pass


def _read_budget() -> Dict[str, object]:
    _ensure_dirs()
    if not BUDGET_FILE.exists():
        payload = {
            "default_limit": DEFAULT_LIMIT,
            "current_limit": DEFAULT_LIMIT,
            "last_rate_limit_at": "",
            "cooldown_until_utc": "",
            "updated_at_utc": utc_now(),
        }
        BUDGET_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return payload
    return json.loads(BUDGET_FILE.read_text(encoding="utf-8"))


def _write_budget(payload: Dict[str, object]) -> None:
    _ensure_dirs()
    payload["updated_at_utc"] = utc_now()
    BUDGET_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _cleanup_stale_slots() -> None:
    _ensure_dirs()
    for slot_path in SLOTS_DIR.glob("*.json"):
        try:
            payload = json.loads(slot_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            slot_path.unlink(missing_ok=True)
            continue
        pid = payload.get("pid")
        if not isinstance(pid, int) or not _process_alive(pid):
            slot_path.unlink(missing_ok=True)


def _active_slots() -> List[Path]:
    _cleanup_stale_slots()
    return sorted(SLOTS_DIR.glob("*.json"))


def get_budget_status() -> Dict[str, object]:
    with _file_lock():
        budget = _read_budget()
        budget = maybe_restore_default_limit_locked(budget)
        slots = _active_slots()
        return {
            **budget,
            "active_slots": len(slots),
            "slot_files": [str(p) for p in slots],
        }


def note_rate_limit() -> Dict[str, object]:
    with _file_lock():
        budget = _read_budget()
        budget["current_limit"] = RATE_LIMIT_LIMIT
        budget["last_rate_limit_at"] = utc_now()
        budget["cooldown_until_utc"] = datetime.fromtimestamp(
            time.time() + RATE_LIMIT_COOLDOWN_SECONDS, tz=timezone.utc
        ).isoformat()
        _write_budget(budget)
        return budget


def maybe_restore_default_limit_locked(budget: Dict[str, object]) -> Dict[str, object]:
    cooldown_until = str(budget.get("cooldown_until_utc") or "")
    if not cooldown_until:
        return budget
    try:
        cooldown_dt = datetime.fromisoformat(cooldown_until.replace("Z", "+00:00"))
    except ValueError:
        budget["cooldown_until_utc"] = ""
        _write_budget(budget)
        return budget
    if datetime.now(timezone.utc) >= cooldown_dt:
        budget["current_limit"] = int(budget.get("default_limit", DEFAULT_LIMIT))
        budget["cooldown_until_utc"] = ""
        _write_budget(budget)
    return budget


def maybe_restore_default_limit() -> Dict[str, object]:
    with _file_lock():
        budget = _read_budget()
        return maybe_restore_default_limit_locked(budget)


def acquire_slot(owner: str, timeout_seconds: int = 600) -> Path:
    start = time.monotonic()
    while True:
        with _file_lock():
            budget = _read_budget()
            slots = _active_slots()
            current_limit = int(budget.get("current_limit", DEFAULT_LIMIT))
            if len(slots) < current_limit:
                slot_id = f"{uuid.uuid4().hex}.json"
                slot_path = SLOTS_DIR / slot_id
                slot_payload = {
                    "owner": owner,
                    "pid": os.getpid(),
                    "created_at_utc": utc_now(),
                }
                slot_path.write_text(json.dumps(slot_payload, indent=2) + "\n", encoding="utf-8")
                return slot_path
        if time.monotonic() - start > timeout_seconds:
            raise TimeoutError(f"Timed out waiting for codex budget slot for {owner}")
        time.sleep(1)


def release_slot(slot_path: Optional[Path]) -> None:
    if slot_path is None:
        return
    try:
        slot_path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
