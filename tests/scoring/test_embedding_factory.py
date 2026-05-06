"""embedding.factory — env-driven provider selection (S6-C)."""

from __future__ import annotations

import sys
import types

import pytest

import embedding.factory as factory
import embedding.openai_provider as openai_provider
from embedding import (
    CachingEmbeddingProvider,
    HashEmbeddingProvider,
    OpenAIEmbeddingProvider,
    default_provider,
    reset_cache,
    selected_provider_name,
)


def _inner_of(p) -> object:
    """Return the underlying (non-cache) provider for isinstance checks."""
    return p.inner if isinstance(p, CachingEmbeddingProvider) else p


# ---------------------------------------------------------------------------
# Fixtures: clean env between tests; reset cached client.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("RETHLAS_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Reset the lazy client cache + the factory's cached wrapper so each
    # test sees an isolated provider stack.
    openai_provider._CLIENT = None
    reset_cache()
    yield
    reset_cache()


# ---------------------------------------------------------------------------
# Default behaviour without env / without openai SDK
# ---------------------------------------------------------------------------
def test_default_provider_falls_back_to_hash_when_nothing_available() -> None:
    p = default_provider()
    assert isinstance(p, CachingEmbeddingProvider)
    assert isinstance(_inner_of(p), HashEmbeddingProvider)


def test_selected_provider_name_reports_hash_when_no_openai() -> None:
    assert selected_provider_name() == "hash"


# ---------------------------------------------------------------------------
# Explicit env override
# ---------------------------------------------------------------------------
def test_explicit_hash_override(monkeypatch) -> None:
    monkeypatch.setenv("RETHLAS_EMBEDDING_PROVIDER", "hash")
    assert isinstance(_inner_of(default_provider()), HashEmbeddingProvider)
    assert selected_provider_name() == "hash"


def test_explicit_openai_override_without_sdk_raises(monkeypatch) -> None:
    monkeypatch.setenv("RETHLAS_EMBEDDING_PROVIDER", "openai")
    # No OPENAI_API_KEY set, no openai SDK guaranteed → must raise.
    with pytest.raises(RuntimeError, match="openai"):
        default_provider()


def test_unknown_env_override_silently_falls_through_to_hash(monkeypatch) -> None:
    """A typo in the env var must not starve dispatch — fall back to
    auto-detect rather than raising."""
    monkeypatch.setenv("RETHLAS_EMBEDDING_PROVIDER", "bogus_name")
    p = default_provider()
    assert isinstance(_inner_of(p), HashEmbeddingProvider)


def test_env_override_case_and_whitespace_insensitive(monkeypatch) -> None:
    monkeypatch.setenv("RETHLAS_EMBEDDING_PROVIDER", "  HASH  ")
    assert isinstance(_inner_of(default_provider()), HashEmbeddingProvider)


# ---------------------------------------------------------------------------
# Auto-detect path: simulate openai SDK present + key set.
# ---------------------------------------------------------------------------
def _install_fake_openai(monkeypatch) -> None:
    """Plant a minimal fake ``openai`` module so ``is_available()`` and
    ``_client()`` return truthy without any network calls."""
    fake = types.SimpleNamespace()

    class _FakeClient:
        def __init__(self):
            self.embeddings = types.SimpleNamespace(create=lambda **kw: None)

    fake.OpenAI = _FakeClient
    monkeypatch.setitem(sys.modules, "openai", fake)


def test_auto_detect_picks_openai_when_sdk_and_key_present(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test-only")
    _install_fake_openai(monkeypatch)
    assert openai_provider.is_available() is True
    assert selected_provider_name() == "openai"
    assert isinstance(_inner_of(default_provider()), OpenAIEmbeddingProvider)


def test_auto_detect_skips_openai_without_key(monkeypatch) -> None:
    _install_fake_openai(monkeypatch)
    # No OPENAI_API_KEY → openai not "available" even with SDK.
    assert openai_provider.is_available() is False
    assert isinstance(_inner_of(default_provider()), HashEmbeddingProvider)


def test_auto_detect_skips_openai_without_sdk(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test-only")
    monkeypatch.setitem(sys.modules, "openai", None)  # Force ImportError
    # Provider should fall back to hash without raising.
    assert isinstance(_inner_of(default_provider()), HashEmbeddingProvider)


# ---------------------------------------------------------------------------
# OpenAIEmbeddingProvider intrinsic behaviour
# ---------------------------------------------------------------------------
def test_openai_provider_returns_zero_vector_for_empty_input() -> None:
    p = OpenAIEmbeddingProvider(dim=8)
    assert p.embed("") == tuple([0.0] * 8)
    assert p.embed("    ") == tuple([0.0] * 8)


def test_openai_provider_raises_when_unavailable() -> None:
    """Without ``OPENAI_API_KEY`` set, calling embed must raise — the
    factory pre-checks via ``is_available`` precisely to avoid this."""
    p = OpenAIEmbeddingProvider()
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        p.embed("non-empty input")


def test_openai_provider_normalises_returned_vector(monkeypatch) -> None:
    """Even if the API returns a non-unit vector (API drift), our
    wrapper L2-normalises before returning."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    fake = types.SimpleNamespace()
    raw_embedding = [3.0, 4.0]  # length-2, norm = 5.0
    expected = [3.0 / 5.0, 4.0 / 5.0]

    class _FakeClient:
        def __init__(self):
            self.embeddings = self

        def create(self, **_kwargs):
            return types.SimpleNamespace(
                data=[types.SimpleNamespace(embedding=raw_embedding)]
            )

    fake.OpenAI = _FakeClient
    monkeypatch.setitem(sys.modules, "openai", fake)

    p = OpenAIEmbeddingProvider(dim=2)
    out = p.embed("ignored-input")
    assert len(out) == 2
    for got, want in zip(out, expected):
        assert abs(got - want) < 1e-9
