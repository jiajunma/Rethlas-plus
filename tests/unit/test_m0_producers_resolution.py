"""M0 — producers.toml resolves from the Rethlas installation, not the workspace.

Guards against an admission-bypass where a compromised workspace
ships a conflicting ``producers.toml`` to subvert the producer
registry (ARCHITECTURE §2.1, §3.5).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_producers_toml_is_packaged_and_readable() -> None:
    from common.producers import producers_toml_bytes, producers_toml_path

    path = producers_toml_path()
    assert path.is_file()
    data = producers_toml_bytes()
    assert b"[[producer]]" in data
    assert b'kind                 = "user"' in data
    assert b'kind                 = "generator"' in data
    assert b'kind                 = "verifier"' in data


def test_workspace_producers_toml_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A workspace-local ``producers.toml`` must not override the packaged copy."""
    from common.producers import producers_toml_bytes

    # Simulate a compromised workspace: write a bogus producers.toml into cwd.
    bogus = tmp_path / "producers.toml"
    bogus.write_text(
        "[[producer]]\n"
        'kind                 = "evil"\n'
        'actor_pattern        = ".*"\n'
        'allowed_event_types  = ["anything"]\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    # Resolver must return the PACKAGED bytes, not the workspace copy.
    packaged = producers_toml_bytes()
    assert b'kind                 = "evil"' not in packaged
    assert b'kind                 = "user"' in packaged
