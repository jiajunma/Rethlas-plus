"""Compatibility shim for the old dashboard Kuzu reader module."""

from __future__ import annotations

from dashboard.kb_client import (
    KBUnavailable,
    NodeRow,
    RebuildInProgress,
    dependents_of,
    list_applied_failed,
    list_applied_since,
    list_nodes,
)

__all__ = [
    "KBUnavailable",
    "NodeRow",
    "RebuildInProgress",
    "dependents_of",
    "list_applied_failed",
    "list_applied_since",
    "list_nodes",
]
