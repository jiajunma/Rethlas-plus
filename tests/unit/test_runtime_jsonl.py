"""Runtime JSONL append contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from common.runtime.jsonl import (
    DETAIL_CAP_BYTES,
    LINE_CAP_BYTES,
    TRUNCATION_SUFFIX,
    append_jsonl,
)


def test_append_jsonl_truncates_detail_and_line_length(tmp_path: Path) -> None:
    path = tmp_path / "rejected_writes.jsonl"
    entry = {
        "schema": "rethlas-rejection-v1",
        "ts": "2026-04-26T00:00:00.000Z",
        "actor": "generator:test",
        "target": "thm:t",
        "reason": "malformed_node",
        "detail": "x" * 10_000,
    }
    append_jsonl(path, entry)
    raw = path.read_bytes()
    assert len(raw) <= LINE_CAP_BYTES
    body = json.loads(raw.decode("utf-8"))
    assert len(body["detail"].encode("utf-8")) <= DETAIL_CAP_BYTES
    assert body["detail"].endswith(TRUNCATION_SUFFIX)


def test_append_jsonl_uses_single_write_and_append_flag(tmp_path: Path) -> None:
    path = tmp_path / "rejected_writes.jsonl"
    entry = {
        "schema": "rethlas-rejection-v1",
        "ts": "2026-04-26T00:00:00.000Z",
        "actor": "generator:test",
        "target": "thm:t",
        "reason": "malformed_node",
        "detail": "short",
    }

    real_open = os.open
    real_write = os.write
    open_flags: list[int] = []
    writes: list[bytes] = []

    def tracked_open(path_s: str, flags: int, mode: int = 0o777) -> int:
        open_flags.append(flags)
        return real_open(path_s, flags, mode)

    def tracked_write(fd: int, data: bytes) -> int:
        writes.append(data)
        return real_write(fd, data)

    with patch("common.runtime.jsonl.os.open", side_effect=tracked_open), patch(
        "common.runtime.jsonl.os.write", side_effect=tracked_write
    ):
        append_jsonl(path, entry)

    assert len(writes) == 1
    assert open_flags
    assert open_flags[0] & os.O_APPEND
