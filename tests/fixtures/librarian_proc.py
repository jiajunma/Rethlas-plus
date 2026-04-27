"""Helpers for driving the librarian subprocess in integration tests.

The librarian speaks one JSON object per line on stdin/stdout. These
helpers wrap :class:`subprocess.Popen` with a strict line-protocol
client + a small set of fixture-only utilities (publish synthetic
events, wait for a phase, etc.).
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from librarian.heartbeat import read_heartbeat


PYTHON = sys.executable


def _spawn(workspace: Path, *, env_extra: dict[str, str] | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    # Default to a fast heartbeat for tests.
    env.setdefault("RETHLAS_LIBRARIAN_HEARTBEAT_S", "0.2")
    if env_extra:
        env.update(env_extra)
    return subprocess.Popen(
        [PYTHON, "-m", "cli.main", "--workspace", str(workspace), "librarian"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        bufsize=0,
    )


class LibrarianProc:
    """Subprocess wrapper that exposes the JSON-line protocol."""

    def __init__(self, proc: subprocess.Popen, workspace: Path) -> None:
        self.proc = proc
        self.workspace = workspace
        self._stdout_buf = b""

    # ---- protocol --------------------------------------------------
    def send(self, payload: dict[str, Any]) -> None:
        line = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        assert self.proc.stdin is not None
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def recv(self, timeout: float = 10.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        assert self.proc.stdout is not None
        fd = self.proc.stdout.fileno()
        while b"\n" not in self._stdout_buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                err = self._drain_stderr()
                raise AssertionError(
                    f"librarian did not reply in time; stderr={err!r}"
                )
            r, _, _ = select.select([fd], [], [], remaining)
            if not r:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                # EOF
                err = self._drain_stderr()
                raise AssertionError(
                    f"librarian closed stdout (code={self.proc.poll()}); stderr={err!r}"
                )
            self._stdout_buf += chunk
        line, _, rest = self._stdout_buf.partition(b"\n")
        self._stdout_buf = rest
        return json.loads(line)

    def _drain_stderr(self) -> str:
        if self.proc.stderr is None:
            return ""
        # Read what's available without blocking forever.
        out: list[bytes] = []
        fd = self.proc.stderr.fileno()
        while True:
            r, _, _ = select.select([fd], [], [], 0.05)
            if not r:
                break
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            out.append(chunk)
        return b"".join(out).decode("utf-8", errors="replace")

    # ---- helpers ---------------------------------------------------
    def wait_for_phase(self, phase: str, timeout: float = 15.0) -> dict[str, Any]:
        """Poll ``librarian.json`` until *this* process reports ``phase``.

        Matching on ``pid`` is mandatory: when a previous fixture session
        in the same workspace just shut down, ``librarian.json`` still
        carries that session's last heartbeat (often ``startup_phase ==
        ready``). Without the pid check, ``wait_for_phase`` would return
        immediately on the *stale* heartbeat — before the new librarian
        has bound its query socket — causing dashboard tests that hit the
        socket right after to fail with ``ENOENT``.
        """
        deadline = time.monotonic() + timeout
        path = self.workspace / "runtime" / "state" / "librarian.json"
        target_pid = self.proc.pid
        while time.monotonic() < deadline:
            data = read_heartbeat(path)
            if (
                data is not None
                and data.get("pid") == target_pid
                and data.get("startup_phase") == phase
            ):
                return data
            time.sleep(0.05)
        raise AssertionError(
            f"librarian (pid={target_pid}) never reached phase {phase!r} "
            f"(last heartbeat: {read_heartbeat(path)})"
        )

    def shutdown(self, timeout: float = 5.0) -> int:
        try:
            self.send({"cmd": "SHUTDOWN"})
            try:
                self.recv(timeout=timeout)
            except AssertionError:
                pass
        except (BrokenPipeError, OSError):
            pass
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait()


@contextmanager
def librarian(
    workspace: Path,
    *,
    env_extra: dict[str, str] | None = None,
) -> Iterator[LibrarianProc]:
    proc = _spawn(workspace, env_extra=env_extra)
    lp = LibrarianProc(proc, workspace)
    try:
        yield lp
    finally:
        lp.shutdown()


__all__ = ["LibrarianProc", "librarian"]
