"""KbAdapter tests (issue #6).

Tests stand up an inline KB fixture under ``tmp_path`` rather than
depending on mdblueprint's bundled examples — keeps the test
self-contained and immune to upstream fixture reshuffles.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from rethlas_kb.adapter import ContextBundle, KbAdapter, Node, _compose_markdown


# ---------------------------------------------------------------------------
# Fixture — a tiny but realistic two-topic KB
# ---------------------------------------------------------------------------
ADMITTED_GROUP_MD = textwrap.dedent("""\
    ---
    id: algebra.group
    title: Group
    kind: definition
    status: admitted
    uses: []
    primary_topic: algebra
    topics:
      - algebra
    ---

    # Group

    A group is a set with an associative binary operation, an identity,
    and inverses.
    """)

ADMITTED_HOM_MD = textwrap.dedent("""\
    ---
    id: algebra.group_homomorphism
    title: Group Homomorphism
    kind: definition
    status: admitted
    uses:
      - algebra.group
    primary_topic: algebra
    topics:
      - algebra
    ---

    # Group Homomorphism
    """)

STAGED_QUOTIENT_MD = textwrap.dedent("""\
    ---
    id: algebra.quotient_group
    title: Quotient Group
    kind: definition
    status: staged
    uses:
      - algebra.group
    primary_topic: algebra
    topics:
      - algebra
    ---

    # Quotient Group

    Let $G$ be a group and $N$ a normal subgroup; $G/N$ is the set of cosets.
    """)

STAGED_OTHER_TOPIC_MD = textwrap.dedent("""\
    ---
    id: analysis.continuous
    title: Continuous Function
    kind: definition
    status: staged
    uses: []
    primary_topic: analysis
    topics:
      - analysis
    ---

    # Continuous

    A function is continuous when small input changes produce small output changes.
    """)


@pytest.fixture
def kb(tmp_path: Path) -> KbAdapter:
    """Build a minimal but real KB under tmp_path and return an adapter."""
    knowledge = tmp_path / "docs" / "knowledge"
    (knowledge / "nodes" / "algebra").mkdir(parents=True)
    (knowledge / "staged" / "algebra").mkdir(parents=True)
    (knowledge / "staged" / "analysis").mkdir(parents=True)

    (knowledge / "nodes" / "algebra" / "group.md").write_text(ADMITTED_GROUP_MD)
    (knowledge / "nodes" / "algebra" / "group_homomorphism.md").write_text(
        ADMITTED_HOM_MD
    )
    (knowledge / "staged" / "algebra" / "quotient_group.md").write_text(
        STAGED_QUOTIENT_MD
    )
    (knowledge / "staged" / "analysis" / "continuous.md").write_text(
        STAGED_OTHER_TOPIC_MD
    )
    return KbAdapter(tmp_path)


# ---------------------------------------------------------------------------
# Init + paths
# ---------------------------------------------------------------------------
def test_paths_resolved_under_kb_root(tmp_path: Path) -> None:
    a = KbAdapter(tmp_path)
    assert a.kb_root == tmp_path.resolve()
    assert a.knowledge_dir == tmp_path.resolve() / "docs" / "knowledge"
    assert a.nodes_dir.name == "nodes"
    assert a.staged_dir.name == "staged"
    assert a.reviews_dir.name == "reviews"
    assert a.requests_dir.name == "requests"
    assert a.sources_dir.name == "sources"


def test_paths_accept_string_kb_root(tmp_path: Path) -> None:
    a = KbAdapter(str(tmp_path))
    assert a.kb_root == tmp_path.resolve()


# ---------------------------------------------------------------------------
# read_node
# ---------------------------------------------------------------------------
def test_read_node_finds_admitted(kb: KbAdapter) -> None:
    node = kb.read_node("algebra.group")
    assert isinstance(node, Node)
    assert node.id == "algebra.group"
    assert node.status == "admitted"


def test_read_node_finds_staged(kb: KbAdapter) -> None:
    node = kb.read_node("algebra.quotient_group")
    assert node.status == "staged"
    assert "algebra.group" in node.uses


def test_read_node_raises_keyerror_for_missing(kb: KbAdapter) -> None:
    with pytest.raises(KeyError, match="not found"):
        kb.read_node("algebra.nonexistent")


# ---------------------------------------------------------------------------
# list_admitted / list_staged / list_staged_by_topic
# ---------------------------------------------------------------------------
def test_list_admitted_returns_only_admitted_nodes(kb: KbAdapter) -> None:
    admitted = kb.list_admitted()
    ids = {n.id for n in admitted}
    assert ids == {"algebra.group", "algebra.group_homomorphism"}
    assert all(n.status == "admitted" for n in admitted)


def test_list_staged_returns_only_staged_nodes(kb: KbAdapter) -> None:
    staged = kb.list_staged()
    ids = {n.id for n in staged}
    assert ids == {"algebra.quotient_group", "analysis.continuous"}
    assert all(n.status == "staged" for n in staged)


def test_list_staged_by_topic_filters_correctly(kb: KbAdapter) -> None:
    algebra = kb.list_staged_by_topic("algebra")
    analysis = kb.list_staged_by_topic("analysis")
    assert {n.id for n in algebra} == {"algebra.quotient_group"}
    assert {n.id for n in analysis} == {"analysis.continuous"}


def test_list_staged_by_unknown_topic_returns_empty(kb: KbAdapter) -> None:
    assert kb.list_staged_by_topic("topology") == []


def test_list_admitted_on_missing_dir_returns_empty(tmp_path: Path) -> None:
    # No docs/knowledge/* created at all
    assert KbAdapter(tmp_path).list_admitted() == []


def test_list_staged_on_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert KbAdapter(tmp_path).list_staged() == []


# ---------------------------------------------------------------------------
# write_review
# ---------------------------------------------------------------------------
def test_write_review_creates_file_with_managed_frontmatter(kb: KbAdapter) -> None:
    path = kb.write_review(
        node_id="algebra.quotient_group",
        agent_name="statement-verifier",
        review={"decision": "accepted", "confidence": 0.9, "body": "Looks good."},
    )
    assert path.exists()
    assert path.parent == kb.reviews_dir
    assert "algebra_quotient_group" in path.name
    assert "statement-verifier" in path.name
    assert path.suffix == ".md"

    text = path.read_text()
    assert text.startswith("---\n")
    fm_block = text.split("---")[1]
    fm = yaml.safe_load(fm_block)
    assert fm["agent"] == "statement-verifier"
    assert fm["kind"] == "review"
    assert fm["target"] == {"node_id": "algebra.quotient_group"}
    assert fm["decision"] == "accepted"
    assert fm["confidence"] == 0.9
    assert "created_at" in fm
    assert "Looks good." in text


def test_write_review_creates_directory_if_missing(tmp_path: Path) -> None:
    kb = KbAdapter(tmp_path)
    assert not kb.reviews_dir.exists()
    kb.write_review(node_id="x.y", agent_name="a", review={})
    assert kb.reviews_dir.exists()


def test_write_review_does_not_overwrite_managed_fields(kb: KbAdapter) -> None:
    """Caller can't smuggle a fake agent/kind into managed frontmatter."""
    path = kb.write_review(
        node_id="algebra.group",
        agent_name="real-agent",
        review={"agent": "FAKE", "kind": "FAKE", "target": "FAKE",
                "decision": "accepted"},
    )
    fm = yaml.safe_load(path.read_text().split("---")[1])
    assert fm["agent"] == "real-agent"
    assert fm["kind"] == "review"
    assert fm["target"] == {"node_id": "algebra.group"}


