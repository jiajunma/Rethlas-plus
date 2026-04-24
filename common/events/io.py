"""Atomic event write + raw-byte SHA-256 (ARCHITECTURE §9.1 G3).

The write helper executes the exact sequence required by §9.1 G3:

1. ``open`` a ``.tmp`` sibling file for write
2. ``write`` the serialized bytes
3. ``fsync(tmp_fd)`` so the content is durable
4. ``close`` the tmp fd
5. ``rename(tmp, canonical)`` — atomic within the same directory
6. ``fsync(dir_fd)`` — so the directory entry itself is durable
7. ``close`` the dir fd

Without step 6 a kernel / power crash can leave the file bytes on disk
but its *name* invisible: the event would be "written but nameless".
Both fsyncs are therefore mandatory for the §3 truth-layer contract.

Callers pass already-serialised bytes so the helper is independent of
the event body schema. The linter's category-F check relies on the
fact that the on-disk bytes are exactly what we wrote — see
:func:`event_sha256`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Final

_TMP_SUFFIX: Final[str] = ".tmp"


def atomic_write_event(canonical_path: str | Path, body: bytes) -> Path:
    """Atomically write ``body`` to ``canonical_path`` with the §9.1 fsync dance.

    Parameters
    ----------
    canonical_path:
        Final path for the event file. Must be inside an **existing**
        directory (callers create the date-sharded ``events/{YYYY-MM-DD}``
        folder ahead of time).
    body:
        Raw bytes to write. Must be UTF-8 JSON already serialised by the
        caller — the helper does no re-encoding, so the bytes on disk are
        exactly what :func:`event_sha256` will hash later.

    Returns
    -------
    Path
        The canonical path as a :class:`pathlib.Path`.

    Raises
    ------
    FileNotFoundError
        If the parent directory does not exist.
    FileExistsError
        If the canonical path already exists (events are immutable).
    """
    canonical = Path(canonical_path)
    parent = canonical.parent
    if not parent.is_dir():
        raise FileNotFoundError(f"parent directory does not exist: {parent}")
    if canonical.exists():
        raise FileExistsError(f"event already exists: {canonical}")

    tmp_path = canonical.with_name(canonical.name + _TMP_SUFFIX)

    # 1. open tmp for write (truncate; writable; binary) — we rely on O_CLOEXEC
    # implicitly; this runs in short-lived subprocesses.
    tmp_fd = os.open(
        str(tmp_path),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o644,
    )
    try:
        # 2. write all bytes.
        view = memoryview(body)
        while view:
            written = os.write(tmp_fd, view)
            view = view[written:]
        # 3. fsync the file descriptor — content durable.
        os.fsync(tmp_fd)
    finally:
        # 4. close the tmp fd.
        os.close(tmp_fd)

    # 5. atomic rename into place.
    os.rename(tmp_path, canonical)

    # 6+7. fsync the parent directory so the rename is durable.
    dir_fd = os.open(str(parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)

    return canonical


def event_sha256(body: bytes) -> str:
    """Return the raw-byte SHA-256 hex digest of an event's on-disk bytes.

    Used by ``AppliedEvent.event_sha256`` so that later tampering with the
    event file is detectable at replay time — the hash fixes the exact
    byte content, not the parsed JSON (which would hide whitespace or
    reordering tampering).
    """
    return hashlib.sha256(body).hexdigest()


def read_event(path: str | Path) -> tuple[bytes, dict]:
    """Read an event file and return ``(raw_bytes, parsed_json_dict)``.

    Raises the usual filesystem errors on missing files, and
    :class:`json.JSONDecodeError` on a malformed body.
    """
    data = Path(path).read_bytes()
    parsed = json.loads(data)
    if not isinstance(parsed, dict):
        raise ValueError(f"event body must be a JSON object, got {type(parsed).__name__}")
    return data, parsed
