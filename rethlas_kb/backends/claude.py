"""ClaudeBackend — subprocess wrapper around the ``claude`` CLI.

Mirrors the already-validated invocation pattern from
``~/mycodes/QED/verify/verify.py::run_claude`` (issue #5 reference):

    claude -p --output-format json --dangerously-skip-permissions \\
        --model <model> <prompt>

Three auth providers are supported, matching the QED config shape:

- ``subscription`` — relies on the user's local ``claude`` login.
  No extra env vars are set; the CLI authenticates itself.
- ``api_key`` — sets ``ANTHROPIC_API_KEY`` from the constructor.
- ``bedrock`` — sets ``CLAUDE_CODE_USE_BEDROCK=1`` and ``AWS_PROFILE``.

Provider-affecting env vars inherited from the parent process are
scrubbed before our overrides are applied, so a wrapper started under
one provider can spawn a child under another without leaking config.

The CLI emits a single JSON document with a top-level ``result``
field; we extract that. Non-JSON output falls back to raw stdout so
the agent layer can still salvage a malformed response.
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


_PROVIDER_VARS = (
    "CLAUDE_CODE_USE_BEDROCK",
    "ANTHROPIC_API_KEY",
    "AWS_PROFILE",
    "ANTHROPIC_MODEL",
)

_VALID_PROVIDERS = ("subscription", "api_key", "bedrock")

_DEFAULT_MODEL_BY_PROVIDER = {
    "subscription": "opus",
    "api_key": "claude-opus-4-6",
    "bedrock": "us.anthropic.claude-opus-4-6-v1[1m]",
}


@dataclass
class ClaudeBackend:
    """``AgentBackend`` impl that shells out to the ``claude`` CLI.

    Per-role model overrides live in ``model_per_role``; otherwise
    ``default_model`` (or the provider-specific default) is used.
    """

    name: str = "claude"
    cli_path: str = "claude"
    provider: str = "subscription"
    default_model: str | None = None
    model_per_role: dict[str, str] = field(default_factory=dict)
    api_key: str = ""
    aws_profile: str = "default"

    def __post_init__(self) -> None:
        if self.provider not in _VALID_PROVIDERS:
            raise ValueError(
                f"Unknown claude provider {self.provider!r}; "
                f"expected one of {_VALID_PROVIDERS}"
            )
        if self.default_model is None:
            self.default_model = _DEFAULT_MODEL_BY_PROVIDER[self.provider]

    # -- helpers -----------------------------------------------------------
    def _model_for(self, agent_role: str) -> str:
        # default_model is guaranteed non-None after __post_init__
        return self.model_per_role.get(agent_role, self.default_model or "opus")

    def _provider_env(self) -> dict[str, str]:
        """Env additions for the chosen auth provider."""
        if self.provider == "subscription":
            return {}
        if self.provider == "api_key":
            # Empty key is allowed at construction time so configs that
            # never actually invoke claude can still load. The CLI will
            # surface its own auth error when invoked.
            return {"ANTHROPIC_API_KEY": self.api_key} if self.api_key else {}
        if self.provider == "bedrock":
            return {
                "CLAUDE_CODE_USE_BEDROCK": "1",
                "AWS_PROFILE": self.aws_profile,
            }
        # Should be unreachable thanks to __post_init__ guard.
        raise ValueError(f"Unknown provider {self.provider!r}")

    def _compose_env(self) -> dict[str, str]:
        scrubbed = {k: v for k, v in os.environ.items() if k not in _PROVIDER_VARS}
        scrubbed.update(self._provider_env())
        return scrubbed

    def _build_argv(self, *, agent_role: str, prompt: str) -> list[str]:
        return [
            self.cli_path,
            "-p",
            "--output-format", "json",
            "--dangerously-skip-permissions",
            "--model", self._model_for(agent_role),
            prompt,
        ]

    # -- AgentBackend protocol --------------------------------------------
    def run(
        self,
        *,
        agent_role: str,
        prompt: str,
        context_files: list[Path] | None = None,
        timeout_seconds: int = 300,
        output_format: str = "json",  # noqa: ARG002 — claude CLI is JSON-only here
    ) -> AgentResult:
        full_prompt = _compose_prompt(prompt, context_files)
        argv = self._build_argv(agent_role=agent_role, prompt=full_prompt)

        started = time.monotonic()
        try:
            result = subprocess.run(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._compose_env(),
                timeout=timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise BackendError(
                f"claude CLI not found at {self.cli_path!r}: {exc}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise BackendError(
                f"claude timed out after {timeout_seconds}s"
            ) from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        response = _parse_claude_output(result.stdout)

        if result.returncode != 0:
            tail = (result.stderr or "").strip()
            print(
                f"[claude] non-zero exit {result.returncode}",
                file=sys.stderr,
            )
            if tail:
                print(f"[claude] stderr: {tail[:500]}", file=sys.stderr)

        if not response.strip():
            raise BackendError(
                f"claude returned empty response (exit={result.returncode}); "
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
    files = list(context_files or [])
    if not files:
        return prompt
    header_lines = ["[Context files — read with your file tools]"]
    header_lines.extend(f"- {p}" for p in files)
    header_lines.append("")
    return "\n".join(header_lines) + "\n" + prompt


def _parse_claude_output(raw_stdout: str) -> str:
    """Extract ``result`` from claude's JSON output; fall back to raw."""
    text = (raw_stdout or "").strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text
    if isinstance(data, dict):
        return str(data.get("result", "") or "")
    # Unexpected JSON shape — give the caller something to inspect.
    return text


def register_default() -> ClaudeBackend:
    """Self-register a default-config ClaudeBackend under name ``claude``.

    CLI startup (issue #8) chooses the provider from config and replaces
    this registration with a configured instance if needed.
    """
    from . import register_backend

    backend = ClaudeBackend()
    register_backend(backend)
    return backend


__all__ = ["ClaudeBackend", "register_default"]
