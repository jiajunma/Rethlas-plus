"""embedding.cache — LRU wrapper (S6-D)."""

from __future__ import annotations

from dataclasses import dataclass, field

from embedding import (
    CachingEmbeddingProvider,
    EmbeddingProvider,
    HashEmbeddingProvider,
    default_provider,
    reset_cache,
)


# ---------------------------------------------------------------------------
# Counting stub provider — lets tests assert how many times the inner
# provider was actually consulted.
# ---------------------------------------------------------------------------
@dataclass
class _CountingProvider:
    """Records every embed() call. Returns a deterministic vector
    derived from text length so tests can sanity-check identity."""

    dim: int = 4
    calls: list[str] = field(default_factory=list)

    def embed(self, text: str) -> tuple[float, ...]:
        self.calls.append(text)
        n = len(text)
        return tuple(float(n + i) for i in range(self.dim))


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------
def test_caching_provider_satisfies_protocol() -> None:
    inner = HashEmbeddingProvider(dim=8)
    cache = CachingEmbeddingProvider(inner=inner)
    assert isinstance(cache, EmbeddingProvider)
    assert cache.dim == 8


# ---------------------------------------------------------------------------
# Cache hit / miss accounting
# ---------------------------------------------------------------------------
def test_cache_records_miss_then_hit_for_repeat_query() -> None:
    counter = _CountingProvider()
    cache = CachingEmbeddingProvider(inner=counter)
    v1 = cache.embed("alpha")
    v2 = cache.embed("alpha")
    assert v1 == v2
    assert counter.calls == ["alpha"]  # inner consulted exactly once
    s = cache.stats()
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["evictions"] == 0
    assert s["size"] == 1


def test_cache_distinct_inputs_each_miss() -> None:
    counter = _CountingProvider()
    cache = CachingEmbeddingProvider(inner=counter)
    for tok in ("a", "b", "c", "a"):
        cache.embed(tok)
    assert counter.calls == ["a", "b", "c"]  # 'a' second time was a hit
    s = cache.stats()
    assert s["hits"] == 1
    assert s["misses"] == 3


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------
def test_cache_evicts_oldest_when_maxsize_exceeded() -> None:
    counter = _CountingProvider()
    cache = CachingEmbeddingProvider(inner=counter, maxsize=2)
    cache.embed("a")  # store: [a]
    cache.embed("b")  # store: [a, b]
    cache.embed("c")  # store: [b, c] — evicts 'a' (1)
    cache.embed("a")  # miss — store: [c, a] — evicts 'b' (2)
    assert counter.calls == ["a", "b", "c", "a"]
    s = cache.stats()
    assert s["evictions"] == 2
    assert s["size"] == 2  # capped by maxsize


def test_cache_lru_promotion_on_hit_protects_recent_entries() -> None:
    counter = _CountingProvider()
    cache = CachingEmbeddingProvider(inner=counter, maxsize=2)
    cache.embed("a")
    cache.embed("b")
    cache.embed("a")  # hit on 'a' → promotes it to most-recent
    cache.embed("c")  # evicts 'b' (now oldest), keeps 'a'
    assert counter.calls == ["a", "b", "c"]
    # 'a' should still be cached.
    cache.embed("a")
    assert counter.calls == ["a", "b", "c"]  # still no extra 'a' call


# ---------------------------------------------------------------------------
# clear() resets state
# ---------------------------------------------------------------------------
def test_cache_clear_resets_storage_and_counters() -> None:
    counter = _CountingProvider()
    cache = CachingEmbeddingProvider(inner=counter)
    cache.embed("x")
    cache.embed("x")
    cache.clear()
    assert cache.stats() == {
        "hits": 0,
        "misses": 0,
        "evictions": 0,
        "size": 0,
        "maxsize": 4096,
    }
    cache.embed("x")  # was a hit before clear; now must miss
    assert counter.calls == ["x", "x"]


# ---------------------------------------------------------------------------
# factory wires the cache transparently
# ---------------------------------------------------------------------------
def test_factory_default_provider_returns_caching_wrapper(monkeypatch) -> None:
    monkeypatch.delenv("RETHLAS_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reset_cache()
    p = default_provider()
    assert isinstance(p, CachingEmbeddingProvider)
    assert isinstance(p.inner, HashEmbeddingProvider)


def test_factory_caches_across_calls_within_a_process(monkeypatch) -> None:
    """Two calls to ``default_provider()`` should return the same
    object so the cache state persists across dispatch ticks."""
    monkeypatch.delenv("RETHLAS_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reset_cache()
    p1 = default_provider()
    p2 = default_provider()
    assert p1 is p2


def test_factory_cache_persists_with_unchanged_inner_type(monkeypatch) -> None:
    """If the env stays consistent, the same cache wrapper is reused."""
    reset_cache()
    monkeypatch.setenv("RETHLAS_EMBEDDING_PROVIDER", "hash")
    p1 = default_provider()
    assert isinstance(p1.inner, HashEmbeddingProvider)
    p2 = default_provider()
    assert p1 is p2  # stable
