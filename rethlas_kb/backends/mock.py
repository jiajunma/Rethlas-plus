"""MockBackend — deterministic backend for tests.

Records the last call (so tests can assert on it) and returns whatever
canned response the test set up. Does not invoke any subprocess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .base import AgentResult


@dataclass
class MockBackend:
    """In-memory backend for unit testing agents.

    Implements the ``AgentBackend`` Protocol structurally (duck typing).
    """

    name: str = "mock"
    canned_response: str = "{}"
    canned_exit_code: int = 0
    canned_duration_ms: int = 10
    last_call: dict = field(default_factory=dict)
    call_count: int = 0

    def run(
        self,
        *,
        agent_role: str,
        prompt: str,
        context_files: list[Path] | None = None,
        timeout_seconds: int = 300,
        output_format: str = "json",
    ) -> AgentResult:
        self.last_call = {
            "agent_role": agent_role,
            "prompt": prompt,
            "context_files": list(context_files or []),
            "timeout_seconds": timeout_seconds,
            "output_format": output_format,
        }
        self.call_count += 1
        return AgentResult(
            stdout=self.canned_response,
            stderr="",
            exit_code=self.canned_exit_code,
            duration_ms=self.canned_duration_ms,
            backend_name=self.name,
        )


__all__ = ["MockBackend"]
