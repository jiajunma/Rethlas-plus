"""Abstract read API for the projected KB.

In the revised §4.1 concurrency model (2026-04-25), only librarian opens
the Kuzu database. Other processes reach KB state via an IPC client
that implements the same interface. M2 ships only the in-process
implementation backed by :class:`common.kb.kuzu_backend.KuzuBackend`; the
IPC client lands with librarian's daemon work in M4.

Typing this as a :class:`~typing.Protocol` keeps the contract clear and
lets later milestones swap implementations without touching callers.
"""

from __future__ import annotations

from typing import Protocol

from common.kb.kuzu_backend import KuzuBackend, RawNodeRow
from common.kb.types import AppliedEvent


class KBReader(Protocol):
    """Minimum read surface needed by coordinator / dashboard / linter."""

    def applied_event(self, event_id: str) -> AppliedEvent | None: ...

    def node_by_label(self, label: str) -> RawNodeRow | None: ...

    def node_labels(self) -> list[str]: ...

    def dependencies_of(self, label: str) -> list[str]: ...

    def dependents_of(self, label: str) -> list[str]: ...

    def repair_count(self, label: str) -> int: ...


class LibrarianReader:
    """In-process KBReader used by the librarian itself."""

    def __init__(self, backend: KuzuBackend) -> None:
        self._backend = backend

    def applied_event(self, event_id: str) -> AppliedEvent | None:
        return self._backend.applied_event(event_id)

    def node_by_label(self, label: str) -> RawNodeRow | None:
        return self._backend.node_by_label(label)

    def node_labels(self) -> list[str]:
        return self._backend.node_labels()

    def dependencies_of(self, label: str) -> list[str]:
        return self._backend.dependencies_of(label)

    def dependents_of(self, label: str) -> list[str]:
        return self._backend.dependents_of(label)

    def repair_count(self, label: str) -> int:
        row = self._backend.node_by_label(label)
        return row.repair_count if row is not None else 0