# ---------------------------------------------------------------------------
# write_request
# ---------------------------------------------------------------------------
def test_write_request_creates_file_with_request_kind(kb: KbAdapter) -> None:
    path = kb.write_request(
        node_id="algebra.quotient_group",
        request_kind="missing-dependency",
        payload={"missing": ["algebra.normal_subgroup"], "body": "Need normal subgroup."},
    )
    assert path.exists()
    assert path.parent == kb.requests_dir

    fm = yaml.safe_load(path.read_text().split("---")[1])
    assert fm["kind"] == "missing-dependency"
    assert fm["missing"] == ["algebra.normal_subgroup"]
    assert "Need normal subgroup." in path.read_text()


# ---------------------------------------------------------------------------
# write_staged_node — frontmatter validation
# ---------------------------------------------------------------------------
def test_write_staged_node_happy_path(kb: KbAdapter) -> None:
    path = kb.write_staged_node(
        frontmatter={
            "id": "algebra.new_thing",
            "title": "New Thing",
            "kind": "definition",
            "status": "staged",
            "uses": [],
            "primary_topic": "algebra",
            "topics": ["algebra"],
        },
        body="# New Thing\n\nA placeholder definition.",
    )
    assert path.exists()
    assert path.parent == kb.staged_dir / "algebra"
    assert path.name == "new_thing.md"

    # Round-trip: the adapter should be able to read it back
    node = kb.read_node("algebra.new_thing")
    assert node.kind == "definition"
    assert node.status == "staged"


