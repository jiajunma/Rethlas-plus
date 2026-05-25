"""CLI tests (issue #8).

Pre-registers a MockBackend under the same name (``codex`` / ``claude``)
the CLI would otherwise wire up, so we never spawn a real subprocess.
The CLI's ``_register_backends`` is a no-op when a name is already in
the registry (registry semantics from issue #3).
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from rethlas_kb import cli
from rethlas_kb.backends import (
    MockBackend,
    available_backends,
    clear_registry,
    register_backend,
)

# --- Inline KB fixture --------------------------------------------------
QUOTIENT_MD = textwrap.dedent("""\
    ---
    id: algebra.quotient_group
    title: Quotient Group
    kind: definition
    status: staged
    uses:
      - algebra.group
    primary_topic: algebra
    topics: [algebra]
    ---

    # Quotient Group

    Let $G$ be a group and $N$ a normal subgroup. $G/N$ is the cosets.
    """)

GROUP_MD = textwrap.dedent("""\
    ---
    id: algebra.group
    title: Group
    kind: definition
    status: admitted
    uses: []
    primary_topic: algebra
    topics: [algebra]
    ---

    # Group

    A set with associative multiplication, identity, and inverses.
    """)


@pytest.fixture(autouse=True)
def _empty_registry():
    clear_registry()
    yield
    clear_registry()


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    """Build a tiny mdblueprint repo and return its root."""
    knowledge = tmp_path / "docs" / "knowledge"
    (knowledge / "nodes" / "algebra").mkdir(parents=True)
    (knowledge / "staged" / "algebra").mkdir(parents=True)
    (knowledge / "nodes" / "algebra" / "group.md").write_text(GROUP_MD)
    (knowledge / "staged" / "algebra" / "quotient_group.md").write_text(
        QUOTIENT_MD
    )
    return tmp_path


def _accepted_response(rationale: str = "Looks fine.") -> str:
    return json.dumps({
        "decision": "accepted",
        "rationale": rationale,
        "confidence": 0.9,
    })


# ---------------------------------------------------------------------------
# Top-level: --version / --help / no args
# ---------------------------------------------------------------------------
def test_version_flag_prints_version(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--version"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "rethlas-kb" in out
    assert cli.__version__ in out


def test_help_flag_lists_verify_stmt(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "verify-stmt" in out


def test_no_args_prints_usage_and_returns_zero(capsys) -> None:
    rc = cli.main([])
    assert rc == cli.EXIT_OK
    err = capsys.readouterr().err
    assert "verify-stmt" in err


# ---------------------------------------------------------------------------
# verify-stmt — happy path
# ---------------------------------------------------------------------------
def test_verify_stmt_accepted_returns_exit_ok(kb: Path, capsys) -> None:
    register_backend(MockBackend(name="codex", canned_response=_accepted_response()))
    rc = cli.main(["verify-stmt", "algebra.quotient_group",
                   "--project", str(kb)])
    assert rc == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "accepted"
    assert payload["rationale"] == "Looks fine."
    # `raw` is stripped from the stdout view
    assert "raw" not in payload


def test_verify_stmt_writes_review_file_by_default(kb: Path, capsys) -> None:
    register_backend(MockBackend(name="codex", canned_response=_accepted_response()))
    cli.main(["verify-stmt", "algebra.quotient_group", "--project", str(kb)])
    err = capsys.readouterr().err
    assert "review written:" in err

    reviews = list((kb / "docs" / "knowledge" / "reviews").glob("*.md"))
    assert len(reviews) == 1
    text = reviews[0].read_text()
    assert "agent: statement-verifier" in text
    assert "decision: accepted" in text
    # Raw LLM output preserved in the body, not the frontmatter
    assert "Raw LLM output" in text


def test_verify_stmt_no_write_skips_persistence(kb: Path) -> None:
    register_backend(MockBackend(name="codex", canned_response=_accepted_response()))
    rc = cli.main(["verify-stmt", "algebra.quotient_group",
                   "--project", str(kb), "--no-write"])
    assert rc == cli.EXIT_OK
    reviews_dir = kb / "docs" / "knowledge" / "reviews"
    assert not reviews_dir.exists() or not list(reviews_dir.glob("*.md"))


# ---------------------------------------------------------------------------
# verify-stmt — backend selection
# ---------------------------------------------------------------------------
def test_verify_stmt_defaults_to_codex_backend(kb: Path) -> None:
    codex_mock = MockBackend(name="codex", canned_response=_accepted_response("via codex"))
    claude_mock = MockBackend(name="claude", canned_response=_accepted_response("via claude"))
    register_backend(codex_mock)
    register_backend(claude_mock)

    cli.main(["verify-stmt", "algebra.quotient_group", "--project", str(kb)])
    assert codex_mock.call_count == 1
    assert claude_mock.call_count == 0


def test_verify_stmt_backend_flag_routes_to_claude(kb: Path) -> None:
    codex_mock = MockBackend(name="codex", canned_response=_accepted_response("via codex"))
    claude_mock = MockBackend(name="claude", canned_response=_accepted_response("via claude"))
    register_backend(codex_mock)
    register_backend(claude_mock)

    cli.main(["verify-stmt", "algebra.quotient_group",
              "--project", str(kb), "--backend", "claude"])
    assert codex_mock.call_count == 0
    assert claude_mock.call_count == 1


def test_verify_stmt_rejects_unknown_backend(kb: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["verify-stmt", "algebra.quotient_group",
                  "--project", str(kb), "--backend", "gemini"])
    assert exc_info.value.code == cli.EXIT_USAGE


# ---------------------------------------------------------------------------
# verify-stmt — exit codes reflect decision
# ---------------------------------------------------------------------------
def test_verify_stmt_returns_review_fail_for_non_accepted(kb: Path) -> None:
    register_backend(MockBackend(
        name="codex",
        canned_response=json.dumps({
            "decision": "formulation_issue",
            "rationale": "missing quantifier",
            "formulation_issues": ["forall N missing"],
        }),
    ))
    rc = cli.main(["verify-stmt", "algebra.quotient_group",
                   "--project", str(kb)])
    assert rc == cli.EXIT_REVIEW_FAIL


# ---------------------------------------------------------------------------
# verify-stmt — error surfaces
# ---------------------------------------------------------------------------
def test_verify_stmt_missing_project_returns_usage(tmp_path: Path, capsys) -> None:
    bare = tmp_path / "not-a-blueprint"
    bare.mkdir()
    rc = cli.main(["verify-stmt", "algebra.x", "--project", str(bare)])
    assert rc == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "docs/knowledge" in err


def test_verify_stmt_missing_node_returns_runtime(kb: Path, capsys) -> None:
    register_backend(MockBackend(name="codex", canned_response=_accepted_response()))
    rc = cli.main(["verify-stmt", "algebra.does_not_exist",
                   "--project", str(kb)])
    assert rc == cli.EXIT_RUNTIME
    assert "not found" in capsys.readouterr().err


def test_verify_stmt_unparseable_backend_returns_runtime(kb: Path, capsys) -> None:
    register_backend(MockBackend(name="codex", canned_response="not json"))
    rc = cli.main(["verify-stmt", "algebra.quotient_group",
                   "--project", str(kb)])
    assert rc == cli.EXIT_RUNTIME
    err = capsys.readouterr().err
    assert "could not be parsed" in err


# ---------------------------------------------------------------------------
# verify-stmt — flag passthrough
# ---------------------------------------------------------------------------
def test_verify_stmt_timeout_flag_propagates(kb: Path) -> None:
    mock = MockBackend(name="codex", canned_response=_accepted_response())
    register_backend(mock)
    cli.main(["verify-stmt", "algebra.quotient_group",
              "--project", str(kb), "--timeout", "42"])
    assert mock.last_call["timeout_seconds"] == 42


def test_verify_stmt_no_include_staged_flag(kb: Path) -> None:
    """When the flag is passed, the prompt's context mode is admitted-only."""
    mock = MockBackend(name="codex", canned_response=_accepted_response())
    register_backend(mock)
    cli.main(["verify-stmt", "algebra.group",
              "--project", str(kb), "--no-include-staged"])
    # Admitted-only context for an admitted node
    assert "Mode: **admitted**" in mock.last_call["prompt"]


# ---------------------------------------------------------------------------
# Backend registration helper
# ---------------------------------------------------------------------------
def test_register_backends_idempotent_when_mock_present() -> None:
    """A pre-registered mock under the same name must survive registration."""
    mock = MockBackend(name="codex", canned_response=_accepted_response())
    register_backend(mock)
    cli._register_backends()
    # The mock is still under "codex"; it was not clobbered
    from rethlas_kb.backends import get_backend
    assert get_backend("codex") is mock
    # claude was added since it wasn't there before
    assert "claude" in available_backends()
