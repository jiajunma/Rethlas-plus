"""ClaudeBackend tests (issue #5)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from rethlas_kb.backends import (
    AgentBackend,
    AgentResult,
    BackendError,
    available_backends,
    clear_registry,
    get_backend,
)
from rethlas_kb.backends.claude import (
    ClaudeBackend,
    _compose_prompt,
    _parse_claude_output,
    register_default,
)


@pytest.fixture(autouse=True)
def _empty_registry():
    clear_registry()
    yield
    clear_registry()


def _fake_claude_run(
    result_text: str = "ok",
    *,
    exit_code: int = 0,
    stderr: str = "",
    raw_stdout: str | None = None,
) -> subprocess.CompletedProcess:
    if raw_stdout is None:
        raw_stdout = json.dumps({"result": result_text})
    return subprocess.CompletedProcess(
        args=[], returncode=exit_code, stdout=raw_stdout, stderr=stderr,
    )


# ---------------------------------------------------------------------------
# Protocol conformance + construction
# ---------------------------------------------------------------------------
def test_claude_backend_satisfies_protocol() -> None:
    assert isinstance(ClaudeBackend(), AgentBackend)


def test_default_name_is_claude() -> None:
    assert ClaudeBackend().name == "claude"


def test_invalid_provider_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown claude provider"):
        ClaudeBackend(provider="bogus")


def test_default_model_picks_per_provider_default() -> None:
    assert ClaudeBackend(provider="subscription").default_model == "opus"
    assert (
        ClaudeBackend(provider="api_key").default_model == "claude-opus-4-6"
    )
    assert ClaudeBackend(provider="bedrock").default_model.startswith(
        "us.anthropic.claude"
    )


def test_explicit_default_model_overrides_provider_default() -> None:
    backend = ClaudeBackend(provider="subscription", default_model="haiku")
    assert backend.default_model == "haiku"


# ---------------------------------------------------------------------------
# argv composition
# ---------------------------------------------------------------------------
def test_argv_contains_required_flags() -> None:
    argv = ClaudeBackend()._build_argv(agent_role="x", prompt="hello")
    assert "-p" in argv
    assert "--output-format" in argv
    assert "json" in argv
    assert "--dangerously-skip-permissions" in argv
    assert "--model" in argv
    assert argv[-1] == "hello"


def test_argv_uses_per_role_model_when_set() -> None:
    backend = ClaudeBackend(
        default_model="haiku",
        model_per_role={"proof-verifier": "opus"},
    )
    argv_a = backend._build_argv(agent_role="proof-verifier", prompt="x")
    argv_b = backend._build_argv(agent_role="statement-verifier", prompt="x")
    assert "opus" in argv_a
    assert "haiku" in argv_b


def test_argv_uses_custom_cli_path() -> None:
    backend = ClaudeBackend(cli_path="/opt/claude/bin/claude")
    argv = backend._build_argv(agent_role="x", prompt="y")
    assert argv[0] == "/opt/claude/bin/claude"


# ---------------------------------------------------------------------------
# Provider auth env
# ---------------------------------------------------------------------------
def test_subscription_env_is_clean() -> None:
    backend = ClaudeBackend(provider="subscription")
    assert backend._provider_env() == {}


def test_api_key_env_sets_anthropic_key() -> None:
    backend = ClaudeBackend(provider="api_key", api_key="sk-test-123")
    assert backend._provider_env() == {"ANTHROPIC_API_KEY": "sk-test-123"}


def test_api_key_provider_with_empty_key_defers_failure() -> None:
    """Empty key shouldn't error at construction — CLI surfaces it later."""
    backend = ClaudeBackend(provider="api_key", api_key="")
    assert backend._provider_env() == {}  # no override; CLI fails when invoked


def test_bedrock_env_sets_bedrock_flag_and_profile() -> None:
    backend = ClaudeBackend(provider="bedrock", aws_profile="prod")
    env = backend._provider_env()
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert env["AWS_PROFILE"] == "prod"


def test_compose_env_scrubs_inherited_provider_vars() -> None:
    backend = ClaudeBackend(provider="subscription")
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "leaked",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "ANTHROPIC_MODEL": "wrong",
        "AWS_PROFILE": "leak",
        "PATH": "/usr/bin",
    }, clear=True):
        env = backend._compose_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert "ANTHROPIC_MODEL" not in env
    assert "AWS_PROFILE" not in env
    assert env["PATH"] == "/usr/bin"  # unrelated vars survive


def test_compose_env_layers_provider_overrides_after_scrub() -> None:
    backend = ClaudeBackend(provider="api_key", api_key="sk-new")
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "stale-inherited-key",
        "PATH": "/usr/bin",
    }, clear=True):
        env = backend._compose_env()
    assert env["ANTHROPIC_API_KEY"] == "sk-new"  # ours wins after scrub


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------
def test_parse_extracts_result_field() -> None:
    raw = json.dumps({"result": "the answer", "cost": 0.01})
    assert _parse_claude_output(raw) == "the answer"


