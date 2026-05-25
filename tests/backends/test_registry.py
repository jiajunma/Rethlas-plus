"""Backend Protocol + registry tests (issue #3)."""

from __future__ import annotations

import pytest

from rethlas_kb.backends import (
    AgentBackend,
    AgentResult,
    BackendError,
    MockBackend,
    available_backends,
    clear_registry,
    get_backend,
    register_backend,
)


@pytest.fixture(autouse=True)
def _empty_registry():
    """Each test runs against a clean registry."""
    clear_registry()
    yield
    clear_registry()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------
def test_mock_backend_satisfies_protocol() -> None:
    backend = MockBackend()
    assert isinstance(backend, AgentBackend)


def test_mock_backend_has_required_attributes() -> None:
    backend = MockBackend(name="custom-mock")
    assert backend.name == "custom-mock"
    assert callable(backend.run)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_register_and_lookup_backend() -> None:
    mock = MockBackend(name="mock-a")
    register_backend(mock)
    assert get_backend("mock-a") is mock


def test_get_backend_raises_for_unknown_name() -> None:
    with pytest.raises(BackendError, match="not registered"):
        get_backend("nonexistent")


def test_get_backend_error_lists_available() -> None:
    register_backend(MockBackend(name="claude-mock"))
    register_backend(MockBackend(name="codex-mock"))
    with pytest.raises(BackendError) as exc_info:
        get_backend("missing")
    msg = str(exc_info.value)
    assert "claude-mock" in msg
    assert "codex-mock" in msg


def test_available_backends_sorted() -> None:
    register_backend(MockBackend(name="zeta"))
    register_backend(MockBackend(name="alpha"))
    register_backend(MockBackend(name="mu"))
    assert available_backends() == ["alpha", "mu", "zeta"]


def test_re_register_overwrites() -> None:
    """Tests can swap a real backend's name for a MockBackend."""
    first = MockBackend(name="claude", canned_response='{"first": true}')
    second = MockBackend(name="claude", canned_response='{"second": true}')
    register_backend(first)
    register_backend(second)
    backend = get_backend("claude")
    assert backend is second


# ---------------------------------------------------------------------------
# Mock backend run behaviour
# ---------------------------------------------------------------------------
def test_mock_backend_returns_canned_response() -> None:
    backend = MockBackend(canned_response='{"decision": "accepted"}')
    result = backend.run(
        agent_role="statement-verifier",
        prompt="any prompt",
    )
    assert isinstance(result, AgentResult)
    assert result.stdout == '{"decision": "accepted"}'
    assert result.exit_code == 0
    assert result.backend_name == "mock"


def test_mock_backend_records_call_args() -> None:
    backend = MockBackend()
    backend.run(
        agent_role="proof-verifier",
        prompt="some prompt body",
        timeout_seconds=60,
        output_format="markdown",
    )
    assert backend.last_call == {
        "agent_role": "proof-verifier",
        "prompt": "some prompt body",
        "context_files": [],
        "timeout_seconds": 60,
        "output_format": "markdown",
    }
    assert backend.call_count == 1


def test_mock_backend_call_count_increments() -> None:
    backend = MockBackend()
    backend.run(agent_role="x", prompt="a")
    backend.run(agent_role="y", prompt="b")
    backend.run(agent_role="z", prompt="c")
    assert backend.call_count == 3


def test_mock_backend_honours_canned_failure() -> None:
    backend = MockBackend(canned_exit_code=1, canned_response="ERROR")
    result = backend.run(agent_role="x", prompt="y")
    assert result.exit_code == 1
    assert result.stdout == "ERROR"


# ---------------------------------------------------------------------------
# AgentResult dataclass
# ---------------------------------------------------------------------------
def test_agent_result_is_frozen() -> None:
    result = AgentResult(
        stdout="x", stderr="y", exit_code=0, duration_ms=1, backend_name="t"
    )
    with pytest.raises(Exception):  # FrozenInstanceError subclass of AttributeError
        result.exit_code = 99  # type: ignore[misc]


def test_agent_result_default_extras_isolated() -> None:
    """Default-factory dict on a frozen dataclass should not be shared."""
    r1 = AgentResult(stdout="", stderr="", exit_code=0, duration_ms=0, backend_name="t")
    r2 = AgentResult(stdout="", stderr="", exit_code=0, duration_ms=0, backend_name="t")
    assert r1.extra == {}
    assert r2.extra == {}
    assert r1.extra is not r2.extra
