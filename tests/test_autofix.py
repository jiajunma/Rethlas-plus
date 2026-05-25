"""v1.4 autofix agent tests (issue #23) — statement-fixer + def-stub + promote-request."""

from __future__ import annotations

import io
import json
import textwrap
from pathlib import Path

import pytest
import yaml

from rethlas_kb import cli
from rethlas_kb.adapter import KbAdapter
from rethlas_kb.backends import MockBackend, clear_registry, register_backend
from rethlas_kb_agents.def_stub_generator import (
    DefStubGenerator,
    DefStubReviewParseError,
    parse as parse_def_stub,
)
from rethlas_kb_agents.statement_fixer import (
    StatementFixer,
    StatementFixReviewParseError,
    parse as parse_fix,
)


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

QUOT_MD = textwrap.dedent("""\
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

    Some staged body with quantifier issue.
    """)


@pytest.fixture(autouse=True)
def _empty_registry():
    clear_registry()
    yield
    clear_registry()


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    knowledge = tmp_path / "docs" / "knowledge"
    (knowledge / "nodes" / "algebra").mkdir(parents=True)
    (knowledge / "staged" / "algebra").mkdir(parents=True)
    (knowledge / "nodes" / "algebra" / "group.md").write_text(GROUP_MD)
    (knowledge / "staged" / "algebra" / "quotient_group.md").write_text(QUOT_MD)
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


# ===========================================================================
# statement-fixer decoder
# ===========================================================================
def test_fixer_decoder_fixed_requires_fixed_body() -> None:
    with pytest.raises(StatementFixReviewParseError, match="fixed_requires_fixed_body"):
        parse_fix(json.dumps({
            "decision": "fixed", "rationale": "ok", "fixed_body": "",
        }))


def test_fixer_decoder_cannot_fix_requires_blocker() -> None:
    with pytest.raises(StatementFixReviewParseError, match="cannot_fix_requires_blocker"):
        parse_fix(json.dumps({"decision": "cannot_fix", "rationale": "x"}))


def test_fixer_decoder_defers_to_human_requires_blocker() -> None:
    with pytest.raises(StatementFixReviewParseError,
                       match="defers_to_human_requires_blocker"):
        parse_fix(json.dumps({"decision": "defers_to_human", "rationale": "x"}))


def test_fixer_decoder_fixed_round_trips() -> None:
    r = parse_fix(json.dumps({
        "decision": "fixed",
        "rationale": "added missing quantifier",
        "fixed_body": "# Quotient Group\n\nNew correct body.",
        "addressed_issues": ["quantifier drift on line 3"],
        "confidence": 0.85,
    }))
    assert r.decision == "fixed"
    assert r.writes_body
    assert "Quotient Group" in r.fixed_body


def test_fixer_decoder_invalid_decision_rejected() -> None:
    with pytest.raises(StatementFixReviewParseError, match="invalid_decision"):
        parse_fix(json.dumps({"decision": "maybe", "rationale": "x"}))


# ===========================================================================
# statement-fixer role
# ===========================================================================
def test_fixer_role_runs_with_prior_review(kb: Path) -> None:
    adapter = KbAdapter(kb)
    backend = MockBackend(canned_response=json.dumps({
        "decision": "fixed",
        "rationale": "patched quantifier",
        "fixed_body": "# Quotient Group\n\nNew body with $\\forall$ added.",
        "addressed_issues": ["quantifier missing"],
    }))
    fixer = StatementFixer(backend=backend)
    r = fixer.run(
        "algebra.quotient_group", adapter,
        prior_review="The statement is missing a universal quantifier.",
    )
    assert r.decision == "fixed"
    assert "quantifier" in backend.last_call["prompt"]
    assert backend.last_call["agent_role"] == "statement-fixer"


def test_fixer_role_surfaces_parse_error(kb: Path) -> None:
    adapter = KbAdapter(kb)
    backend = MockBackend(canned_response="not json")
    fixer = StatementFixer(backend=backend)
    with pytest.raises(StatementFixReviewParseError):
        fixer.run("algebra.quotient_group", adapter)


