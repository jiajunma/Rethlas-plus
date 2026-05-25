"""CodexBackend — subprocess wrapper around the ``codex`` CLI.

Mirrors the already-validated invocation pattern from
``~/mycodes/QED/verify/verify.py::run_codex`` (issue #4 reference):

    codex --search -m <model> -c model_reasoning_effort="<level>" \\
        exec --json --dangerously-bypass-approvals-and-sandbox \\
        -C <cwd> <prompt>

The CLI streams a sequence of JSON events to stdout. We collect them
and extract the ``item.completed → agent_message → text`` payload —
that is the human-readable response we hand back to the agent.

Non-zero exit codes are treated as a *warning* when a response was
nevertheless parsed (codex's ``--dangerously-bypass-approvals-and-sandbox``
path frequently exits non-zero even on a successful generation).
Empty response + non-zero exit is a hard :class:`BackendError`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .base import AgentResult, BackendError


_DEFAULT_MODEL = "gpt-5.5"
_DEFAULT_REASONING = "xhigh"


@dataclass
class CodexBackend:
    """``AgentBackend`` impl that shells out to the ``codex`` CLI.

    Configuration is intentionally minimal — per-role model and
    reasoning effort are picked up from ``model_per_role`` and
    ``reasoning_per_role`` (both fall back to the defaults). The
    backend stays stateless across calls; only the dicts are read.
    """

    name: str = "codex"
    cli_path: str = "codex"
    default_model: str = _DEFAULT_MODEL
    default_reasoning: str = _DEFAULT_REASONING
    model_per_role: dict[str, str] = field(default_factory=dict)
    reasoning_per_role: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None

    def _model_for(self, agent_role: str) -> str:
        return self.model_per_role.get(agent_role, self.default_model)

    def _reasoning_for(self, agent_role: str) -> str:
        return self.reasoning_per_role.get(agent_role, self.default_reasoning)

    def _build_argv(self, *, agent_role: str, prompt: str) -> list[str]:
        cwd = str(self.cwd) if self.cwd is not None else os.getcwd()
        return [
            self.cli_path,
            "--search",
            "-m", self._model_for(agent_role),
            "-c", f'model_reasoning_effort="{self._reasoning_for(agent_role)}"',
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C", cwd,
            prompt,
        ]

    def run(
        self,
        *,
        agent_role: str,
        prompt: str,
        context_files: list[Path] | None = None,
        timeout_seconds: int = 300,
        output_format: str = "json",  # noqa: ARG002 — codex doesn't take a format hint
    ) -> AgentResult:
        """Invoke codex and return an :class:`AgentResult`.

        ``context_files`` are listed in a small header prepended to the
        prompt so codex's ``--search`` flag can pull them into context
        on its own. The CLI has no first-class flag for file injection.
        ``output_format`` is accepted for Protocol parity but ignored —
        the *agent*'s prompt already requests the right shape.
        """

        full_prompt = _compose_prompt(prompt, context_files)
        argv = self._build_argv(agent_role=agent_role, prompt=full_prompt)

        started = time.monotonic()
        try:
            result = subprocess.run(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise BackendError(
                f"codex CLI not found at {self.cli_path!r}: {exc}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise BackendError(
                f"codex timed out after {timeout_seconds}s"
            ) from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        response = _parse_codex_stream(result.stdout)

        if result.returncode != 0:
            tail = (result.stderr or "").strip()
            suffix = (
                " (treating as warning — response parsed)"
                if response.strip()
                else ""
            )
            print(
                f"[codex] non-zero exit {result.returncode}{suffix}",
                file=sys.stderr,
            )
            if tail:
                print(f"[codex] stderr: {tail[:500]}", file=sys.stderr)

        if not response.strip():
            raise BackendError(
                f"codex returned empty response (exit={result.returncode}); "
                f"stderr tail: {(result.stderr or '').strip()[:200]}"
            )

        return AgentResult(
            stdout=response,
            stderr=result.stderr or "",
            exit_code=result.returncode,
            duration_ms=duration_ms,
            backend_name=self.name,
        )


def _compose_prompt(prompt: str, context_files: list[Path] | None) -> str:
    """Prepend a simple header listing context files (paths only)."""
    files = list(context_files or [])
    if not files:
        return prompt
    header_lines = ["[Context files — read with your search tool]"]
    header_lines.extend(f"- {p}" for p in files)
    header_lines.append("")  # blank line before user prompt
    return "\n".join(header_lines) + "\n" + prompt


def _parse_codex_stream(raw_stdout: str) -> str:
    """Extract the ``agent_message`` text from codex's JSON event stream.

    Falls back to the raw stdout if the stream is not valid JSONL — the
    agent layer can then decide whether the response is salvageable.
    """
    text = (raw_stdout or "").strip()
    if not text:
        return ""
    response = ""
    try:
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            if event.get("type") != "item.completed":
                continue
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                response = item.get("text", "") or response
    except (json.JSONDecodeError, ValueError):
        return text
    return response or text


def register_default() -> CodexBackend:
    """Self-register a default-config CodexBackend under name ``codex``.

    Called from :mod:`rethlas_kb.backends` import time so the CLI can
    look up ``"codex"`` without test setup. Tests that need a different
    config call ``register_backend(CodexBackend(...))`` explicitly,
    which overwrites this entry (registry semantics, issue #3).
    """
    from . import register_backend

    backend = CodexBackend()
    register_backend(backend)
    return backend


__all__ = ["CodexBackend", "register_default"]
