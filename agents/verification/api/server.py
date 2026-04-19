from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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


class VerifyRequest(BaseModel):
    statement: str = Field(..., min_length=1)
    proof: str = Field(..., min_length=1)


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


def run_codex_verification(run_id: str, statement: str, proof: str) -> Dict[str, Any]:
    results_dir = _results_dir(run_id)
    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = _log_path(run_id)
    proof_path = results_dir / "proof.md"
    proof_path.write_text(proof, encoding="utf-8")
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
        raise HTTPException(
            status_code=504,
            detail=f"codex exec timed out after {exc.timeout} seconds. See log at {log_path}",
        ) from exc

    verification_path = _verification_path(run_id)
    if completed.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=(
                f"codex exec failed with exit code {completed.returncode}. "
                f"See log at {log_path}"
            ),
        )

    if verification_path is None:
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
        raise HTTPException(
            status_code=500,
            detail=f"verification output at {verification_path} is not valid JSON",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=500,
            detail=f"verification output at {verification_path} must be a JSON object",
        )

    written_verification_path = _write_verification_payload(run_id, payload)
    _build_summary(run_id, statement, written_verification_path)

    return payload


app = FastAPI(title="Verification Agent API", version="0.1.0")


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/verify")
def verify(request: VerifyRequest) -> Dict[str, Any]:
    run_id = _allocate_run_id(request.statement)
    return run_codex_verification(
        run_id=run_id,
        statement=request.statement,
        proof=request.proof,
    )
