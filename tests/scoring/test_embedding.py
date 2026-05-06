"""embedding/ — Protocol + HashEmbeddingProvider (S6-A)."""

from __future__ import annotations

import math

from embedding import EmbeddingProvider, HashEmbeddingProvider
from rethlas_scoring.cluster import cosine


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------
def test_hash_provider_satisfies_protocol() -> None:
    p = HashEmbeddingProvider()
    assert isinstance(p, EmbeddingProvider)


def test_hash_provider_dim_attribute_present() -> None:
    p = HashEmbeddingProvider(dim=32)
    assert p.dim == 32


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
def test_embed_is_deterministic_same_input() -> None:
    p = HashEmbeddingProvider()
    a = p.embed("for all n, n^2 >= 0")
    b = p.embed("for all n, n^2 >= 0")
    assert a == b


def test_embed_returns_tuple_of_correct_length() -> None:
    p = HashEmbeddingProvider(dim=128)
    v = p.embed("any input")
    assert isinstance(v, tuple)
    assert len(v) == 128


# ---------------------------------------------------------------------------
# L2 normalisation
# ---------------------------------------------------------------------------
def test_embed_returns_unit_norm_vector_for_nontrivial_input() -> None:
    p = HashEmbeddingProvider()
    v = p.embed("a b c d e f")
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-9


def test_embed_returns_zero_vector_for_empty_input() -> None:
    p = HashEmbeddingProvider(dim=16)
    assert p.embed("") == tuple([0.0] * 16)


def test_embed_returns_zero_vector_for_pure_punctuation() -> None:
    p = HashEmbeddingProvider(dim=16)
    assert p.embed("?!??...") == tuple([0.0] * 16)


# ---------------------------------------------------------------------------
# Cosine semantics
# ---------------------------------------------------------------------------
def test_similar_texts_have_positive_cosine() -> None:
    """Sentences sharing most vocabulary should sit well above zero."""
    p = HashEmbeddingProvider()
    a = p.embed("the cat sat on the mat")
    b = p.embed("the cat sat on the rug")
    assert cosine(a, b) > 0.5


def test_disjoint_vocab_has_near_zero_cosine() -> None:
    """Sentences sharing no tokens should sit near 0 (random hash collision)."""
    p = HashEmbeddingProvider()
    a = p.embed("topology is the study of continuous deformations")
    b = p.embed("xyzzy plugh quux fnord wibble")
    assert abs(cosine(a, b)) < 0.25


def test_identical_text_has_cosine_one() -> None:
    p = HashEmbeddingProvider()
    v = p.embed("the integers form a ring")
    assert abs(cosine(v, v) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# Tokenisation
# ---------------------------------------------------------------------------
def test_case_insensitive_tokenisation() -> None:
    p = HashEmbeddingProvider()
    a = p.embed("The Quick Brown Fox")
    b = p.embed("the quick brown fox")
    assert cosine(a, b) > 0.99


def test_punctuation_does_not_affect_tokenisation() -> None:
    p = HashEmbeddingProvider()
    a = p.embed("hello, world!")
    b = p.embed("hello world")
    assert cosine(a, b) > 0.99


def test_unicode_word_characters_are_tokenised() -> None:
    """``\\w`` matches unicode letters; non-ASCII claims should embed."""
    p = HashEmbeddingProvider()
    v = p.embed("证明对就是对错就是错")
    # Non-zero — at least one token was extracted.
    assert any(x != 0.0 for x in v)