def test_parse_returns_raw_on_invalid_json() -> None:
    assert _parse_claude_output("not json at all") == "not json at all"


def test_parse_returns_raw_on_non_object_json() -> None:
    assert _parse_claude_output("[1, 2, 3]") == "[1, 2, 3]"


def test_parse_handles_null_result_field() -> None:
    raw = json.dumps({"result": None})
    assert _parse_claude_output(raw) == ""


def test_parse_empty_stream_returns_empty_string() -> None:
    assert _parse_claude_output("") == ""
    assert _parse_claude_output("   \n   ") == ""


# ---------------------------------------------------------------------------
# Context-file header
# ---------------------------------------------------------------------------
def test_compose_prompt_without_files_passes_through() -> None:
    assert _compose_prompt("body", None) == "body"
    assert _compose_prompt("body", []) == "body"


def test_compose_prompt_prepends_file_header() -> None:
    out = _compose_prompt("body", [Path("a.md"), Path("b.md")])
    assert "[Context files" in out
    assert "- a.md" in out
    assert "- b.md" in out
    assert out.endswith("body")


# ---------------------------------------------------------------------------
# run() — mocked subprocess
# ---------------------------------------------------------------------------
def test_run_happy_path_returns_agent_result() -> None:
    backend = ClaudeBackend()
    with patch("subprocess.run",
               return_value=_fake_claude_run("the verdict is YES")) as mock_run:
        result = backend.run(agent_role="statement-verifier", prompt="check X")

    assert isinstance(result, AgentResult)
    assert result.stdout == "the verdict is YES"
    assert result.exit_code == 0
    assert result.backend_name == "claude"
    assert result.duration_ms >= 0
    mock_run.assert_called_once()


def test_run_non_zero_exit_with_parsed_response_warns(capsys) -> None:
    backend = ClaudeBackend()
    with patch("subprocess.run",
               return_value=_fake_claude_run(
                   "salvaged response", exit_code=1, stderr="some warning")):
        result = backend.run(agent_role="x", prompt="y")

    assert result.stdout == "salvaged response"
    assert result.exit_code == 1
    err = capsys.readouterr().err
    assert "non-zero exit 1" in err


def test_run_empty_response_raises_backend_error() -> None:
    backend = ClaudeBackend()
    empty = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="auth failed",
    )
    with patch("subprocess.run", return_value=empty):
        with pytest.raises(BackendError, match="empty response"):
            backend.run(agent_role="x", prompt="y")


def test_run_cli_not_found_raises_backend_error() -> None:
    backend = ClaudeBackend(cli_path="/nonexistent/claude")
    with patch("subprocess.run", side_effect=FileNotFoundError("nope")):
        with pytest.raises(BackendError, match="claude CLI not found"):
            backend.run(agent_role="x", prompt="y")


def test_run_timeout_raises_backend_error() -> None:
    backend = ClaudeBackend()
    with patch("subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=7)):
        with pytest.raises(BackendError, match="timed out after 7"):
            backend.run(agent_role="x", prompt="y", timeout_seconds=7)


def test_run_passes_timeout_to_subprocess() -> None:
    backend = ClaudeBackend()
    with patch("subprocess.run",
               return_value=_fake_claude_run("ok")) as mock_run:
        backend.run(agent_role="x", prompt="y", timeout_seconds=33)
    assert mock_run.call_args.kwargs["timeout"] == 33


def test_run_passes_provider_env_to_subprocess() -> None:
    backend = ClaudeBackend(provider="api_key", api_key="sk-real")
    with patch("subprocess.run",
               return_value=_fake_claude_run("ok")) as mock_run:
        backend.run(agent_role="x", prompt="y")
    env = mock_run.call_args.kwargs["env"]
    assert env["ANTHROPIC_API_KEY"] == "sk-real"


def test_run_passes_context_files_via_prompt_header() -> None:
    backend = ClaudeBackend()
    with patch("subprocess.run",
               return_value=_fake_claude_run("ok")) as mock_run:
        backend.run(
            agent_role="x",
            prompt="user prompt",
            context_files=[Path("docs/node-001.md")],
        )
    argv = mock_run.call_args.args[0]
    final_prompt = argv[-1]
    assert "docs/node-001.md" in final_prompt
    assert final_prompt.endswith("user prompt")


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------
def test_register_default_makes_backend_lookup_succeed() -> None:
    assert "claude" not in available_backends()
    register_default()
    assert "claude" in available_backends()
    assert isinstance(get_backend("claude"), ClaudeBackend)


# ---------------------------------------------------------------------------
# Smoke test — only when real CLI present
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="claude CLI not installed on this host",
)
def test_real_claude_returns_nonempty_response() -> None:
    backend = ClaudeBackend()
    result = backend.run(
        agent_role="smoke",
        prompt="Reply with exactly the word PONG and nothing else.",
        timeout_seconds=120,
    )
    assert result.stdout.strip(), "real claude returned empty"
    assert result.backend_name == "claude"
