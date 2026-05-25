"""AgentBackend Protocol — backend-agnostic infrastructure.

Each backend (codex / claude / opencode / mock) wraps an LLM CLI as a
subprocess. Agents (statement-verifier / proof-verifier / ...) take a
backend by injection and remain ignorant of which LLM is running.

See ``AGENTS.md`` "Backend Isolation Constraint" for the cross-backend
rule enforced at config-validation time (issue #13).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class AgentResult:
    """One backend invocation's outcome.

    ``stdout`` is the raw model output, possibly containing JSON-in-prose
    that an agent's decoder will extract. ``estimated_tokens`` is best-
    effort; not every backend reports it (default 0).
    """

    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    backend_name: str
    estimated_tokens: int = 0
    extra: dict[str, object] = field(default_factory=dict)


class BackendError(RuntimeError):
    """Raised when a backend cannot fulfill a run request (CLI missing,
    auth not configured, subprocess crashed before producing output)."""


@runtime_checkable
class AgentBackend(Protocol):
    """Subprocess wrapper around an LLM CLI.

    Implementations are stateless wrt the agent role — the caller passes
    ``agent_role`` so the backend may pick a per-role model / reasoning
    effort from its own config, but the backend does not retain per-role
    state across calls.
    """

    name: str

    def run(
        self,
        *,
        agent_role: str,
        prompt: str,
        context_files: list[Path] | None = None,
        timeout_seconds: int = 300,
        output_format: str = "json",
    ) -> AgentResult:
        """Invoke the backend with a composed prompt.

        Parameters
        ----------
        agent_role:
            Which Rethlas-KB agent is calling (e.g. ``"statement-verifier"``).
            Backends may use this to pick a per-role model / temperature
            / reasoning level.
        prompt:
            The complete user-facing prompt; already assembled by the
            agent's ``prompt.compose(...)``.
        context_files:
            Files the LLM should be able to read (e.g. mdblueprint
            ``context_pack`` bundle). Backends may pre-load these into
            the LLM's context window or pass them by path depending on
            the CLI.
        timeout_seconds:
            Hard wall-clock cap. Backends should raise
            :class:`BackendError` if exceeded.
        output_format:
            Hint to the backend about expected output shape
            (``"json"`` / ``"markdown"`` / ``"yaml"``). Backends are free
            to ignore if the CLI doesn't support the hint.
        """
        ...


__all__ = [
    "AgentBackend",
    "AgentResult",
    "BackendError",
]
