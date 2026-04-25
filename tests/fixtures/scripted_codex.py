"""Composer for ``FAKE_CODEX_SCRIPT`` env vars (PHASE1 M5).

Provides ergonomic helpers for the common test scenarios so individual
tests don't repeat verbose JSON schemas. Each helper returns a JSON
string that can be passed directly to ``env={"FAKE_CODEX_SCRIPT": ...}``.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Iterable

# Path to the fake codex executable. Tests use this as
# ``argv[0]`` when calling :func:`common.runtime.codex_runner.run_codex`.
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"


def fake_codex_argv(*extra_args: str) -> list[str]:
    """Return an argv that invokes the fake codex script via the active interpreter."""
    return [sys.executable, str(FAKE_CODEX), *extra_args]


def _serialize(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def quick_success(stdout: str = "") -> str:
    """Codex returns immediately with ``stdout`` and exit 0."""
    return _serialize(
        {
            "stdout_lines": [{"text": stdout, "delay_s": 0.0}] if stdout else [],
            "exit_code": 0,
        }
    )


def silent_for(seconds: float, *, exit_code: int = 0, stdout: str = "") -> str:
    """Sleep ``seconds`` (then emit ``stdout``); used for timeout tests."""
    return _serialize(
        {
            "silent_seconds": float(seconds),
            "stdout_lines": [{"text": stdout, "delay_s": 0.0}] if stdout else [],
            "exit_code": exit_code,
        }
    )


def streaming_lines(
    lines: Iterable[str],
    *,
    delay_s: float = 0.0,
    exit_code: int = 0,
) -> str:
    """Emit each line on stdout with ``delay_s`` between them.

    Useful for log-mtime watchdog tests: a steady trickle of output keeps
    the timeout from firing.
    """
    return _serialize(
        {
            "stdout_lines": [{"text": l, "delay_s": delay_s} for l in lines],
            "exit_code": exit_code,
        }
    )


def stderr_warning(stderr: str, *, stdout: str = "", exit_code: int = 0) -> str:
    """Emit ``stderr`` + ``stdout``; verifies merged-log behaviour (§7.4)."""
    return _serialize(
        {
            "stderr_lines": [{"text": stderr, "delay_s": 0.0}],
            "stdout_lines": [{"text": stdout, "delay_s": 0.0}] if stdout else [],
            "exit_code": exit_code,
        }
    )


def crash(*, exit_code: int = 1, stderr: str = "boom") -> str:
    return _serialize(
        {
            "stderr_lines": [{"text": stderr, "delay_s": 0.0}],
            "exit_code": exit_code,
        }
    )


def malformed(stdout_prefix: str = "<node>broken") -> str:
    return _serialize(
        {
            "stdout_lines": [{"text": stdout_prefix, "delay_s": 0.0}],
            "malformed": True,
            "exit_code": 0,
        }
    )


__all__ = [
    "FAKE_CODEX",
    "crash",
    "fake_codex_argv",
    "malformed",
    "quick_success",
    "silent_for",
    "streaming_lines",
    "stderr_warning",
]
