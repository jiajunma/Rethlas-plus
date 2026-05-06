"""Deterministic bag-of-words hash embedding (S6-A default).

Zero-dependency stand-in for a real embedding model. Tokenises the
input text into lowercase word tokens, hashes each token to a fixed
dimension via ``blake2b``, and accumulates a unit-normalised vector.
Two claims that share vocabulary will have non-trivial cosine
similarity; two claims with disjoint vocabularies sit near zero.

This is **not** a semantic embedder — synonyms get different hashes —
but it gives the cluster propagation layer a meaningful signal in
development / tests until a real provider (OpenAI, sentence-
transformers, nomic-embed, …) is plugged in.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass


_WORD_RE = re.compile(r"[\w']+")


@dataclass(frozen=True, slots=True)
class HashEmbeddingProvider:
    """Bag-of-words hash embedder.

    ``dim`` defaults to 64 — small enough to keep cosine in
    ``ClusterIndex`` cheap, large enough that collisions between
    unrelated tokens stay rare for typical proof-claim vocabulary.
    """

    dim: int = 64

    def embed(self, text: str) -> tuple[float, ...]:
        """Tokenise ``text``, hash each token to a coordinate, return
        an L2-normalised vector of length ``self.dim``.

        - Empty / pure-punctuation input → ``(0.0,) * dim`` (cosine
          with this vector is 0 by construction in
          ``rethlas_scoring.cluster.cosine``).
        - Same input → same output (deterministic per Protocol).
        """

        tokens = [t.lower() for t in _WORD_RE.findall(text or "")]
        if not tokens:
            return tuple(0.0 for _ in range(self.dim))

        vec = [0.0] * self.dim
        for tok in tokens:
            digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
            # First 4 bytes pick the coordinate; next byte's high bit picks sign.
            idx = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if (digest[4] & 0x80) else -1.0
            vec[idx] += sign

        norm = math.sqrt(sum(x * x for x in vec))
        if norm <= 0.0:
            return tuple(0.0 for _ in range(self.dim))
        return tuple(x / norm for x in vec)


__all__ = ["HashEmbeddingProvider"]
