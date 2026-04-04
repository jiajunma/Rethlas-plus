#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROBLEM_FILE="${PROBLEM_FILE:-data/example.md}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs/example}"
MODEL="${MODEL:-gpt-5.4}"
REASONING_EFFORT="${REASONING_EFFORT:-xhigh}"

if [[ ! -f "$ROOT_DIR/$PROBLEM_FILE" ]]; then
  echo "Problem file not found: $ROOT_DIR/$PROBLEM_FILE" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"

problem_id="$(basename "$PROBLEM_FILE" .md)"
log_file="$LOG_DIR/${problem_id}.md"
prompt="Use AGENTS.md exactly to solve the math problem in ${PROBLEM_FILE}."

echo "Running ${PROBLEM_FILE} -> $log_file"

(
  cd "$ROOT_DIR"
  codex exec \
    -C "$ROOT_DIR" \
    -m "$MODEL" \
    --config "model_reasoning_effort=\"$REASONING_EFFORT\"" \
    --dangerously-bypass-approvals-and-sandbox \
    "$prompt"
) >"$log_file" 2>&1

echo "Finished ${PROBLEM_FILE} -> $log_file"
