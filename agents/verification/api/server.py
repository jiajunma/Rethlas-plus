from __future__ import annotations

import hashlib
import json
import re
import os
import sys
import threading
import shlex
import subprocess
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from common.codex_budget import acquire_slot, get_budget_status, note_rate_limit, release_slot  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]
WORK_DIR = REPO_ROOT.resolve()
RESULTS_ROOT = WORK_DIR / "results"

CODEX_BIN = os.getenv("CODEX_BIN", "codex")
CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-5.4")
CODEX_REASONING_EFFORT = os.getenv("CODEX_REASONING_EFFORT", "xhigh")
CODEX_TIMEOUT_SECONDS = int(os.getenv("CODEX_TIMEOUT_SECONDS", "0")) or None
VERIFICATION_FILENAMES = ("verification.json", "verificationt.json")
SUMMARY_SCRIPT = WORK_DIR / "scripts" / "build_verification_summary.py"
STATE_FILENAME = "state.json"

ACTIVE_LOCK = threading.Lock()
ACTIVE_RUN_IDS: set[str] = set()
VERIFY_QUEUE: Deque[Tuple[str, str, str, str]] = deque()
QUEUE_CONDITION = threading.Condition()
WORKER_STARTED = False
VERIFIER_WORKERS = int(os.getenv("VERIFY_WORKERS", "3"))


class VerifyRequest(BaseModel):
    statement: str = Field(..., min_length=1)
    proof: str = Field(..., min_length=1)
    context: str = ""


class VerifyAcceptedResponse(BaseModel):
    run_id: str
    status: str


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _statement_hash(statement: str) -> str:
    return hashlib.sha256(statement.encode("utf-8")).hexdigest()[:12]


def _verification_key(statement: str, proof: str, context: str = "") -> str:
    payload = statement + "\n\n---CONTEXT---\n\n" + context + "\n\n---PROOF---\n\n" + proof
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def generate_run_id(statement: str) -> str:
    return f"{_utc_timestamp()}_{_statement_hash(statement)}"


def _allocate_run_id(statement: str) -> str:
    base = generate_run_id(statement)
    run_id = base
    suffix = 1
    while (RESULTS_ROOT / run_id).exists():
        suffix += 1
        run_id = f"{base}_{suffix}"
    return run_id


def _results_dir(run_id: str) -> Path:
    return RESULTS_ROOT / run_id


def _log_path(run_id: str) -> Path:
    return _results_dir(run_id) / "log.md"


def _verification_path(run_id: str) -> Optional[Path]:
    for filename in VERIFICATION_FILENAMES:
        path = _results_dir(run_id) / filename
        if path.exists():
            return path
    return None


def _state_path(run_id: str) -> Path:
    return _results_dir(run_id) / STATE_FILENAME


