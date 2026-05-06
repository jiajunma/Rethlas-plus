"""OpenAI semantic embedding provider (S6-C).

Thin wrapper around ``openai.embeddings.create``. The ``openai`` SDK
is **lazily imported** inside ``embed()`` so this module stays
importable even when the dependency isn't installed — the factory in
:mod:`embedding.factory` checks importability before constructing
this provider.

Reads ``OPENAI_API_KEY`` from the environment. Outputs are L2-normalised
floats (the OpenAI API already returns unit vectors for the
``text-embedding-3-*`` family, but we re-normalise defensively in case
of API drift).

This provider is a network call — keep ``HashEmbeddingProvider`` as
the dispatcher fallback so a transient API outage cannot starve
scheduling. ``factory.default_provider()`` handles that fallback.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass


# Default to ``text-embedding-3-small`` — 1536-dim, 5x cheaper than
# ``text-embedding-3-large`` and still strong on short claim text.
_DEFAULT_MODEL = "text-embedding-3-small"
_DEFAULT_DIM = 1536


@dataclass(frozen=True, slots=True)
class OpenAIEmbeddingProvider:
    """Network-backed semantic embedder via the OpenAI API.

    Raises ``RuntimeError`` from :meth:`embed` if the SDK or env var
    isn't available — callers (typically the factory) should pre-check
    via :func:`is_available`.
    """

    model: str = _DEFAULT_MODEL
    dim: int = _DEFAULT_DIM

    def embed(self, text: str) -> tuple[float, ...]:
        if not text or not text.strip():
            return tuple(0.0 for _ in range(self.dim))
        client = _client()
        if client is None:
            raise RuntimeError(
                "OpenAIEmbeddingProvider: OPENAI_API_KEY not set or "
                "openai package missing — callers should pre-check via "
                "embedding.openai_provider.is_available() and fall back "
                "to HashEmbeddingProvider."
            )
        resp = client.embeddings.create(model=self.model, input=text)
        raw = tuple(float(x) for x in resp.data[0].embedding)
        # Defensive L2 normalisation (API already does this for the
        # text-embedding-3 family, but re-doing is cheap and survives
        # any future API drift).
        norm = math.sqrt(sum(x * x for x in raw))
        if norm <= 0.0:
            return tuple(0.0 for _ in range(len(raw)))
        return tuple(x / norm for x in raw)


def is_available() -> bool:
    """Return True iff ``openai`` is importable and ``OPENAI_API_KEY`` set."""
    if not os.environ.get("OPENAI_API_KEY"):
        return False
    try:
        import openai  # noqa: F401
    except ImportError:
        return False
    return True


def _client():
    """Construct an ``openai.OpenAI`` client, or return ``None`` if not
    available. Cached on the module so repeat calls reuse the same
    HTTP connection pool."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    if not is_available():
        return None
    try:
        import openai

        _CLIENT = openai.OpenAI()
        return _CLIENT
    except Exception:
        return None


_CLIENT = None  # populated lazily by ``_client()``


__all__ = ["OpenAIEmbeddingProvider", "is_available"]
