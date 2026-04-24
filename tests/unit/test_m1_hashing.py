"""M1 — statement_hash / verification_hash are stable under key order / newlines."""

from __future__ import annotations

from common.kb.hashing import DepRef, canonical_json, statement_hash, verification_hash


def test_statement_hash_stable_no_deps() -> None:
    h1 = statement_hash(
        label="def:x",
        kind="definition",
        statement="A primary object is...",
        depends_on=(),
    )
    h2 = statement_hash(
        label="def:x",
        kind="definition",
        statement="A primary object is...",
        depends_on=(),
    )
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_statement_hash_depends_on_order_invariant() -> None:
    """``depends_on`` is sorted by label → input order must not matter."""
    deps_a = (
        DepRef(label="lem:a", statement_hash="aa"),
        DepRef(label="lem:b", statement_hash="bb"),
    )
    deps_b = (
        DepRef(label="lem:b", statement_hash="bb"),
        DepRef(label="lem:a", statement_hash="aa"),
    )
    h_a = statement_hash(label="thm:t", kind="theorem", statement="s", depends_on=deps_a)
    h_b = statement_hash(label="thm:t", kind="theorem", statement="s", depends_on=deps_b)
    assert h_a == h_b


def test_statement_hash_normalises_line_endings() -> None:
    lf = statement_hash(
        label="lem:z", kind="lemma", statement="line 1\nline 2\n", depends_on=()
    )
    crlf = statement_hash(
        label="lem:z", kind="lemma", statement="line 1\r\nline 2\r\n", depends_on=()
    )
    cr = statement_hash(
        label="lem:z", kind="lemma", statement="line 1\rline 2\r", depends_on=()
    )
    assert lf == crlf == cr, "CRLF / CR must be normalised to LF before hashing"


def test_statement_hash_nfc_normalisation() -> None:
    # "é" can be NFC (single code point U+00E9) or NFD (U+0065 + U+0301).
    nfc = "café"
    nfd = "café"
    assert nfc != nfd  # sanity
    h_nfc = statement_hash(label="def:x", kind="definition", statement=nfc, depends_on=())
    h_nfd = statement_hash(label="def:x", kind="definition", statement=nfd, depends_on=())
    assert h_nfc == h_nfd


def test_statement_hash_differs_for_different_statements() -> None:
    h1 = statement_hash(label="def:x", kind="definition", statement="A", depends_on=())
    h2 = statement_hash(label="def:x", kind="definition", statement="B", depends_on=())
    assert h1 != h2


def test_verification_hash_axiom_special_case() -> None:
    sh = statement_hash(label="def:x", kind="definition", statement="A", depends_on=())
    vh_empty = verification_hash(statement_hash_hex=sh, proof="")
    vh_none = verification_hash(statement_hash_hex=sh, proof=None)
    assert vh_empty == vh_none, "proof=None treated as empty proof (axiom)"


def test_verification_hash_changes_with_proof() -> None:
    sh = statement_hash(label="lem:x", kind="lemma", statement="A", depends_on=())
    vh_a = verification_hash(statement_hash_hex=sh, proof="Proof sketch A")
    vh_b = verification_hash(statement_hash_hex=sh, proof="Proof sketch B")
    assert vh_a != vh_b


def test_canonical_json_sorted_keys_and_compact() -> None:
    payload = {"b": 1, "a": [3, 2, 1]}
    got = canonical_json(payload)
    assert got == b'{"a":[3,2,1],"b":1}'