# ===========================================================================
# def-stub-generator decoder
# ===========================================================================
def _stub_payload(**overrides) -> dict:
    p = {
        "decision": "placeholder_only",
        "rationale": "needed for downstream proof",
        "proposed_id": "algebra.normal_subgroup",
        "title": "Normal Subgroup",
        "primary_topic": "algebra",
        "topics": ["algebra"],
        "uses": ["algebra.group"],
        "body": "# Normal Subgroup\n\n**TODO:** define.",
    }
    p.update(overrides)
    return p


def test_stub_decoder_drafted_round_trips() -> None:
    r = parse_def_stub(json.dumps(_stub_payload(
        decision="drafted",
        body="# Normal Subgroup\n\nA subgroup H of G is normal iff gHg^{-1}=H for all g.",
    )))
    assert r.decision == "drafted"
    assert r.writes_stub
    assert r.proposed_id == "algebra.normal_subgroup"


def test_stub_decoder_placeholder_only_round_trips() -> None:
    r = parse_def_stub(json.dumps(_stub_payload()))
    assert r.decision == "placeholder_only"
    assert "TODO" in r.body


def test_stub_decoder_drafted_without_id_rejected() -> None:
    p = _stub_payload(decision="drafted"); p["proposed_id"] = ""
    with pytest.raises(DefStubReviewParseError, match="drafted_requires_proposed_id"):
        parse_def_stub(json.dumps(p))


def test_stub_decoder_drafted_without_body_rejected() -> None:
    p = _stub_payload(decision="drafted"); p["body"] = ""
    with pytest.raises(DefStubReviewParseError, match="drafted_requires_body"):
        parse_def_stub(json.dumps(p))


def test_stub_decoder_cannot_stub_requires_blocker() -> None:
    with pytest.raises(DefStubReviewParseError, match="cannot_stub_requires_blocker"):
        parse_def_stub(json.dumps({"decision": "cannot_stub", "rationale": "x"}))


# ===========================================================================
# def-stub-generator role
# ===========================================================================
def test_stub_role_runs_with_missing_id(kb: Path) -> None:
    adapter = KbAdapter(kb)
    backend = MockBackend(canned_response=json.dumps(_stub_payload()))
    stubber = DefStubGenerator(backend=backend)
    r = stubber.run(
        "algebra.normal_subgroup", "algebra.quotient_group",
        adapter, reason="needed to define the quotient correctly",
    )
    assert r.decision == "placeholder_only"
    assert "algebra.normal_subgroup" in backend.last_call["prompt"]
    assert "algebra.quotient_group" in backend.last_call["prompt"]


# ===========================================================================
# promote-request primitive
# ===========================================================================
def test_promote_request_creates_staged_node(kb: Path, tmp_path: Path) -> None:
    requests_dir = kb / "docs" / "knowledge" / "requests"
    requests_dir.mkdir(parents=True)
    request_path = requests_dir / "algebra_quotient_group__new-lemma__001.md"
    request_path.write_text(textwrap.dedent("""\
        ---
        agent: proof-gap-filler
        kind: new-lemma
        target:
          node_id: algebra.quotient_group
        proposed_id: algebra.helper_lemma
        statement: For every group G, |G/H| = [G:H].
        rationale: Needed for step 3 of the quotient proof.
        ---

        Auto-generated request.
        """))

    rc, out, _ = _run([
        "promote-request", str(request_path),
        "--blueprint", str(kb),
    ])
    assert rc == cli.EXIT_OK
    staged_path = Path(out.strip())
    assert staged_path.exists()
    # Frontmatter is well-formed (mdblueprint validated)
    text = staged_path.read_text()
    assert "id: algebra.helper_lemma" in text
    assert "kind: lemma" in text
    assert "status: staged" in text
    assert "[G:H]" in text  # statement carried over
    # Request was moved to processed/
    assert not request_path.exists()
    assert (requests_dir / "processed" / request_path.name).exists()


def test_promote_request_keep_flag_leaves_request_in_place(kb: Path) -> None:
    requests_dir = kb / "docs" / "knowledge" / "requests"
    requests_dir.mkdir(parents=True)
    request_path = requests_dir / "test__new-lemma__001.md"
    request_path.write_text(textwrap.dedent("""\
        ---
        agent: proof-gap-filler
        kind: new-lemma
        proposed_id: algebra.keep_me
        statement: Test.
        ---
        body
        """))
    rc, _, _ = _run([
        "promote-request", str(request_path),
        "--blueprint", str(kb), "--keep",
    ])
    assert rc == cli.EXIT_OK
    # Original still in place
    assert request_path.exists()
    # processed/ not created (or empty)
    assert not (requests_dir / "processed" / request_path.name).exists()


