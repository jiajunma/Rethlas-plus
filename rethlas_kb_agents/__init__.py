"""Agent implementations for rethlas-kb (kept separate from rethlas_kb).

The split keeps the infrastructure layer (``rethlas_kb`` — backends,
adapter, CLI, config) from importing any agent. Agents are
discovered/registered by name at CLI-startup time so that adding a
new one never forces an edit of the lower layer.
"""

from __future__ import annotations
