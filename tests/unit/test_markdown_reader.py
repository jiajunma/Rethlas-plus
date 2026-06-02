"""Markdown KB read library — inverse of ``librarian.renderer.render_node``.

These tests pin the read side of the markdown KB (§13): markdown carries only
the *math* (label/kind/statement/proof/remark/source_note/pass_count/hashes/
depends_on). Operational fields (repair_count, verification_report, repair_hint,
introduced_by_actor) are NOT in markdown — they come from the events-derived
projection — so the reader returns them at their defaults.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from common.kb.markdown_reader import (
    parse_node_markdown,
    read_node_file,
    read_nodes_dir,
    query_verified,
    list_staged,
)
from common.kb.types import Node, NodeKind
from librarian.renderer import render_node, write_node_file


def _make_node(**overrides) -> Node:
    base = {
        "label": "lem:foo",
        "kind": NodeKind.LEMMA,
        "statement": "A statement.",
        "proof": "A proof. $\\square$",
        "remark": "",
        "source_note": "",
        "pass_count": 2,
        "repair_count": 0,
        "statement_hash": "a" * 64,
        "verification_hash": "b" * 64,
        "depends_on": ("def:x",),
    }
    base.update(overrides)
    return Node(**base)


# Math fields preserved by a render -> parse round-trip.
_MATH_FIELDS = (
    "label",
    "kind",
    "statement",
    "proof",
    "remark",
    "source_note",
    "pass_count",
    "statement_hash",
    "verification_hash",
)


def test_round_trip_preserves_math_fields() -> None:
    node = _make_node(remark="A remark.", source_note="Adapted from X.")
    parsed = parse_node_markdown(render_node(node))
    for field in _MATH_FIELDS:
        assert getattr(parsed, field) == getattr(node, field), field
    assert parsed.depends_on == ("def:x",)


def test_round_trip_empty_proof_and_optional_sections() -> None:
    node = _make_node(kind=NodeKind.DEFINITION, label="def:x", proof="", remark="", source_note="")
    parsed = parse_node_markdown(render_node(node))
    assert parsed.label == "def:x"
    assert parsed.kind is NodeKind.DEFINITION
    assert parsed.proof == ""
    assert parsed.remark == ""
    assert parsed.source_note == ""
    assert parsed.statement == "A statement."


def test_depends_on_sorted_deduped_round_trip() -> None:
    node = _make_node(depends_on=("def:z", "def:x", "def:x", "def:a"))
    parsed = parse_node_markdown(render_node(node))
    assert parsed.depends_on == ("def:a", "def:x", "def:z")


def test_operational_fields_default_when_absent() -> None:
    parsed = parse_node_markdown(render_node(_make_node()))
    assert parsed.repair_count == 0
    assert parsed.verification_report == ""
    assert parsed.repair_hint == ""
    assert parsed.introduced_by_actor == "user:cli"


def test_missing_fence_raises() -> None:
    from common.kb.markdown_reader import MarkdownParseError

    with pytest.raises(MarkdownParseError):
        parse_node_markdown("no frontmatter here")


def test_read_node_file_and_dir(tmp_path: Path) -> None:
    nodes_dir = tmp_path / "nodes"
    write_node_file(nodes_dir, _make_node(label="lem:foo", pass_count=3))
    write_node_file(nodes_dir, _make_node(label="def:x", kind=NodeKind.DEFINITION, proof="", pass_count=0))

    foo = read_node_file(nodes_dir / "lem_foo.md")
    assert foo.label == "lem:foo" and foo.pass_count == 3

    by_label = read_nodes_dir(nodes_dir)
    assert set(by_label) == {"lem:foo", "def:x"}

    verified = {n.label for n in query_verified(nodes_dir)}
    staged = {n.label for n in list_staged(nodes_dir)}
    assert verified == {"lem:foo"}      # pass_count >= 1
    assert staged == {"def:x"}          # pass_count <= 0


def test_reads_real_manual_run_node() -> None:
    repo = Path(__file__).resolve().parents[2]
    f = (
        repo
        / "tests/manual_runs/real_induced_orbit_problem_B_general_reverify_20260504"
        / "knowledge_base/nodes/lem_block_form_for_x0_plus_u.md"
    )
    if not f.exists():
        pytest.skip("manual-run fixture not present")
    node = read_node_file(f)
    assert node.label == "lem:block_form_for_x0_plus_u"
    assert node.kind is NodeKind.LEMMA
    assert node.pass_count == 3
    assert len(node.proof) > 0
