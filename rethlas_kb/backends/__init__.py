"""Backend registry — name → AgentBackend lookup.

Real backends (codex / claude / opencode) self-register via
``register_backend(...)`` in their respective modules. Tests inject
``MockBackend`` instances explicitly.

See :mod:`rethlas_kb.backends.base` for the Protocol.
"""

from __future__ import annotations

from .base import AgentBackend, AgentResult, BackendError
from .mock import MockBackend

_REGISTRY: dict[str, AgentBackend] = {}


def register_backend(backend: AgentBackend) -> None:
    """Add a backend to the registry, keyed by ``backend.name``.

    Re-registration overwrites — this is intentional so tests can swap
    in a ``MockBackend`` for the same name a real backend would use.
    """

    _REGISTRY[backend.name] = backend


def get_backend(name: str) -> AgentBackend:
    """Lookup a registered backend by name.

    Raises :class:`BackendError` if ``name`` is not registered, with the
    available backend names in the message.
    """

    if name not in _REGISTRY:
        available = sorted(_REGISTRY.keys()) or ["<none registered>"]
        raise BackendError(
            f"backend {name!r} not registered; available: {available}"
        )
    return _REGISTRY[name]


def available_backends() -> list[str]:
    """Return the names of currently-registered backends, sorted."""

    return sorted(_REGISTRY.keys())


def clear_registry() -> None:
    """Test helper — empty the registry between test cases."""

    _REGISTRY.clear()


__all__ = [
    "AgentBackend",
    "AgentResult",
    "BackendError",
    "MockBackend",
    "available_backends",
    "clear_registry",
    "get_backend",
    "register_backend",
]
