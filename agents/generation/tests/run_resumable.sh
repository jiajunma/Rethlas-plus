#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROBLEM_FILE="${PROBLEM_FILE:-data/example.md}"
MODEL="${MODEL:-gpt-5.4}"
REASONING_EFFORT="${REASONING_EFFORT:-xhigh}"
VERIFY_URL="${VERIFY_URL:-http://127.0.0.1:8091/health}"
BACKOFF_SECONDS="${BACKOFF_SECONDS:-30}"
ATTEMPT_TIMEOUT_SECONDS="${ATTEMPT_TIMEOUT_SECONDS:-1800}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-0}"
EXTRA_PROMPT="${EXTRA_PROMPT:-}"

cd "$ROOT_DIR"
python3 scripts/run_with_recovery.py \
  --problem-file "$PROBLEM_FILE" \
  --model "$MODEL" \
  --reasoning-effort "$REASONING_EFFORT" \
  --verify-url "$VERIFY_URL" \
  --backoff-seconds "$BACKOFF_SECONDS" \
  --attempt-timeout-seconds "$ATTEMPT_TIMEOUT_SECONDS" \
  --max-attempts "$MAX_ATTEMPTS" \
  --extra-prompt "$EXTRA_PROMPT"
