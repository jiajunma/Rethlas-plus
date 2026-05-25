"""Tests for v1.3 --project batch mode + status / open-questions (issue #15)."""

from __future__ import annotations

import io
import json
import textwrap
from pathlib import Path

import pytest
import yaml

from rethlas_kb import cli
from rethlas_kb.backends import MockBackend, clear_registry, register_backend


# ---------------------------------------------------------------------------
# Fixture: small project with mixed states
# ---------------------------------------------------------------------------
GROUP_MD = textwrap.dedent("""\
    ---
    id: algebra.group
    title: Group
    kind: definition
    status: admitted
    primary_topic: algebra
    topics: [algebra]
    ---
    # Group
    """)

QUOTIENT_MD = textwrap.dedent("""\
    ---
    id: algebra.quotient_group
    title: Quotient Group
    kind: definition
    status: staged
    uses: [algebra.group]
    primary_topic: algebra
    topics: [algebra]
    ---
    # Quotient Group

    Cosets of a normal subgroup.
    """)

ISO_THM_MD = textwrap.dedent("""\
    ---
    id: algebra.iso_thm
    title: First Iso Theorem
    kind: theorem
    status: staged
    uses: [algebra.quotient_group]
    primary_topic: algebra
    topics: [algebra]
    ---
    # First Iso Theorem

    > **Theorem.** ...
    **Proof.** TODO
    """)


@pytest.fixture(autouse=True)
def _empty_registry():
    clear_registry()
    yield
    clear_registry()


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    """Blueprint with 3 nodes + a 'main' project rooted at iso_thm."""
    knowledge = tmp_path / "docs" / "knowledge"
    (knowledge / "nodes" / "algebra").mkdir(parents=True)
    (knowledge / "staged" / "algebra").mkdir(parents=True)
    (knowledge / "nodes" / "algebra" / "group.md").write_text(GROUP_MD)
    (knowledge / "staged" / "algebra" / "quotient_group.md").write_text(QUOTIENT_MD)
    (knowledge / "staged" / "algebra" / "iso_thm.md").write_text(ISO_THM_MD)

    projects_dir = tmp_path / ".rethlas-kb" / "projects"
    projects_dir.mkdir(parents=True)
    (projects_dir / "main.yml").write_text(yaml.safe_dump({
        "id": "main",
        "title": "First Iso Theorem project",
        "goal_nodes": ["algebra.iso_thm"],
    }))
    return tmp_path


def _run(argv: list[str], *, stdin: str = "") -> tuple[int, str, str]:
    import sys
    old_in, old_out, old_err = sys.stdin, sys.stdout, sys.stderr
    sys.stdin = io.StringIO(stdin)
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        rc = cli.main(argv)
        return rc, sys.stdout.getvalue(), sys.stderr.getvalue()
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_in, old_out, old_err


def _accepted_response() -> str:
    return json.dumps({
        "decision": "accepted",
        "rationale": "looks fine",
        "confidence": 0.9,
    })


def _formulation_issue_response() -> str:
    return json.dumps({
        "decision": "formulation_issue",
        "rationale": "missing quantifier",
        "formulation_issues": ["forall G missing"],
    })


# ===========================================================================
# status
# ===========================================================================
def test_status_human_readable_includes_counts(kb: Path) -> None:
    rc, out, _ = _run([
        "status", "--project", "main", "--blueprint", str(kb),
    ])
    assert rc == cli.EXIT_OK
    assert "project: main" in out
    assert "First Iso Theorem project" in out
    assert "closure:" in out
    assert "admitted:" in out
    assert "staged:" in out


def test_status_json_returns_machine_readable(kb: Path) -> None:
    rc, out, _ = _run([
        "status", "--project", "main", "--blueprint", str(kb), "--json",
    ])
    assert rc == cli.EXIT_OK
    payload = json.loads(out)
    assert payload["project_id"] == "main"
    assert payload["goal_count"] == 1
    assert payload["closure_count"] == 3       # iso_thm + quotient + group
    assert payload["admitted_count"] == 1      # group
    assert payload["staged_count"] == 2        # iso_thm + quotient
    assert payload["missing_count"] == 0
    assert payload["by_status"]["admitted"] == 1
    assert payload["by_status"]["staged"] == 2


def test_status_missing_project_returns_usage(kb: Path) -> None:
    rc, _, err = _run([
        "status", "--project", "nonexistent", "--blueprint", str(kb),
    ])
    assert rc == cli.EXIT_USAGE
    assert "manifest not found" in err


def test_status_requires_project_flag(kb: Path) -> None:
    with pytest.raises(SystemExit) as ei:
        _run(["status", "--blueprint", str(kb)])
    assert ei.value.code == cli.EXIT_USAGE


# ===========================================================================
# open-questions
# ===========================================================================
def test_open_questions_lists_staged_in_priority_order(kb: Path) -> None:
    rc, out, _ = _run([
        "open-questions", "--project", "main", "--blueprint", str(kb), "--json",
    ])
    assert rc == cli.EXIT_OK
    payload = json.loads(out)
    ids = [q["node_id"] for q in payload]
    # algebra.group is admitted → excluded
    assert "algebra.group" not in ids
    # iso_thm at distance 0 (goal), quotient at distance 1
    assert ids[0] == "algebra.iso_thm"
    assert "algebra.quotient_group" in ids