def _read_state(run_id: str) -> Dict[str, Any]:
    path = _state_path(run_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state(run_id: str, payload: Dict[str, Any]) -> Path:
    path = _state_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _find_existing_run_for_verification_key(verification_key: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    candidates = sorted(
        [p for p in RESULTS_ROOT.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for run_dir in candidates:
        state = _read_state(run_dir.name)
        if state.get("verification_key") != verification_key:
            continue
        status = state.get("status")
        if status in {"queued", "running", "succeeded"}:
            return run_dir.name, state
    return None


def _write_verification_payload(run_id: str, payload: Dict[str, Any]) -> Path:
    results_dir = _results_dir(run_id)
    results_dir.mkdir(parents=True, exist_ok=True)
    verification_path = results_dir / VERIFICATION_FILENAMES[0]
    verification_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return verification_path


def _is_verification_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    verdict = payload.get("verdict")
    if not isinstance(verdict, str) or verdict not in {"correct", "wrong"}:
        return False
    if not isinstance(payload.get("repair_hints", ""), str):
        return False
    report = payload.get("verification_report")
    if not isinstance(report, dict):
        return False
    if not isinstance(report.get("summary", ""), str):
        return False
    if not isinstance(report.get("critical_errors", []), list):
        return False
    if not isinstance(report.get("gaps", []), list):
        return False
    return True


def _recover_verification_payload_from_log(log_text: str) -> Optional[Dict[str, Any]]:
    tail = log_text[-200000:]
    decoder = json.JSONDecoder()
    candidate_starts = [m.start() for m in re.finditer(r"\{", tail)]
    recovered: Optional[Dict[str, Any]] = None
    for start in candidate_starts:
        try:
            payload, _ = decoder.raw_decode(tail[start:])
        except json.JSONDecodeError:
            continue
        if _is_verification_payload(payload):
            recovered = payload
    return recovered


def _reconcile_stale_states() -> None:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    for run_dir in RESULTS_ROOT.iterdir():
        if not run_dir.is_dir():
            continue
        state_path = run_dir / STATE_FILENAME
        if not state_path.exists():
            continue
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        status = payload.get("status")
        if status in {"queued", "running"}:
            verification_path = _verification_path(run_dir.name)
            if verification_path is not None:
                try:
                    verification_payload = json.loads(verification_path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    verification_payload = {}
                payload["status"] = "succeeded"
                payload["verdict"] = verification_payload.get("verdict", payload.get("verdict", ""))
                payload["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
                state_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                continue
            payload["status"] = "interrupted"
            payload["error"] = "Verifier restarted before this run completed."
            payload["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            state_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _build_summary(run_id: str, statement: str, verification_path: Path) -> None:
    proof_path = _results_dir(run_id) / "proof.md"
    if not proof_path.exists():
        return
    subprocess.run(
        [
            "python3",
            str(SUMMARY_SCRIPT),
            "--results-dir",
            str(_results_dir(run_id)),
            "--statement",
            statement,
            "--proof-file",
            str(proof_path),
            "--verification-file",
            str(verification_path),
        ],
        cwd=WORK_DIR,
        check=False,
    )


def _current_state_status(run_id: str) -> Optional[str]:
    return _read_state(run_id).get("status")


def _wait_for_completion(run_id: str, timeout_seconds: Optional[int]) -> Dict[str, Any]:
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    while True:
        status = _current_state_status(run_id)
        if status == "succeeded":
            verification_path = _verification_path(run_id)
            if verification_path is None:
                raise HTTPException(
                    status_code=500,
                    detail=f"verification succeeded but no result was found for run_id={run_id}",
                )
            return json.loads(verification_path.read_text(encoding="utf-8"))
        if status == "failed":
            state = _read_state(run_id)
            raise HTTPException(
                status_code=500,
                detail=state.get("error") or f"verification failed for run_id={run_id}",
            )
        if status == "timed_out":
            state = _read_state(run_id)
            raise HTTPException(
                status_code=504,
                detail=state.get("error") or f"verification timed out for run_id={run_id}",
            )
        if deadline is not None and time.monotonic() >= deadline:
            raise HTTPException(
                status_code=504,
                detail=f"timed out waiting for run_id={run_id}",
            )
        time.sleep(1)


def build_prompt(run_id: str, statement: str, proof: str, context: str = "") -> str:
    prompt = (
        f"Run_id: {run_id}. "
        f"Statement: {statement}. "
    )
    if context.strip():
        prompt += (
            "Dependency_context:\n"
            f"{context}\n\n"
        )
    prompt += (
        "Proof_of_current_statement_only:\n"
        f"{proof}\n\n"
        "Use AGENTS.md to verify only the proof of the current statement. "
        "Treat Dependency_context as previously established theorem statements available for use. "
        "Do not re-verify proofs of dependencies. "
        "Instead, check that every dependency the current proof needs is actually present in Dependency_context, "
        "and that the proof uses only what those dependent statements literally assert. "
        "You must still write results/{run_id}/verification.json if possible, "
        "but in addition your final response must end with the raw verification JSON object itself, "
        "with no markdown fence and no extra prose after that JSON."
    )
    return prompt


def build_codex_command(run_id: str, statement: str, proof: str, context: str = "") -> List[str]:
    return [
        CODEX_BIN,
        "exec",
        "-C",
        str(WORK_DIR),
        "-m",
        CODEX_MODEL,
        "--config",
        f"model_reasoning_effort={CODEX_REASONING_EFFORT}",
        "--dangerously-bypass-approvals-and-sandbox",
        build_prompt(run_id=run_id, statement=statement, proof=proof, context=context),
    ]


def _mark_running(run_id: str, statement: str, verification_key: str) -> None:
    _write_state(
        run_id,
        {
            "run_id": run_id,
            "status": "running",
            "statement": statement,
            "verification_key": verification_key,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )


def _mark_finished(run_id: str, statement: str, status: str, details: Optional[Dict[str, Any]] = None) -> None:
    current = _read_state(run_id)
    payload = {
        "run_id": run_id,
        "status": status,
        "statement": statement,
        "verification_key": current.get("verification_key", ""),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if details:
        payload.update(details)
    _write_state(run_id, payload)


def run_codex_verification(run_id: str, statement: str, proof: str, context: str = "") -> Dict[str, Any]:
    results_dir = _results_dir(run_id)
    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = _log_path(run_id)
    proof_path = results_dir / "proof.md"
    proof_path.write_text(proof, encoding="utf-8")
    verification_key = _verification_key(statement, proof, context)
    _mark_running(run_id, statement, verification_key)
    cmd = build_codex_command(run_id=run_id, statement=statement, proof=proof, context=context)

    started_at = datetime.now(timezone.utc).isoformat()
    slot_path = acquire_slot(f"verifier:{run_id}")
    try:
        with log_path.open("w", encoding="utf-8") as log_handle:
            log_handle.write(f"started_at_utc: {started_at}\n")
            log_handle.write(f"command: {shlex.join(cmd)}\n\n")
            log_handle.flush()

            completed = subprocess.run(
                cmd,
                cwd=WORK_DIR,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=CODEX_TIMEOUT_SECONDS,
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        _mark_finished(run_id, statement, "timed_out", {"error": str(exc)})
        raise HTTPException(
            status_code=504,
            detail=f"codex exec timed out after {exc.timeout} seconds. See log at {log_path}",
        ) from exc
    finally:
        release_slot(slot_path)

    verification_path = _verification_path(run_id)
    log_text = log_path.read_text(encoding="utf-8", errors="ignore")
    recovered_payload = _recover_verification_payload_from_log(log_text)
    if completed.returncode != 0:
        if recovered_payload is not None:
            written_verification_path = _write_verification_payload(run_id, recovered_payload)
            _build_summary(run_id, statement, written_verification_path)
            _mark_finished(run_id, statement, "succeeded", {"verdict": recovered_payload.get("verdict")})
            return recovered_payload
        if "429" in log_text or "rate limit" in log_text.lower() or "too many requests" in log_text.lower():
            note_rate_limit()
        _mark_finished(run_id, statement, "failed", {"exit_code": completed.returncode})
        raise HTTPException(
            status_code=500,
            detail=(
                f"codex exec failed with exit code {completed.returncode}. "
                f"See log at {log_path}"
            ),
        )

    if verification_path is None:
        if recovered_payload is not None:
            written_verification_path = _write_verification_payload(run_id, recovered_payload)
            _build_summary(run_id, statement, written_verification_path)
            _mark_finished(run_id, statement, "succeeded", {"verdict": recovered_payload.get("verdict")})
            return recovered_payload
        _mark_finished(run_id, statement, "failed", {"error": "verification output missing"})
        expected_primary = _results_dir(run_id) / VERIFICATION_FILENAMES[0]
        raise HTTPException(
            status_code=500,
            detail=(
                f"verification output was not found at {expected_primary}. "
                f"See log at {log_path}"
            ),
        )

    try:
        payload = json.loads(verification_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _mark_finished(run_id, statement, "failed", {"error": "verification output not valid JSON"})
        raise HTTPException(
            status_code=500,
            detail=f"verification output at {verification_path} is not valid JSON",
        ) from exc

    if not isinstance(payload, dict):
        _mark_finished(run_id, statement, "failed", {"error": "verification output not JSON object"})
        raise HTTPException(
            status_code=500,
            detail=f"verification output at {verification_path} must be a JSON object",
        )

    written_verification_path = _write_verification_payload(run_id, payload)
    _build_summary(run_id, statement, written_verification_path)
    _mark_finished(run_id, statement, "succeeded", {"verdict": payload.get("verdict")})

    return payload


def _run_verification_background(run_id: str, statement: str, proof: str, context: str = "") -> None:
    try:
        run_codex_verification(run_id=run_id, statement=statement, proof=proof, context=context)
    except Exception as exc:  # noqa: BLE001
        _mark_finished(run_id, statement, "failed", {"error": str(exc)})
    finally:
        with ACTIVE_LOCK:
            ACTIVE_RUN_IDS.discard(run_id)


def _worker_loop() -> None:
    while True:
        with QUEUE_CONDITION:
            while not VERIFY_QUEUE:
                QUEUE_CONDITION.wait()
            run_id, statement, proof, context = VERIFY_QUEUE.popleft()
        with ACTIVE_LOCK:
            ACTIVE_RUN_IDS.add(run_id)
        _run_verification_background(run_id, statement, proof, context)


def _ensure_worker_started() -> None:
    global WORKER_STARTED
    with QUEUE_CONDITION:
        if WORKER_STARTED:
            return
        for _ in range(max(1, VERIFIER_WORKERS)):
            thread = threading.Thread(target=_worker_loop, daemon=True)
            thread.start()
        WORKER_STARTED = True


def _enqueue_verification(run_id: str, statement: str, proof: str, context: str = "") -> None:
    _ensure_worker_started()
    with QUEUE_CONDITION:
        VERIFY_QUEUE.append((run_id, statement, proof, context))
        QUEUE_CONDITION.notify()


app = FastAPI(title="Verification Agent API", version="0.1.0")


@app.on_event("startup")
def startup_reconcile_states() -> None:
    _reconcile_stale_states()


@app.get("/health")
def health() -> Dict[str, str]:
    with ACTIVE_LOCK:
        active_run_ids = sorted(ACTIVE_RUN_IDS)
    with QUEUE_CONDITION:
        queue_depth = len(VERIFY_QUEUE)
    budget = get_budget_status()
    return {
        "status": "ok",
        "active_run_id": active_run_ids[0] if active_run_ids else "",
        "active_run_ids": ",".join(active_run_ids),
        "queue_depth": str(queue_depth),
        "codex_current_limit": str(budget.get("current_limit", "")),
        "codex_active_slots": str(budget.get("active_slots", "")),
    }


@app.post("/verify")
def verify(request: VerifyRequest) -> Dict[str, Any]:
    verification_key = _verification_key(request.statement, request.proof, request.context)
    existing = _find_existing_run_for_verification_key(verification_key)
    if existing is not None:
        run_id, _state = existing
        return _wait_for_completion(run_id, CODEX_TIMEOUT_SECONDS)
    run_id = _allocate_run_id(request.statement)
    _write_state(
        run_id,
        {
            "run_id": run_id,
            "status": "queued",
            "statement": request.statement,
            "verification_key": verification_key,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    _enqueue_verification(run_id, request.statement, request.proof, request.context)
    return _wait_for_completion(run_id, CODEX_TIMEOUT_SECONDS)


@app.post("/verify_async", response_model=VerifyAcceptedResponse)
def verify_async(request: VerifyRequest) -> VerifyAcceptedResponse:
    verification_key = _verification_key(request.statement, request.proof, request.context)
    existing = _find_existing_run_for_verification_key(verification_key)
    if existing is not None:
        run_id, state = existing
        return VerifyAcceptedResponse(run_id=run_id, status=str(state.get("status") or "queued"))
    run_id = _allocate_run_id(request.statement)
    _write_state(
        run_id,
        {
            "run_id": run_id,
            "status": "queued",
            "statement": request.statement,
            "verification_key": verification_key,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    _enqueue_verification(run_id, request.statement, request.proof, request.context)
    return VerifyAcceptedResponse(run_id=run_id, status="queued")


@app.get("/verify_status/{run_id}")
def verify_status(run_id: str) -> Dict[str, Any]:
    state = _read_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"No such run_id: {run_id}")
    return state


@app.get("/verify_result/{run_id}")
def verify_result(run_id: str) -> Dict[str, Any]:
    verification_path = _verification_path(run_id)
    if verification_path is None:
        raise HTTPException(status_code=404, detail=f"No verification result for run_id: {run_id}")
    return json.loads(verification_path.read_text(encoding="utf-8"))
