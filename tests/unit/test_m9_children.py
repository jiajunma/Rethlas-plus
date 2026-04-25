"""M9 — coordinator child-spawn plumbing."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from coordinator.children import spawn_librarian


def test_spawn_librarian_routes_stderr_to_log_file(tmp_path: Path) -> None:
    popen_result = object()

    def _fake_open(self: Path, mode: str = "r", buffering: int = -1, *args, **kwargs):
        assert self == tmp_path / "runtime" / "logs" / "librarian.log"
        assert mode == "ab"
        assert buffering == 0
        return io.BytesIO()

    with patch("pathlib.Path.open", new=_fake_open), patch(
        "subprocess.Popen", return_value=popen_result
    ) as popen:
        child = spawn_librarian(tmp_path)

    assert child.proc is popen_result
    kwargs = popen.call_args.kwargs
    assert kwargs["stderr"] is child.stderr_handle
    assert kwargs["stdout"] == subprocess.PIPE
    assert kwargs["stdin"] == subprocess.PIPE


def test_spawn_librarian_closes_log_handle_when_popen_fails(tmp_path: Path) -> None:
    holder: list[io.BytesIO] = []

    def _fake_open(self: Path, mode: str = "r", buffering: int = -1, *args, **kwargs):
        fh = io.BytesIO()
        holder.append(fh)
        return fh

    with patch("pathlib.Path.open", new=_fake_open), patch(
        "subprocess.Popen", side_effect=OSError("boom")
    ):
        with pytest.raises(OSError, match="boom"):
            spawn_librarian(tmp_path)

    assert holder, "expected fake log handle to be created"
    assert holder[0].closed
