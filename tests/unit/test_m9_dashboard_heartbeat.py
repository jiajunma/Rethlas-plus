"""M9 — dashboard.json heartbeat schema + atomic writer."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from unittest.mock import patch

from dashboard.heartbeat import (
    DASHBOARD_JSON_SCHEMA,
    DashboardHeartbeat,
    HeartbeatPublisher,
    read_heartbeat,
    write_heartbeat,
)


def _hb(**overrides) -> DashboardHeartbeat:
    base = dict(
        pid=42,
        started_at="2026-04-25T00:00:00.000Z",
        updated_at="2026-04-25T00:00:01.000Z",
        bind="127.0.0.1:8765",
    )
    base.update(overrides)
    return DashboardHeartbeat(**base)


def test_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "dashboard.json"
    write_heartbeat(p, _hb())
    parsed = read_heartbeat(p)
    assert parsed is not None
    assert parsed["schema"] == DASHBOARD_JSON_SCHEMA
    assert parsed["bind"] == "127.0.0.1:8765"
    assert parsed["pid"] == 42


def test_atomic_overwrite_no_tmp_left_behind(tmp_path: Path) -> None:
    p = tmp_path / "dashboard.json"
    write_heartbeat(p, _hb(pid=1))
    write_heartbeat(p, _hb(pid=2))
    assert not (tmp_path / "dashboard.json.tmp").exists()
    parsed = read_heartbeat(p)
    assert parsed["pid"] == 2


def test_z_suffix_timestamps_in_payload(tmp_path: Path) -> None:
    p = tmp_path / "dashboard.json"
    write_heartbeat(p, _hb())
    body = json.loads(p.read_text(encoding="utf-8"))
    iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    assert iso_re.match(body["started_at"]), body["started_at"]
    assert iso_re.match(body["updated_at"]), body["updated_at"]


def test_read_returns_none_on_missing(tmp_path: Path) -> None:
    assert read_heartbeat(tmp_path / "nope.json") is None


def test_read_returns_none_on_malformed(tmp_path: Path) -> None:
    p = tmp_path / "dashboard.json"
    p.write_text("not-json{", encoding="utf-8")
    assert read_heartbeat(p) is None


def test_publisher_loop_survives_transient_write_failure(tmp_path: Path) -> None:
    """A transient OSError from _write must not kill the heartbeat thread.

    Regression: prior to the fix, an OSError (e.g. EBUSY race in
    os.replace, brief permission flip) silently terminated the daemon
    thread. The dashboard process kept serving HTTP, but the heartbeat
    froze and the coordinator supervisor tore it down 5 minutes later.
    """
    pub = HeartbeatPublisher(tmp_path, bind="127.0.0.1:0", interval_s=0.05)
    call_count = {"n": 0}
    real_write = pub._write

    def flaky(status: str) -> None:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated EBUSY")
        real_write(status)

    pub._write = flaky  # type: ignore[assignment]
    pub.start()
    try:
        # Wait long enough for at least 4 ticks (one of which raises).
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and call_count["n"] < 4:
            time.sleep(0.05)
    finally:
        pub.stop()
    assert call_count["n"] >= 4, f"thread died after error; only {call_count['n']} writes"
    # And the file from the first successful write must exist.
    parsed = read_heartbeat(tmp_path / "runtime" / "state" / "dashboard.json")
    assert parsed is not None
    assert parsed["status"] in ("running", "stopping")
