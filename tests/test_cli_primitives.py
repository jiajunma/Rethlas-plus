"""CLI primitive subcommand tests (issue #19).

Mode A primitives — every command is a thin shell over ``KbAdapter``,
so the tests mostly exercise: argument parsing, stdin handling,
output format flags, and exit codes. We never spawn an LLM here.
"""

from __future__ import annotations

import io
import json
import textwrap
from pathlib import Path

import pytest
import yaml

from rethlas_kb import cli


# ---------------------------------------------------------------------------
# Inline KB fixture
# ---------------------------------------------------------------------------
GROUP_MD = textwrap.dedent("""\
    ---
    id: algebra.group
    title: Group
    kind: definition
    status: admitted
    uses: []
    primary_topic: algebra
    topics: [algebra]
    tags: [foundational]
    ---

    # Group

    A set with an associative multiplication, an identity, and inverses.
    """)

HOM_MD = textwrap.dedent("""\
    ---
    id: algebra.group_homomorphism
    title: Group Homomorphism
    kind: definition
    status: admitted
    uses:
      - algebra.group
    primary_topic: algebra
    topics: [algebra]
    ---

    # Group Homomorphism

    A map preserving the group operation.
    """)

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

    Cosets of a normal subgroup.
    """)

CONTINUOUS_MD = textwrap.dedent("""\
    ---
    id: analysis.continuous
    title: Continuous Function
    kind: definition
    status: staged
    uses: []
    primary_topic: analysis
    topics: [analysis]
    ---

    # Continuous

    Small input changes give small output changes.
    """)


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    knowledge = tmp_path / "docs" / "knowledge"
    (knowledge / "nodes" / "algebra").mkdir(parents=True)
    (knowledge / "staged" / "algebra").mkdir(parents=True)
    (knowledge / "staged" / "analysis").mkdir(parents=True)
    (knowledge / "nodes" / "algebra" / "group.md").write_text(GROUP_MD)
    (knowledge / "nodes" / "algebra" / "group_homomorphism.md").write_text(HOM_MD)
    (knowledge / "staged" / "algebra" / "quotient_group.md").write_text(QUOTIENT_MD)
    (knowledge / "staged" / "analysis" / "continuous.md").write_text(CONTINUOUS_MD)
    return tmp_path


# ---------------------------------------------------------------------------
# Helper: invoke the CLI and capture stdout/stderr separately
# ---------------------------------------------------------------------------
def _run(argv: list[str], *, stdin: str = "") -> tuple[int, str, str]:
    """Run cli.main with stdin/stdout/stderr captured. Returns (rc, out, err)."""
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


# ---------------------------------------------------------------------------
# get-node
# ---------------------------------------------------------------------------
def test_get_node_text_format_prints_raw_markdown(kb: Path) -> None:
    rc, out, _ = _run(["get-node", "algebra.group", "--project", str(kb)])
    assert rc == cli.EXIT_OK
    assert out.startswith("---\nid: algebra.group")
    assert "associative multiplication" in out


def test_get_node_json_format_returns_parseable_dict(kb: Path) -> None:
    rc, out, _ = _run(["get-node", "algebra.group", "--project", str(kb),
                       "--format", "json"])
    assert rc == cli.EXIT_OK
    payload = json.loads(out)
    assert payload["id"] == "algebra.group"
    assert payload["kind"] == "definition"
    assert payload["status"] == "admitted"
    assert "associative" in payload["body"]


def test_get_node_frontmatter_format_returns_yaml(kb: Path) -> None:
    rc, out, _ = _run(["get-node", "algebra.group", "--project", str(kb),
                       "--format", "frontmatter"])
    assert rc == cli.EXIT_OK
    fm = yaml.safe_load(out)
    assert fm["id"] == "algebra.group"
    assert "body" not in fm  # frontmatter only


def test_get_node_finds_staged_too(kb: Path) -> None:
    rc, out, _ = _run(["get-node", "algebra.quotient_group", "--project", str(kb)])
    assert rc == cli.EXIT_OK
    assert "Cosets of a normal subgroup" in out


def test_get_node_missing_returns_runtime(kb: Path) -> None:
    rc, _, err = _run(["get-node", "algebra.does_not_exist",
                       "--project", str(kb)])
    assert rc == cli.EXIT_RUNTIME
    assert "not found" in err


def test_get_node_bad_project_returns_usage(tmp_path: Path) -> None:
    bare = tmp_path / "not-a-blueprint"
    bare.mkdir()
    rc, _, err = _run(["get-node", "x.y", "--project", str(bare)])
    assert rc == cli.EXIT_USAGE
    assert "docs/knowledge" in err


# ---------------------------------------------------------------------------
# get-context
# ---------------------------------------------------------------------------
def test_get_context_default_renders_markdown(kb: Path) -> None:
    rc, out, _ = _run(["get-context", "algebra.group_homomorphism",
                       "--project", str(kb)])
    assert rc == cli.EXIT_OK
    assert "## Context" in out
    # Closure includes algebra.group (a predecessor of homomorphism)
    assert "algebra.group" in out


def test_get_context_json_returns_raw_dict(kb: Path) -> None:
    rc, out, _ = _run(["get-context", "algebra.group_homomorphism",
                       "--project", str(kb), "--json"])
    assert rc == cli.EXIT_OK
    payload = json.loads(out)
    assert payload["target_id"] == "algebra.group_homomorphism"
    assert payload["mode"] == "admitted+staged"
    assert "nodes" in payload
    assert "answer_contract" in payload


def test_get_context_no_staged_flips_mode(kb: Path) -> None:
    rc, out, _ = _run(["get-context", "algebra.group_homomorphism",
                       "--project", str(kb), "--no-staged", "--json"])
    assert rc == cli.EXIT_OK
    payload = json.loads(out)
    assert payload["mode"] == "admitted"


# ---------------------------------------------------------------------------
# compose-prompt
# ---------------------------------------------------------------------------
def test_compose_prompt_statement_verifier_full_prompt(kb: Path) -> None:
    rc, out, _ = _run(["compose-prompt", "statement-verifier",
                       "algebra.quotient_group", "--project", str(kb)])
    assert rc == cli.EXIT_OK
    assert "statement-verifier" in out
    assert "## Target node" in out
    assert "algebra.quotient_group" in out
    assert "## Context" in out
    assert "Output contract" in out
    # The four decision tokens are spelled out
    for decision in ("accepted", "needs_definition",
                     "generality_concern", "formulation_issue"):
        assert decision in out


def test_compose_prompt_unknown_role_rejected_by_argparse(kb: Path) -> None:
    # argparse choices=... causes a SystemExit(2) on unknown role
    with pytest.raises(SystemExit) as exc_info:
        _run(["compose-prompt", "bogus-role", "algebra.group",
              "--project", str(kb)])
    assert exc_info.value.code == cli.EXIT_USAGE


# ---------------------------------------------------------------------------
# list-staged / list-admitted
# ---------------------------------------------------------------------------
def test_list_staged_text_format(kb: Path) -> None:
    rc, out, _ = _run(["list-staged", "--project", str(kb)])
    assert rc == cli.EXIT_OK
    assert "algebra.quotient_group" in out
    assert "analysis.continuous" in out
    # Tab-delimited "id\tkind\tstatus\ttitle"
    for line in out.strip().split("\n"):
        assert line.count("\t") == 3


def test_list_staged_json_format(kb: Path) -> None:
    rc, out, _ = _run(["list-staged", "--project", str(kb), "--json"])
    assert rc == cli.EXIT_OK
    payload = json.loads(out)
    ids = {n["id"] for n in payload}
    assert ids == {"algebra.quotient_group", "analysis.continuous"}


def test_list_staged_topic_filter(kb: Path) -> None:
    rc, out, _ = _run(["list-staged", "--project", str(kb),
                       "--topic", "algebra", "--json"])
    payload = json.loads(out)
    ids = {n["id"] for n in payload}
    assert ids == {"algebra.quotient_group"}


def test_list_admitted_basic(kb: Path) -> None:
    rc, out, _ = _run(["list-admitted", "--project", str(kb), "--json"])
    assert rc == cli.EXIT_OK
    ids = {n["id"] for n in json.loads(out)}
    assert ids == {"algebra.group", "algebra.group_homomorphism"}


def test_list_admitted_topic_filter(kb: Path) -> None:
    rc, out, _ = _run(["list-admitted", "--project", str(kb),
                       "--topic", "algebra", "--json"])
    payload = json.loads(out)
    assert {n["id"] for n in payload} == {
        "algebra.group", "algebra.group_homomorphism",
    }


# ---------------------------------------------------------------------------
# write-review
# ---------------------------------------------------------------------------
def test_write_review_minimal(kb: Path) -> None:
    rc, out, _ = _run([
        "write-review", "algebra.quotient_group",
        "--project", str(kb),
        "--agent", "statement-verifier",
        "--decision", "accepted",
        "--rationale", "Looks fine.",
    ])
    assert rc == cli.EXIT_OK
    path = Path(out.strip())
    assert path.exists()
    text = path.read_text()
    assert "decision: accepted" in text
    assert "agent: statement-verifier" in text
    assert "Looks fine." in text  # in rationale field


def test_write_review_with_confidence_and_csv_fields(kb: Path) -> None:
    rc, out, _ = _run([
        "write-review", "algebra.quotient_group",
        "--project", str(kb),
        "--agent", "statement-verifier",
        "--decision", "needs_definition",
        "--rationale", "Uses normal subgroup without admitted definition.",
        "--confidence", "0.85",
        "--missing-definitions", "algebra.normal_subgroup, algebra.coset",
    ])
    assert rc == cli.EXIT_OK
    path = Path(out.strip())
    fm = yaml.safe_load(path.read_text().split("---")[1])
    assert fm["confidence"] == 0.85
    assert fm["missing_definitions"] == ["algebra.normal_subgroup", "algebra.coset"]


def test_write_review_raw_from_stdin_goes_into_body(kb: Path) -> None:
    rc, out, _ = _run(
        [
            "write-review", "algebra.quotient_group",
            "--project", str(kb),
            "--agent", "statement-verifier",
            "--decision", "accepted",
            "--rationale", "ok",
            "--raw", "-",
        ],
        stdin="multi\nline\nfull LLM\nreasoning trace\n",
    )
    assert rc == cli.EXIT_OK
    path = Path(out.strip())
    text = path.read_text()
    assert "Raw LLM output" in text
    assert "multi\nline\nfull LLM\nreasoning trace" in text


def test_write_review_raw_from_file_path(kb: Path, tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.txt"
    raw_path.write_text("full reasoning here")
    rc, out, _ = _run([
        "write-review", "algebra.quotient_group",
        "--project", str(kb),
        "--agent", "statement-verifier",
        "--decision", "accepted",
        "--rationale", "ok",
        "--raw", str(raw_path),
    ])
    assert rc == cli.EXIT_OK
    text = Path(out.strip()).read_text()
    assert "full reasoning here" in text


def test_write_review_missing_required_flag(kb: Path) -> None:
    with pytest.raises(SystemExit):
        _run([
            "write-review", "algebra.quotient_group",
            "--project", str(kb),
            "--agent", "statement-verifier",
            "--decision", "accepted",
            # missing --rationale
        ])


# ---------------------------------------------------------------------------
# write-request
# ---------------------------------------------------------------------------
def test_write_request_with_payload_and_body(kb: Path) -> None:
    payload = json.dumps({"missing": ["algebra.normal_subgroup"]})
    rc, out, _ = _run(
        [
            "write-request", "algebra.quotient_group",
            "--project", str(kb),
            "--kind", "missing-dependency",
            "--payload", "-",
        ],
        stdin=payload,
    )
    assert rc == cli.EXIT_OK
    path = Path(out.strip())
    text = path.read_text()
    fm = yaml.safe_load(text.split("---")[1])
    assert fm["kind"] == "missing-dependency"
    assert fm["missing"] == ["algebra.normal_subgroup"]


def test_write_request_payload_not_object_rejected(kb: Path) -> None:
    rc, _, err = _run(
        [
            "write-request", "algebra.quotient_group",
            "--project", str(kb),
            "--kind", "x",
            "--payload", "-",
        ],
        stdin="[1, 2, 3]",  # array, not object
    )
    assert rc == cli.EXIT_USAGE
    assert "JSON must be an object" in err


def test_write_request_invalid_json_payload(kb: Path) -> None:
    rc, _, err = _run(
        [
            "write-request", "algebra.quotient_group",
            "--project", str(kb),
            "--kind", "x",
            "--payload", "-",
        ],
        stdin="not json at all",
    )
    assert rc == cli.EXIT_USAGE
    assert "not valid JSON" in err


# ---------------------------------------------------------------------------
# write-staged-node
# ---------------------------------------------------------------------------
NEW_STAGED_MD = textwrap.dedent("""\
    ---
    id: algebra.commutator
    title: Commutator
    kind: definition
    status: staged
    uses:
      - algebra.group
    primary_topic: algebra
    topics: [algebra]
    ---

    # Commutator

    The commutator [a, b] = aba^{-1}b^{-1}.
    """)


def test_write_staged_node_happy_path(kb: Path) -> None:
    rc, out, _ = _run(
        ["write-staged-node", "--project", str(kb), "--from-file", "-"],
        stdin=NEW_STAGED_MD,
    )
    assert rc == cli.EXIT_OK
    path = Path(out.strip())
    assert path.exists()
    assert path.parent.name == "algebra"
    assert "commutator" in path.name


def test_write_staged_node_rejects_invalid_frontmatter(kb: Path) -> None:
    bad = textwrap.dedent("""\
        ---
        title: Anonymous
        kind: definition
        status: staged
        ---

        body
        """)
    rc, _, err = _run(
        ["write-staged-node", "--project", str(kb), "--from-file", "-"],
        stdin=bad,
    )
    assert rc == cli.EXIT_RUNTIME
    assert "validation failed" in err


def test_write_staged_node_no_frontmatter_returns_usage(kb: Path) -> None:
    rc, _, err = _run(
        ["write-staged-node", "--project", str(kb), "--from-file", "-"],
        stdin="just a body, no frontmatter\n",
    )
    assert rc == cli.EXIT_USAGE
    assert "missing YAML frontmatter" in err


def test_write_staged_node_from_file_path(kb: Path, tmp_path: Path) -> None:
    src = tmp_path / "new.md"
    src.write_text(NEW_STAGED_MD)
    rc, out, _ = _run([
        "write-staged-node", "--project", str(kb),
        "--from-file", str(src),
    ])
    assert rc == cli.EXIT_OK


# ---------------------------------------------------------------------------
# validate-frontmatter
# ---------------------------------------------------------------------------
def test_validate_frontmatter_ok(kb: Path) -> None:
    rc, out, _ = _run(
        ["validate-frontmatter", "--from-file", "-", "--staged"],
        stdin=NEW_STAGED_MD,
    )
    assert rc == cli.EXIT_OK
    assert out.strip() == "ok"


def test_validate_frontmatter_reports_errors(kb: Path) -> None:
    bad = textwrap.dedent("""\
        ---
        id: x.y
        title: X
        kind: bogus-kind
        status: staged
        ---

        body
        """)
    rc, out, _ = _run(
        ["validate-frontmatter", "--from-file", "-", "--staged"],
        stdin=bad,
    )
    assert rc == cli.EXIT_RUNTIME
    assert "invalid kind" in out
