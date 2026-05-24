"""Embedding provider Protocol (S6-A).

Minimal contract that decouples ``rethlas_scoring.cluster`` from any
specific embedding backend.
Implementations only need to expose a stable ``dim`` and a
``embed(text)`` that returns a tuple of floats.

Choosing the actual backend (OpenAI, sentence-transformers,
nomic-embed, etc.) is a deployment decision; the
``coordinator/main.py::_build_priority_fn`` seam injects whichever
provider the operator has wired in. The default in S6-B is the
zero-dependency :class:`HashEmbeddingProvider` so the cluster signal
isn't dead even before a real model is plugged in.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Anything with a ``dim`` attribute and ``embed(text) -> tuple[float, ...]``."""

    dim: int

    def embed(self, text: str) -> tuple[float, ...]:
        """Return a unit-or-near-unit vector of length ``dim``.

        Implementations should be **deterministic** — the same input
        text must always produce the same output vector. That keeps
        ``ClusterIndex`` snapshots reproducible across ticks (we
        re-build the index every dispatch tick, so a non-deterministic
        embedder would invalidate the clustering each time).
        """
        ...


__all__ = ["EmbeddingProvider"]