def test_write_staged_node_rejects_missing_required_field(kb: KbAdapter) -> None:
    with pytest.raises(ValueError, match="validation failed"):
        kb.write_staged_node(
            frontmatter={
                # missing: id
                "title": "Anon",
                "kind": "definition",
                "status": "staged",
            },
        )


def test_write_staged_node_rejects_invalid_kind(kb: KbAdapter) -> None:
    with pytest.raises(ValueError, match="validation failed"):
        kb.write_staged_node(
            frontmatter={
                "id": "algebra.bad",
                "title": "Bad",
                "kind": "not-a-real-kind",
                "status": "staged",
            },
        )


def test_write_staged_node_rejects_admitted_status_in_staged_dir(
    kb: KbAdapter,
) -> None:
    """status:admitted under staged/ is a directory-consistency error."""
    with pytest.raises(ValueError, match="validation failed"):
        kb.write_staged_node(
            frontmatter={
                "id": "algebra.wrong_dir",
                "title": "Wrong",
                "kind": "definition",
                "status": "admitted",  # not allowed under staged/
            },
        )


def test_write_staged_node_honours_explicit_filename(kb: KbAdapter) -> None:
    path = kb.write_staged_node(
        frontmatter={
            "id": "algebra.alt",
            "title": "Alt",
            "kind": "definition",
            "status": "staged",
            "primary_topic": "algebra",
        },
        body="body",
        filename="alt_explicit.md",
    )
    assert path.name == "alt_explicit.md"


# ---------------------------------------------------------------------------
# context_pack
# ---------------------------------------------------------------------------
def test_context_pack_target_id_returns_bundle(kb: KbAdapter) -> None:
    bundle = kb.context_pack(target_id="algebra.group_homomorphism")
    assert isinstance(bundle, ContextBundle)
    assert bundle.target_id == "algebra.group_homomorphism"
    assert bundle.topic is None
    assert bundle.mode == "admitted"
    ids = {n["id"] for n in bundle.nodes}
    # Target + its closure (uses algebra.group)
    assert "algebra.group_homomorphism" in ids
    assert "algebra.group" in ids


def test_context_pack_include_staged_flips_mode(kb: KbAdapter) -> None:
    bundle = kb.context_pack(
        target_id="algebra.quotient_group",
        include_staged=True,
    )
    assert bundle.mode == "admitted+staged"
    # Raw dict still carries the full upstream payload
    assert "allowed_inputs" in bundle.raw
    assert "answer_contract" in bundle.raw


def test_context_pack_requires_target_or_topic(kb: KbAdapter) -> None:
    with pytest.raises(ValueError):
        kb.context_pack()  # neither target nor topic


# ---------------------------------------------------------------------------
# _compose_markdown helper
# ---------------------------------------------------------------------------
def test_compose_markdown_with_body() -> None:
    out = _compose_markdown({"id": "x.y", "kind": "definition"}, "hello world")
    assert out.startswith("---\n")
    assert "id: x.y" in out
    assert out.rstrip("\n").endswith("hello world")


def test_compose_markdown_without_body() -> None:
    out = _compose_markdown({"id": "x.y"}, "")
    assert out.endswith("---\n")
    assert "x.y" in out
