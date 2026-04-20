#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import atexit
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = REPO_ROOT / "results"
LOGS_ROOT = REPO_ROOT / "logs"
MEMORY_ROOT = REPO_ROOT / "memory"
SECTION_VERIFY = REPO_ROOT / "scripts" / "verify_sections.py"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_problem_id(raw: str) -> str:
    cleaned = re.sub(r"\s+", "_", raw.strip())
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned or "problem"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--problem-file", default="data/example.md")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--reasoning-effort", default="xhigh")
    parser.add_argument("--verify-url", default="http://127.0.0.1:8091/health")
    parser.add_argument("--backoff-seconds", type=int, default=30)
    parser.add_argument("--attempt-timeout-seconds", type=int, default=1800)
    parser.add_argument("--max-attempts", type=int, default=0, help="0 means unlimited")
    parser.add_argument("--extra-prompt", default="")
    return parser.parse_args()


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_lock(lock_path: Path) -> None:
    if lock_path.exists():
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            payload = {}
        old_pid = payload.get("pid")
        if isinstance(old_pid, int) and process_alive(old_pid):
            raise RuntimeError(f"run_with_recovery already active with pid={old_pid}")
        lock_path.unlink(missing_ok=True)

    payload = {
        "pid": os.getpid(),
        "created_at_utc": utc_now(),
    }
    write_json(lock_path, payload)

    def _cleanup() -> None:
        try:
            current = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            current = {}
        if current.get("pid") == os.getpid():
            lock_path.unlink(missing_ok=True)

    atexit.register(_cleanup)


def verification_service_ok(verify_url: str) -> bool:
    try:
        r = requests.get(verify_url, timeout=3)
        return r.ok
    except Exception:  # noqa: BLE001
        return False


def classify_failure(log_text: str) -> str:
    lowered = log_text.lower()
    if "429" in log_text or "too many requests" in lowered or "rate limit" in lowered:
        return "rate_limit"
    if "verification service is not reachable" in lowered:
        return "verifier_unreachable"
    if "timed out" in lowered:
        return "timeout"
    if "500 internal server error" in lowered or "server error" in lowered:
        return "server_error"
    return "unknown"


def pause_for_rate_limit(state: Dict[str, Any], state_path: Path, heartbeat_path: Path) -> int:
    state["status"] = "paused_rate_limit"
    state["updated_at_utc"] = utc_now()
    write_json(state_path, state)
    while True:
        heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")
        time.sleep(60)


def run_attempt(args: argparse.Namespace, problem_id: str, attempt_num: int, log_file: Path) -> int:
    prompt = (
        f"Use AGENTS.md exactly to solve the math problem in {args.problem_file}. "
        f"If memory/{problem_id}/ or results/{problem_id}/ already contain artifacts from an earlier run, "
        "resume from them instead of restarting from scratch. Preserve and build on prior memory, failed paths, and proof drafts."
    )
    if args.extra_prompt.strip():
        prompt += " " + args.extra_prompt.strip()

    cmd = [
        "codex",
        "exec",
        "-C",
        str(REPO_ROOT),
        "-m",
        args.model,
        "--config",
        f'model_reasoning_effort="{args.reasoning_effort}"',
        "--dangerously-bypass-approvals-and-sandbox",
        prompt,
    ]

    with log_file.open("w", encoding="utf-8") as handle:
        handle.write(f"started_at_utc: {utc_now()}\n")
        handle.write(f"attempt: {attempt_num}\n")
        handle.write(f"command: {' '.join(cmd)}\n\n")
        handle.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            stdout=handle,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )
        try:
            return proc.wait(timeout=args.attempt_timeout_seconds)
        except subprocess.TimeoutExpired:
            handle.write(f"\n[runner] attempt timed out after {args.attempt_timeout_seconds} seconds\n")
            handle.flush()
            os.killpg(proc.pid, signal.SIGINT)
            try:
                return proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                return proc.wait()


