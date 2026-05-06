"""Embedding-provider factory (S6-C).

``default_provider()`` returns whichever embedding provider is best
available *right now*, with these precedence rules:

1. **Explicit env override** ``RETHLAS_EMBEDDING_PROVIDER`` —
   ``"openai"`` forces OpenAI (raises if unavailable),
   ``"hash"`` forces the deterministic stub.
2. **OpenAI** if ``openai`` is importable AND ``OPENAI_API_KEY`` set.
3. **Hash fallback** otherwise — zero-dependency, deterministic.

Used by :mod:`coordinator.main` so a deployment can opt into a real
semantic provider purely by setting an env var (no code change). The
fallback ladder also means an OpenAI outage during a tick never
starves dispatch — the next tick falls back automatically because
``is_available()`` re-checks the SDK / key.
"""

from __future__ import annotations

import os
from typing import Literal

from .cache import CachingEmbeddingProvider
from .hash_provider import HashEmbeddingProvider
from .openai_provider import OpenAIEmbeddingProvider, is_available as _openai_available
from .provider import EmbeddingProvider


_ENV_OVERRIDE_VAR = "RETHLAS_EMBEDDING_PROVIDER"
_VALID_OVERRIDES = frozenset({"openai", "hash"})


# Process-wide cache so repeated dispatch ticks against the same
# workspace pay the inner provider exactly once per distinct claim.
# Keyed by the inner provider's type so swapping providers (e.g.
# operator flips RETHLAS_EMBEDDING_PROVIDER mid-process) doesn't
# return stale vectors of the wrong dimension.
_CACHE: CachingEmbeddingProvider | None = None
_CACHED_INNER_TYPE: type | None = None


def _build_inner() -> EmbeddingProvider:
    """Pick the raw underlying provider per the env precedence."""
    override = os.environ.get(_ENV_OVERRIDE_VAR, "").strip().lower()
    if override and override not in _VALID_OVERRIDES:
        # Unknown override — fall through to auto-detect rather than
        # raise; the dispatcher must never starve on a config typo.
        override = ""

    if override == "openai":
        if not _openai_available():
            raise RuntimeError(
                f"{_ENV_OVERRIDE_VAR}=openai but openai SDK / "
                "OPENAI_API_KEY not available"
            )
        return OpenAIEmbeddingProvider()

    if override == "hash":
        return HashEmbeddingProvider()

    # Auto-detect: prefer OpenAI when fully wired, else hash.
    if _openai_available():
        return OpenAIEmbeddingProvider()
    return HashEmbeddingProvider()


def default_provider() -> EmbeddingProvider:
    """Return the best available provider per the precedence above,
    wrapped in a process-wide :class:`CachingEmbeddingProvider`."""
    global _CACHE, _CACHED_INNER_TYPE
    inner = _build_inner()
    if _CACHE is None or type(inner) is not _CACHED_INNER_TYPE:
        _CACHE = CachingEmbeddingProvider(inner=inner)
        _CACHED_INNER_TYPE = type(inner)
    return _CACHE


def reset_cache() -> None:
    """Drop the process-wide cache. Useful in tests / after operator
    flips the env override; not normally called from production code."""
    global _CACHE, _CACHED_INNER_TYPE
    _CACHE = None
    _CACHED_INNER_TYPE = None


def selected_provider_name() -> Literal["openai", "hash"]:
    """Cheap query for logging / telemetry — what would
    :func:`default_provider` choose right now?

    Mirrors the precedence in :func:`default_provider` without
    actually constructing a provider (so it does not require the
    OpenAI SDK to be imported)."""
    override = os.environ.get(_ENV_OVERRIDE_VAR, "").strip().lower()
    if override == "hash":
        return "hash"
    if override == "openai":
        return "openai"
    if _openai_available():
        return "openai"
    return "hash"


__all__ = ["default_provider", "reset_cache", "selected_provider_name"]
