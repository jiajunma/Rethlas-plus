"""CodexBackend tests (issue #4).

The smoke test against a real ``codex`` binary is skipped when it is
not on PATH — CI runs without it. Unit tests use ``unittest.mock`` so
they never spawn a real subprocess.
"""

from __future__ import annotations

import json
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
from rethlas_kb.backends.codex import (
    CodexBackend,
    _compose_prompt,
    _parse_codex_stream,
    register_default,
)


@pytest.fixture(autouse=True)
def _empty_registry():
    clear_registry()
    yield
    clear_registry()


def _fake_completed(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout=json.dumps(payload), stderr="",
    )


def _fake_event_stream(text: str, *, exit_code: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    """Build a codex-shaped JSONL stream with one ``agent_message``."""
    events = [
        {"type": "thread.started"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": text},
        },
        {"type": "turn.completed"},
    ]
    stdout = "\n".join(json.dumps(e) for e in events) + "\n"
    return subprocess.CompletedProcess(
        args=[], returncode=exit_code, stdout=stdout, stderr=stderr,
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------
def test_codex_backend_satisfies_protocol() -> None:
    assert isinstance(CodexBackend(), AgentBackend)


def test_codex_backend_default_name() -> None:
    assert CodexBackend().name == "codex"


# ---------------------------------------------------------------------------
# argv composition
# ---------------------------------------------------------------------------
def test_argv_uses_default_model_and_reasoning() -> None:
    backend = CodexBackend(default_model="gpt-9", default_reasoning="medium")
    argv = backend._build_argv(agent_role="statement-verifier", prompt="hi")
    assert "gpt-9" in argv
    assert 'model_reasoning_effort="medium"' in argv
    assert argv[-1] == "hi"
    assert "--json" in argv
    assert "--dangerously-bypass-approvals-and-sandbox" in argv


def test_argv_per_role_model_overrides_default() -> None:
    backend = CodexBackend(
        default_model="gpt-base",
        model_per_role={"proof-verifier": "gpt-deep"},
    )
    argv_a = backend._build_argv(agent_role="proof-verifier", prompt="x")
    argv_b = backend._build_argv(agent_role="statement-verifier", prompt="x")
    assert "gpt-deep" in argv_a
    assert "gpt-base" in argv_b


def test_argv_uses_custom_cli_path() -> None:
    backend = CodexBackend(cli_path="/opt/codex/bin/codex")
    argv = backend._build_argv(agent_role="x", prompt="y")
    assert argv[0] == "/opt/codex/bin/codex"


# ---------------------------------------------------------------------------
# JSON event parsing
# ---------------------------------------------------------------------------
def test_parse_extracts_agent_message_text() -> None:
    events = [
        {"type": "item.completed",
         "item": {"type": "agent_message", "text": "hello world"}},
    ]
    stream = "\n".join(json.dumps(e) for e in events)
    assert _parse_codex_stream(stream) == "hello world"


def test_parse_ignores_unrelated_events() -> None:
    events = [
        {"type": "thread.started"},
        {"type": "item.completed",
         "item": {"type": "tool_call", "name": "search"}},
        {"type": "item.completed",
         "item": {"type": "agent_message", "text": "the answer"}},
    ]
    stream = "\n".join(json.dumps(e) for e in events)
    assert _parse_codex_stream(stream) == "the answer"


def test_parse_uses_last_agent_message_when_multiple() -> None:
    events = [
        {"type": "item.completed",
         "item": {"type": "agent_message", "text": "first"}},
        {"type": "item.completed",
         "item": {"type": "agent_message", "text": "second"}},
    ]
    stream = "\n".join(json.dumps(e) for e in events)
    assert _parse_codex_stream(stream) == "second"


def test_parse_falls_back_to_raw_stdout_when_not_jsonl() -> None:
    assert _parse_codex_stream("plain text not json") == "plain text not json"


def test_parse_empty_stream_returns_empty_string() -> None:
    assert _parse_codex_stream("") == ""
    assert _parse_codex_stream("   \n   ") == ""


# ---------------------------------------------------------------------------
# Context-file header
# ---------------------------------------------------------------------------
def test_compose_prompt_without_context_files_passes_through() -> None:
    assert _compose_prompt("just the prompt", None) == "just the prompt"
    assert _compose_prompt("just the prompt", []) == "just the prompt"


def test_compose_prompt_prepends_context_file_header() -> None:
    out = _compose_prompt("body", [Path("a.md"), Path("b.md")])
    assert out.startswith("[Context files")
    assert "- a.md" in out
    assert "- b.md" in out
    assert out.endswith("body")


# ---------------------------------------------------------------------------
# run() — mocked subprocess
# ---------------------------------------------------------------------------
def test_run_happy_path_returns_agent_result() -> None:
    backend = CodexBackend()
    with patch("subprocess.run",
               return_value=_fake_event_stream("the verdict is YES")) as mock_run:
        result = backend.run(agent_role="statement-verifier", prompt="check X")

    assert isinstance(result, AgentResult)
    assert result.stdout == "the verdict is YES"
    assert result.exit_code == 0
    assert result.backend_name == "codex"
    assert result.duration_ms >= 0
    mock_run.assert_called_once()


def test_run_non_zero_exit_with_parsed_response_succeeds(capsys) -> None:
    """Codex often exits non-zero even when the response is valid."""
    backend = CodexBackend()
    with patch("subprocess.run",
               return_value=_fake_event_stream(
                   "actual answer", exit_code=137, stderr="some warning")):
        result = backend.run(agent_role="x", prompt="y")

    assert result.stdout == "actual answer"
    assert result.exit_code == 137
    err = capsys.readouterr().err
    assert "non-zero exit 137" in err
    assert "treating as warning" in err


def test_run_empty_response_raises_backend_error() -> None:
    backend = CodexBackend()
    empty = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="auth failed",
    )
    with patch("subprocess.run", return_value=empty):
        with pytest.raises(BackendError, match="empty response"):
            backend.run(agent_role="x", prompt="y")


def test_run_cli_not_found_raises_backend_error() -> None:
    backend = CodexBackend(cli_path="/nonexistent/codex-binary")
    with patch("subprocess.run", side_effect=FileNotFoundError("not found")):
        with pytest.raises(BackendError, match="codex CLI not found"):
            backend.run(agent_role="x", prompt="y")


def test_run_timeout_raises_backend_error() -> None:
    backend = CodexBackend()
    with patch("subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="codex", timeout=5)):
        with pytest.raises(BackendError, match="timed out after 5"):
            backend.run(agent_role="x", prompt="y", timeout_seconds=5)


def test_run_passes_timeout_to_subprocess() -> None:
    backend = CodexBackend()
    with patch("subprocess.run",
               return_value=_fake_event_stream("ok")) as mock_run:
        backend.run(agent_role="x", prompt="y", timeout_seconds=42)
    assert mock_run.call_args.kwargs["timeout"] == 42


def test_run_passes_context_files_via_prompt_header() -> None:
    backend = CodexBackend()
    with patch("subprocess.run",
               return_value=_fake_event_stream("ok")) as mock_run:
        backend.run(
            agent_role="x", prompt="user prompt",
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
    assert "codex" not in available_backends()
    register_default()
    assert "codex" in available_backends()
    assert isinstance(get_backend("codex"), CodexBackend)


# ---------------------------------------------------------------------------
# Smoke test — only when real CLI present
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    shutil.which("codex") is None,
    reason="codex CLI not installed on this host",
)
def test_real_codex_returns_nonempty_response() -> None:
    backend = CodexBackend()
    result = backend.run(
        agent_role="smoke",
        prompt="Reply with exactly the word PONG and nothing else.",
        timeout_seconds=120,
    )
    assert result.stdout.strip(), "real codex returned empty"
    assert result.backend_name == "codex"
