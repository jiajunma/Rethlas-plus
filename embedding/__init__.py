"""``embedding`` — pluggable text-to-vector providers (S6-A).

Defines :class:`EmbeddingProvider` (Protocol) plus a zero-dependency
:class:`HashEmbeddingProvider` for development / tests. Production
deployments swap in an OpenAI / sentence-transformers / nomic-embed
backed implementation by injecting a different provider at the
``coordinator/main.py::_build_priority_fn`` seam.

The S6-A package ships **provider primitives only** — no librarian
write path, no KB schema change. S6-B wires the default hash provider
into the dispatcher; later sprints (S6-C / S6-D) add a real provider
and persist embeddings on the KB side.
"""

from __future__ import annotations

from .cache import CachingEmbeddingProvider
from .factory import default_provider, reset_cache, selected_provider_name
from .hash_provider import HashEmbeddingProvider
from .openai_provider import OpenAIEmbeddingProvider
from .provider import EmbeddingProvider

__all__ = [
    "CachingEmbeddingProvider",
    "EmbeddingProvider",
    "HashEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "default_provider",
    "reset_cache",
    "selected_provider_name",
]
