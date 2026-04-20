from __future__ import annotations

import hashlib
import json
import os
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
ACTIVE_RUN_ID: Optional[str] = None
VERIFY_QUEUE: Deque[Tuple[str, str, str]] = deque()
QUEUE_CONDITION = threading.Condition()
WORKER_STARTED = False


class VerifyRequest(BaseModel):
    statement: str = Field(..., min_length=1)
    proof: str = Field(..., min_length=1)


class VerifyAcceptedResponse(BaseModel):
    run_id: str
    status: str


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _statement_hash(statement: str) -> str:
    return hashlib.sha256(statement.encode("utf-8")).hexdigest()[:12]


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


def _write_verification_payload(run_id: str, payload: Dict[str, Any]) -> Path:
    results_dir = _results_dir(run_id)
    results_dir.mkdir(parents=True, exist_ok=True)
    verification_path = results_dir / VERIFICATION_FILENAMES[0]
    verification_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return verification_path


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


def build_prompt(run_id: str, statement: str, proof: str) -> str:
    return (
        f"Run_id: {run_id}. "
        f"Statement: {statement}. "
        f"Proof:\n{proof}\n\n"
        "Use AGENTS.md to verify the above proof for the statement."
    )


def build_codex_command(run_id: str, statement: str, proof: str) -> List[str]:
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
        build_prompt(run_id=run_id, statement=statement, proof=proof),
    ]


def _mark_running(run_id: str, statement: str) -> None:
    _write_state(
        run_id,
        {
            "run_id": run_id,
            "status": "running",
            "statement": statement,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )


def _mark_finished(run_id: str, statement: str, status: str, details: Optional[Dict[str, Any]] = None) -> None:
    payload = {
        "run_id": run_id,
        "status": status,
        "statement": statement,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if details:
        payload.update(details)
    _write_state(run_id, payload)


def run_codex_verification(run_id: str, statement: str, proof: str) -> Dict[str, Any]:
    results_dir = _results_dir(run_id)
    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = _log_path(run_id)
    proof_path = results_dir / "proof.md"
    proof_path.write_text(proof, encoding="utf-8")
    _mark_running(run_id, statement)
    cmd = build_codex_command(run_id=run_id, statement=statement, proof=proof)

    started_at = datetime.now(timezone.utc).isoformat()
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

    verification_path = _verification_path(run_id)
    if completed.returncode != 0:
        _mark_finished(run_id, statement, "failed", {"exit_code": completed.returncode})
        raise HTTPException(
            status_code=500,
            detail=(
                f"codex exec failed with exit code {completed.returncode}. "
                f"See log at {log_path}"
            ),
        )

    if verification_path is None:
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


def _run_verification_background(run_id: str, statement: str, proof: str) -> None:
    global ACTIVE_RUN_ID
    try:
        run_codex_verification(run_id=run_id, statement=statement, proof=proof)
    except Exception as exc:  # noqa: BLE001
        _mark_finished(run_id, statement, "failed", {"error": str(exc)})
    finally:
        with ACTIVE_LOCK:
            ACTIVE_RUN_ID = None


def _worker_loop() -> None:
    global ACTIVE_RUN_ID
    while True:
        with QUEUE_CONDITION:
            while not VERIFY_QUEUE:
                QUEUE_CONDITION.wait()
            run_id, statement, proof = VERIFY_QUEUE.popleft()
        with ACTIVE_LOCK:
            ACTIVE_RUN_ID = run_id
        _run_verification_background(run_id, statement, proof)


def _ensure_worker_started() -> None:
    global WORKER_STARTED
    with QUEUE_CONDITION:
        if WORKER_STARTED:
            return
        thread = threading.Thread(target=_worker_loop, daemon=True)
        thread.start()
        WORKER_STARTED = True


def _enqueue_verification(run_id: str, statement: str, proof: str) -> None:
    _ensure_worker_started()
    with QUEUE_CONDITION:
        VERIFY_QUEUE.append((run_id, statement, proof))
        QUEUE_CONDITION.notify()


app = FastAPI(title="Verification Agent API", version="0.1.0")


@app.get("/health")
def health() -> Dict[str, str]:
    with ACTIVE_LOCK:
        active_run_id = ACTIVE_RUN_ID
    with QUEUE_CONDITION:
        queue_depth = len(VERIFY_QUEUE)
    return {
        "status": "ok",
        "active_run_id": active_run_id or "",
        "queue_depth": str(queue_depth),
    }


@app.post("/verify")
def verify(request: VerifyRequest) -> Dict[str, Any]:
    run_id = _allocate_run_id(request.statement)
    _write_state(
        run_id,
        {
            "run_id": run_id,
            "status": "queued",
            "statement": request.statement,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    _enqueue_verification(run_id, request.statement, request.proof)
    return _wait_for_completion(run_id, CODEX_TIMEOUT_SECONDS)


@app.post("/verify_async", response_model=VerifyAcceptedResponse)
def verify_async(request: VerifyRequest) -> VerifyAcceptedResponse:
    run_id = _allocate_run_id(request.statement)
    _write_state(
        run_id,
        {
            "run_id": run_id,
            "status": "queued",
            "statement": request.statement,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    _enqueue_verification(run_id, request.statement, request.proof)
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
