"""LRU embedding cache (S6-D).

Wraps any :class:`EmbeddingProvider` in an LRU cache keyed by input
text. The dispatcher rebuilds its priority function every tick — at
N candidates per tick × T ticks the inner provider would otherwise
get N×T calls for an unchanged statement. With this cache it gets
exactly one call per distinct statement (until the LRU evicts it).

Properties:

- **Transparent**: the cache exposes the same ``EmbeddingProvider``
  Protocol (``dim`` + ``embed``); callers cannot tell.
- **Bounded**: ``maxsize`` caps memory. Default 4096 — comfortable for
  workspaces with thousands of distinct claims, still small in RAM
  (4096 × 1536 floats × 8 B ≈ 50 MB worst-case for OpenAI dim).
- **No-network on hit**: critical for the OpenAI path — repeated
  ticks against the same workspace cost 0 API calls.

Pure stdlib. Not thread-safe by design — the coordinator is single-
threaded; if that changes, wrap accesses in a ``threading.Lock``.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from .provider import EmbeddingProvider


_DEFAULT_MAXSIZE = 4096


@dataclass
class CachingEmbeddingProvider:
    """LRU cache wrapper around any underlying provider.

    Statistics are tracked on the instance for telemetry / tests:

    - ``hits`` — distinct ``embed()`` calls served from cache.
    - ``misses`` — calls that fell through to the inner provider.
    - ``evictions`` — entries kicked out by the LRU policy.
    """

    inner: EmbeddingProvider
    maxsize: int = _DEFAULT_MAXSIZE
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    _store: "OrderedDict[str, tuple[float, ...]]" = field(default_factory=OrderedDict)

    @property
    def dim(self) -> int:
        return self.inner.dim

    def embed(self, text: str) -> tuple[float, ...]:
        if text in self._store:
            self.hits += 1
            self._store.move_to_end(text)
            return self._store[text]
        self.misses += 1
        vec = self.inner.embed(text)
        self._store[text] = vec
        if len(self._store) > self.maxsize:
            self._store.popitem(last=False)  # FIFO drop = LRU eviction
            self.evictions += 1
        return vec

    def stats(self) -> dict[str, int]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "size": len(self._store),
            "maxsize": self.maxsize,
        }

    def clear(self) -> None:
        self._store.clear()
        self.hits = 0
        self.misses = 0
        self.evictions = 0


__all__ = ["CachingEmbeddingProvider"]