def main() -> int:
    args = parse_args()
    problem_path = (REPO_ROOT / args.problem_file).resolve()
    if not problem_path.exists():
        print(f"Problem file not found: {problem_path}", file=sys.stderr)
        return 1

    problem_rel = args.problem_file.removeprefix("data/").removesuffix(".md")
    problem_id = sanitize_problem_id(problem_path.stem)
    results_dir = RESULTS_ROOT / problem_id
    logs_dir = LOGS_ROOT / problem_rel
    state_path = results_dir / "run_state.json"
    lock_path = results_dir / "run_lock.json"
    heartbeat_path = results_dir / "heartbeat.txt"
    verified_blueprint = results_dir / "blueprint_verified.md"
    blueprint = results_dir / "blueprint.md"
    section_report = results_dir / "section_verification.json"
    stale_section_report = results_dir / "section_verification.stale_preclean.json"

    logs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    acquire_lock(lock_path)

    state = read_json(state_path)
    state.setdefault("problem_id", problem_id)
    state.setdefault("problem_file", args.problem_file)
    state.setdefault("created_at_utc", utc_now())
    state.setdefault("attempts", [])

    if verified_blueprint.exists():
        state["status"] = "verified"
        state["verified_blueprint"] = str(verified_blueprint)
        state["updated_at_utc"] = utc_now()
        write_json(state_path, state)
        heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")
        print(f"Verified blueprint already present: {verified_blueprint}")
        return 0

    attempt_num = len(state["attempts"]) + 1
    while args.max_attempts == 0 or attempt_num <= args.max_attempts:
        heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")
        state["status"] = "running"
        state["active_attempt"] = attempt_num
        state["runner_pid"] = os.getpid()
        state["updated_at_utc"] = utc_now()
        write_json(state_path, state)

        if not verification_service_ok(args.verify_url):
            state["status"] = "blocked_verifier"
            state["updated_at_utc"] = utc_now()
            state["last_failure_type"] = "verifier_unreachable"
            write_json(state_path, state)
            time.sleep(args.backoff_seconds)
            continue

        log_file = logs_dir / f"{problem_id}-attempt{attempt_num:02d}.md"
        state["current_log"] = str(log_file)
        state["attempt_started_at_utc"] = utc_now()
        if section_report.exists():
            try:
                section_report.replace(stale_section_report)
            except Exception:  # noqa: BLE001
                pass
        write_json(state_path, state)
        exit_code = run_attempt(args, problem_id, attempt_num, log_file)
        log_text = log_file.read_text(encoding="utf-8", errors="ignore")
        failure_type = classify_failure(log_text) if exit_code != 0 else ""

        state["attempts"].append(
            {
                "attempt": attempt_num,
                "started_log": str(log_file),
                "exit_code": exit_code,
                "failure_type": failure_type,
                "finished_at_utc": utc_now(),
            }
        )
        state["updated_at_utc"] = utc_now()

        if blueprint.exists():
            try:
                subprocess.run(
                    ["python3", str(SECTION_VERIFY), str(blueprint)],
                    cwd=REPO_ROOT,
                    check=False,
                )
            except Exception:  # noqa: BLE001
                pass

        if verified_blueprint.exists():
            state["status"] = "verified"
            state["verified_blueprint"] = str(verified_blueprint)
            write_json(state_path, state)
            heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")
            print(f"Verified blueprint written to {verified_blueprint}")
            return 0

        state["status"] = "retrying"
        state["last_failure_type"] = failure_type
        state["working_blueprint"] = str(blueprint) if blueprint.exists() else ""
        state["section_verification"] = str(section_report) if section_report.exists() else ""
        state.pop("attempt_started_at_utc", None)
        write_json(state_path, state)
        heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")

        if failure_type == "rate_limit":
            return pause_for_rate_limit(state, state_path, heartbeat_path)

        attempt_num += 1
        time.sleep(args.backoff_seconds)

    state["status"] = "exhausted"
    state["updated_at_utc"] = utc_now()
    write_json(state_path, state)
    heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
