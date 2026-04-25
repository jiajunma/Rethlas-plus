"""Coordinator-level singleton lock on ``runtime/locks/supervise.lock``.

ARCHITECTURE §6.4: only one ``rethlas supervise`` is allowed per
workspace. The lock is an advisory ``flock`` on the same file
``rethlas rebuild`` checks, so the two commands are mutually
exclusive across processes.
"""

from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path


class SuperviseLockError(RuntimeError):
    """Raised when another process holds ``runtime/locks/supervise.lock``."""


class SuperviseLock:
    """Context-manager wrapper for the workspace supervise.lock fd."""

    def __init__(self, locks_dir: Path) -> None:
        self.path = locks_dir / "supervise.lock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd: int | None = None

    def __enter__(self) -> "SuperviseLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()

    def acquire(self) -> None:
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise SuperviseLockError(
                    f"another supervise/rebuild holds {self.path}"
                ) from exc
            raise
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


__all__ = ["SuperviseLock", "SuperviseLockError"]
