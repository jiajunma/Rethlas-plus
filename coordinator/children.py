"""Child subprocess management (ARCHITECTURE §6.4 child supervision).

Coordinator spawns the librarian as a subprocess and talks to it over
a JSON-line stdio channel (the same channel the M4 daemon test
fixture uses). The child manager exposes:

- :class:`LibrarianChild` — wraps the Popen + JsonLineChannel pair
  and keeps a tiny in-memory request/response state for APPLY
  pipelining.
- restart policy hooks per §6.4 E1/E2 (one restart, then escalate).

Phase I scope: librarian only. Dashboard child management is M9.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


@dataclass
class LibrarianChild:
    """One running librarian subprocess."""

    proc: subprocess.Popen
    workspace: Path
    started_at: float
    stderr_handle: BinaryIO | None = None
    _stdout_buf: bytes = b""

    @property
    def pid(self) -> int:
        return self.proc.pid

    def is_alive(self) -> bool:
        return self.proc.poll() is None

    def send(self, payload: dict[str, Any]) -> None:
        line = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        if self.proc.stdin is None:
            raise RuntimeError("librarian stdin closed")
        try:
            self.proc.stdin.write(line)
            self.proc.stdin.flush()
        except BrokenPipeError:
            raise RuntimeError("librarian stdin: broken pipe")

    def recv(self, timeout: float = 30.0) -> dict[str, Any] | None:
        """Return the next JSON-line reply or ``None`` on timeout."""
        deadline = time.monotonic() + timeout
        if self.proc.stdout is None:
            return None
        fd = self.proc.stdout.fileno()
        while b"\n" not in self._stdout_buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            r, _, _ = select.select([fd], [], [], remaining)
            if not r:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                return None
            self._stdout_buf += chunk
        line, _, rest = self._stdout_buf.partition(b"\n")
        self._stdout_buf = rest
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def request(self, payload: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any] | None:
        """Send + wait for reply. Returns ``None`` on timeout / EOF."""
        self.send(payload)
        return self.recv(timeout=timeout)

    def close_handles(self) -> None:
        for fh in (self.proc.stdin, self.proc.stdout, self.stderr_handle):
            if fh is None:
                continue
            try:
                fh.close()
            except Exception:
                pass
        self.stderr_handle = None

    def shutdown(self, *, timeout: float = 10.0) -> int:
        """Send SHUTDOWN, then wait. Falls back to SIGTERM/KILL if needed."""
        try:
            self.send({"cmd": "SHUTDOWN"})
            try:
                self.recv(timeout=2.0)
            except Exception:
                pass
        except Exception:
            pass
        try:
            rc = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                rc = self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                rc = self.proc.wait()
        self.close_handles()
        return int(rc if rc is not None else 0)


def spawn_librarian(workspace: Path, *, env_extra: dict[str, str] | None = None) -> LibrarianChild:
    """Launch ``python -m cli.main --workspace WS librarian`` as a child."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    logs_dir = workspace / "runtime" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stderr_handle = (logs_dir / "librarian.log").open("ab", buffering=0)
    proc = subprocess.Popen(
        [sys.executable, "-m", "cli.main", "--workspace", str(workspace), "librarian"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr_handle,
        env=env,
        bufsize=0,
    )
    return LibrarianChild(
        proc=proc,
        workspace=workspace,
        started_at=time.monotonic(),
        stderr_handle=stderr_handle,
    )


__all__ = ["LibrarianChild", "spawn_librarian"]