def test_open_questions_human_readable(kb: Path) -> None:
    rc, out, _ = _run([
        "open-questions", "--project", "main", "--blueprint", str(kb),
    ])
    assert rc == cli.EXIT_OK
    assert "open question" in out
    assert "algebra.iso_thm" in out
    assert "algebra.quotient_group" in out


def test_open_questions_limit_caps_output(kb: Path) -> None:
    rc, out, _ = _run([
        "open-questions", "--project", "main", "--blueprint", str(kb),
        "--json", "--limit", "1",
    ])
    payload = json.loads(out)
    assert len(payload) == 1


def test_open_questions_empty_when_all_admitted(tmp_path: Path) -> None:
    knowledge = tmp_path / "docs" / "knowledge"
    (knowledge / "nodes" / "algebra").mkdir(parents=True)
    (knowledge / "nodes" / "algebra" / "group.md").write_text(GROUP_MD)
    projects = tmp_path / ".rethlas-kb" / "projects"
    projects.mkdir(parents=True)
    (projects / "main.yml").write_text(yaml.safe_dump({
        "id": "main", "goal_nodes": ["algebra.group"],
    }))
    rc, out, _ = _run([
        "open-questions", "--project", "main", "--blueprint", str(tmp_path),
    ])
    assert rc == cli.EXIT_OK
    assert "no open questions" in out


# ===========================================================================
# list-projects
# ===========================================================================
def test_list_projects_returns_sorted_ids(kb: Path) -> None:
    # Add a second project
    projects = kb / ".rethlas-kb" / "projects"
    (projects / "secondary.yml").write_text(yaml.safe_dump({
        "id": "secondary", "goal_nodes": ["algebra.quotient_group"],
    }))
    rc, out, _ = _run(["list-projects", "--blueprint", str(kb)])
    assert rc == cli.EXIT_OK
    lines = [line for line in out.strip().split("\n") if line.strip()]
    assert lines == ["main", "secondary"]


# ===========================================================================
# batch verify-stmt --project
# ===========================================================================
def test_verify_stmt_batch_runs_on_each_staged_node(kb: Path) -> None:
    register_backend(MockBackend(name="codex", canned_response=_accepted_response()))
    rc, out, err = _run([
        "verify-stmt", "--project", "main", "--blueprint", str(kb),
    ])
    assert rc == cli.EXIT_OK
    # The summary mentions both staged nodes
    assert "algebra.iso_thm" in err
    assert "algebra.quotient_group" in err
    # JSON payload on stdout summarises
    payload = json.loads(out)
    assert payload["project_id"] == "main"
    assert payload["agent_role"] == "statement-verifier"
    # 2 applicable (both are staged, both are statement-kinds)
    assert payload["applicable_count"] == 2
    assert payload["accepted_count"] == 2
    assert payload["flagged_count"] == 0


def test_verify_stmt_batch_flagged_returns_review_fail(kb: Path) -> None:
    register_backend(MockBackend(
        name="codex", canned_response=_formulation_issue_response(),
    ))
    rc, out, _ = _run([
        "verify-stmt", "--project", "main", "--blueprint", str(kb),
    ])
    assert rc == cli.EXIT_REVIEW_FAIL
    payload = json.loads(out)
    assert payload["flagged_count"] >= 1


def test_verify_stmt_rejects_both_node_id_and_project(kb: Path) -> None:
    rc, _, err = _run([
        "verify-stmt", "algebra.iso_thm",
        "--project", "main", "--blueprint", str(kb),
    ])
    assert rc == cli.EXIT_USAGE
    assert "either" in err


def test_verify_stmt_rejects_neither_node_id_nor_project(kb: Path) -> None:
    rc, _, err = _run(["verify-stmt", "--blueprint", str(kb)])
    assert rc == cli.EXIT_USAGE
    assert "either" in err


def test_verify_stmt_batch_skips_admitted_nodes(kb: Path) -> None:
    """The agent should NOT be called on algebra.group (admitted)."""
    mock = MockBackend(name="codex", canned_response=_accepted_response())
    register_backend(mock)
    _run(["verify-stmt", "--project", "main", "--blueprint", str(kb)])
    # 2 calls = iso_thm + quotient_group only; group is admitted
    assert mock.call_count == 2


def test_verify_stmt_batch_writes_one_review_per_applicable_node(
    kb: Path,
) -> None:
    register_backend(MockBackend(name="codex", canned_response=_accepted_response()))
    _run(["verify-stmt", "--project", "main", "--blueprint", str(kb)])
    reviews = list((kb / "docs" / "knowledge" / "reviews").glob("*.md"))
    # one per applicable node
    assert len(reviews) == 2


# ===========================================================================
# batch + single-node retain different exit code semantics
# ===========================================================================
def test_single_node_path_still_works_post_refactor(kb: Path) -> None:
    register_backend(MockBackend(name="codex", canned_response=_accepted_response()))
    rc, out, _ = _run([
        "verify-stmt", "algebra.iso_thm", "--blueprint", str(kb),
    ])
    assert rc == cli.EXIT_OK
    # Single-node mode emits the rich review JSON (NOT a batch summary)
    payload = json.loads(out)
    assert payload.get("decision") == "accepted"
    # No batch keys
    assert "applicable_count" not in payload


def test_batch_with_missing_project_returns_usage(kb: Path) -> None:
    rc, _, err = _run([
        "verify-stmt", "--project", "no-such-project", "--blueprint", str(kb),
    ])
    assert rc == cli.EXIT_USAGE
    assert "manifest not found" in err
