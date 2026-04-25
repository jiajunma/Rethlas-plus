"""M4 — node renderer byte-determinism + §4.2 contract.

These tests pin the rendering contract that ``startup_reconciliation``
and the linter ``E`` audit rely on. Without byte-determinism every
restart would flap "drifted nodes/" alerts.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from common.kb.types import Node, NodeKind
from librarian.renderer import node_filename, render_node, write_node_file


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


def test_render_is_byte_deterministic() -> None:
    node = _make_node(depends_on=("def:x", "lem:c", "lem:b"))
    a = render_node(node)
    b = render_node(node)
    assert a == b, "two render calls on same Node must produce identical bytes"


def test_render_yaml_key_order_fixed() -> None:
    node = _make_node()
    text = render_node(node).decode("utf-8")
    head = text.split("---\n", 2)[1]
    keys = [
        line.split(":", 1)[0]
        for line in head.splitlines()
        if line and not line.startswith(" ") and not line.startswith("-")
    ]
    assert keys == [
        "label",
        "kind",
        "pass_count",
        "statement_hash",
        "verification_hash",
        "depends_on",
    ]


def test_depends_on_sorted_and_deduped() -> None:
    node = _make_node(depends_on=("def:z", "def:x", "def:x", "def:a"))
    text = render_node(node).decode("utf-8")
    # block-list form
    deps_block = text.split("depends_on:", 1)[1].split("---\n")[0]
    deps = [line.strip("- ").strip() for line in deps_block.splitlines() if line.startswith("- ")]
    assert deps == ["def:a", "def:x", "def:z"]


def test_unix_line_endings_only() -> None:
    node = _make_node(statement="multi\nline statement")
    raw = render_node(node)
    assert b"\r" not in raw, "renderer must not emit CR bytes"


def test_trailing_newline_exactly_one() -> None:
    node = _make_node()
    raw = render_node(node)
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n\n"), "no triple newline"


def test_empty_sections_omitted() -> None:
    node = _make_node(remark="", source_note="", proof="")
    text = render_node(node).decode("utf-8")
    assert "**Source Note.**" not in text
    assert "**Remark.**" not in text
    assert "**Proof.**" not in text
    assert "**Statement.**" in text


def test_section_order_fixed() -> None:
    node = _make_node(remark="r", source_note="s", proof="p")
    text = render_node(node).decode("utf-8")
    src = text.index("**Source Note.**")
    rem = text.index("**Remark.**")
    stmt = text.index("**Statement.**")
    proof = text.index("**Proof.**")
    assert src < rem < stmt < proof


def test_nfc_normalisation() -> None:
    # combine "e" + COMBINING ACUTE (NFD) → must come out as NFC "é".
    nfd = "café"
    node = _make_node(statement=nfd)
    text = render_node(node).decode("utf-8")
    assert "café" in text
    assert nfd not in text  # the decomposed form must not survive


def test_axiom_node_has_no_proof_section() -> None:
    node = _make_node(
        label="def:x",
        kind=NodeKind.DEFINITION,
        proof="",
        statement="X is a thing.",
    )
    raw = render_node(node).decode("utf-8")
    assert "**Proof.**" not in raw
    assert "**Statement.**" in raw


def test_node_filename_uses_prefix_under_label() -> None:
    node = _make_node(label="thm:main_result", kind=NodeKind.THEOREM)
    assert node_filename(node) == "thm_main_result.md"


def test_node_filename_rejects_prefix_mismatch() -> None:
    node = _make_node(label="thm:foo", kind=NodeKind.LEMMA)
    with pytest.raises(ValueError):
        node_filename(node)


def test_write_node_file_creates_dir_and_writes_bytes(tmp_path: Path) -> None:
    node = _make_node()
    nodes_dir = tmp_path / "nodes"
    written = write_node_file(nodes_dir, node)
    assert written.exists()
    assert written.read_bytes() == render_node(node)


def test_write_node_file_overwrites_atomically(tmp_path: Path) -> None:
    nodes_dir = tmp_path / "nodes"
    n1 = _make_node(statement="v1")
    n2 = _make_node(statement="v2")
    p1 = write_node_file(nodes_dir, n1)
    p2 = write_node_file(nodes_dir, n2)
    assert p1 == p2
    assert b"v2" in p2.read_bytes()
    assert b"v1" not in p2.read_bytes()
    # tmp file is gone
    assert not (nodes_dir / (p2.name + ".tmp")).exists()