def test_promote_request_all_pending_batches(kb: Path) -> None:
    requests_dir = kb / "docs" / "knowledge" / "requests"
    requests_dir.mkdir(parents=True)
    for i in range(3):
        (requests_dir / f"req__new-lemma__{i:03}.md").write_text(textwrap.dedent(f"""\
            ---
            agent: proof-gap-filler
            kind: new-lemma
            proposed_id: algebra.lemma_{i}
            statement: Statement {i}.
            ---
            """))
    rc, out, _ = _run([
        "promote-request", "--all-pending", "--blueprint", str(kb),
    ])
    assert rc == cli.EXIT_OK
    promoted_paths = [line for line in out.strip().split("\n") if line.strip()]
    assert len(promoted_paths) == 3
    # All three originals now in processed/
    assert len(list((requests_dir / "processed").glob("*.md"))) == 3


def test_promote_request_missing_proposed_id_rejected(kb: Path) -> None:
    requests_dir = kb / "docs" / "knowledge" / "requests"
    requests_dir.mkdir(parents=True)
    bad = requests_dir / "bad.md"
    bad.write_text(textwrap.dedent("""\
        ---
        agent: x
        kind: new-lemma
        statement: ok
        ---
        """))
    rc, _, err = _run([
        "promote-request", str(bad), "--blueprint", str(kb),
    ])
    assert rc == cli.EXIT_RUNTIME
    assert "proposed_id" in err


def test_promote_request_both_path_and_all_pending_rejected(kb: Path) -> None:
    rc, _, err = _run([
        "promote-request", "/some/path", "--all-pending",
        "--blueprint", str(kb),
    ])
    assert rc == cli.EXIT_USAGE
    assert "either" in err


def test_promote_request_neither_path_nor_all_rejected(kb: Path) -> None:
    rc, _, err = _run(["promote-request", "--blueprint", str(kb)])
    assert rc == cli.EXIT_USAGE
    assert "either" in err


# ===========================================================================
# Mode B CLI smoke
# ===========================================================================
def test_fix_stmt_cli_runs_via_mock_backend(kb: Path) -> None:
    register_backend(MockBackend(name="codex", canned_response=json.dumps({
        "decision": "fixed",
        "rationale": "added missing quantifier",
        "fixed_body": "# Quotient Group\n\nNew correct body with $\\forall G$.",
        "addressed_issues": ["quantifier drift"],
    })))
    rc, out, _ = _run([
        "fix-stmt", "algebra.quotient_group",
        "--blueprint", str(kb), "--no-write-review",
    ])
    assert rc == cli.EXIT_OK
    payload = json.loads(out)
    assert payload["decision"] == "fixed"


def test_fix_stmt_cli_applies_body_to_staged_node(kb: Path) -> None:
    register_backend(MockBackend(name="codex", canned_response=json.dumps({
        "decision": "fixed", "rationale": "patched",
        "fixed_body": "# Quotient Group\n\nFRESH BODY with $\\forall$.",
        "addressed_issues": ["x"],
    })))
    _run(["fix-stmt", "algebra.quotient_group",
          "--blueprint", str(kb), "--no-write-review"])
    node_path = kb / "docs" / "knowledge" / "staged" / "algebra" / "quotient_group.md"
    text = node_path.read_text()
    assert "FRESH BODY" in text
    assert "id: algebra.quotient_group" in text  # frontmatter preserved


def test_stub_def_cli_writes_staged_node(kb: Path) -> None:
    register_backend(MockBackend(name="codex", canned_response=json.dumps(_stub_payload())))
    rc, out, _ = _run([
        "stub-def", "algebra.normal_subgroup",
        "--referring-node", "algebra.quotient_group",
        "--blueprint", str(kb),
    ])
    assert rc == cli.EXIT_OK
    payload = json.loads(out)
    assert payload["decision"] == "placeholder_only"
    assert payload["applied_staged_node"] is not None
    staged = Path(payload["applied_staged_node"])
    assert staged.exists()
    text = staged.read_text()
    assert "id: algebra.normal_subgroup" in text
    assert "TODO" in text
