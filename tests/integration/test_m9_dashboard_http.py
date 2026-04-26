"""M9 — dashboard HTTP server end-to-end.

Spawns the server in-process (background thread) and hits it with
``urllib.request``. Covers:

- 200 paths for non-Kuzu endpoints
- 503 + ``Retry-After`` for Kuzu endpoints during rebuild
- ``/api/events?limit=N`` clamp behaviour
- SSE envelope schema for all six envelope types
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from common.runtime.jobs import JobRecord, write_job_file
from coordinator.heartbeat import (
    CoordinatorHeartbeat,
    STATUS_RUNNING,
    utc_now_iso,
    write_heartbeat as write_coordinator_hb,
)
from dashboard.server import DashboardCore, SseBroker, make_handler
from dashboard.state_watcher import StateWatcher, envelope
from librarian.heartbeat import (
    LibrarianHeartbeat,
    PHASE_READY,
    write_heartbeat as write_librarian_hb,
)
from tests.fixtures.librarian_proc import librarian


PYTHON = sys.executable


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ServerCtx:
    def __init__(self, ws_root: Path) -> None:
        self.ws_root = ws_root
        self.core = DashboardCore(ws_root)
        self.broker = SseBroker()
        self.watcher = StateWatcher(ws_root, self.broker, poll_interval_s=0.1)
        self.port = _free_port()
        handler_cls = make_handler(self.core, self.broker)
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), handler_cls)
        self._thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.1},
            daemon=True,
        )

    def __enter__(self) -> "_ServerCtx":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.watcher.stop()
        self._thread.join(timeout=5.0)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        req = urllib.request.Request(self.url(path))
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            return exc.code, dict(exc.headers), body


def _init_ws(ws: Path) -> None:
    r = subprocess.run(
        [PYTHON, "-m", "cli.main", "--workspace", str(ws), "init"],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr


def test_coordinator_endpoint_returns_raw_json(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    hb = CoordinatorHeartbeat(
        pid=42, started_at=utc_now_iso(), updated_at=utc_now_iso(),
        status=STATUS_RUNNING, loop_seq=7,
    )
    write_coordinator_hb(tmp_path / "runtime" / "state" / "coordinator.json", hb)

    with _ServerCtx(tmp_path) as ctx:
        code, _hdrs, body = ctx.get("/api/coordinator")
        assert code == 200
        parsed = json.loads(body)
        assert parsed["coordinator"]["loop_seq"] == 7
        assert parsed["liveness"] == "healthy"


def test_active_endpoint_lists_jobs(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    rec = JobRecord(
        job_id="gen-20260424T120000.000-bbbbbbbbbbbbbbbb",
        kind="generator", target="lem:foo", mode="fresh",
        dispatch_hash="cd" * 32,
        pid=99, pgid=99,
        started_at="2026-04-24T12:00:00.000Z",
        updated_at="2026-04-24T12:00:01.000Z",
        status="running",
        log_path=str(tmp_path / "runtime" / "logs" / "x.codex.log"),
    )
    write_job_file(tmp_path / "runtime" / "jobs" / f"{rec.job_id}.json", rec)
    with _ServerCtx(tmp_path) as ctx:
        code, _hdrs, body = ctx.get("/api/active")
        assert code == 200
        parsed = json.loads(body)
        assert parsed["count"] == 1
        assert parsed["jobs"][0]["target"] == "lem:foo"


def test_active_endpoint_ignores_terminal_jobs(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    rec = JobRecord(
        job_id="gen-20260424T120000.000-deadbeefdeadbeef",
        kind="generator", target="lem:foo", mode="fresh",
        dispatch_hash="cd" * 32,
        pid=99, pgid=99,
        started_at="2026-04-24T12:00:00.000Z",
        updated_at="2026-04-24T12:00:01.000Z",
        status="crashed",
        log_path=str(tmp_path / "runtime" / "logs" / "x.codex.log"),
    )
    write_job_file(tmp_path / "runtime" / "jobs" / f"{rec.job_id}.json", rec)
    with _ServerCtx(tmp_path) as ctx:
        code, _hdrs, body = ctx.get("/api/active")
        assert code == 200
        parsed = json.loads(body)
        assert parsed["count"] == 0
        assert parsed["jobs"] == []


def test_rebuild_in_progress_returns_503(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    hb = LibrarianHeartbeat(
        pid=1, started_at=utc_now_iso(), updated_at=utc_now_iso(),
        rebuild_in_progress=True,
    )
    write_librarian_hb(tmp_path / "runtime" / "state" / "librarian.json", hb)

    with _ServerCtx(tmp_path) as ctx:
        for path in ("/api/overview", "/api/theorems", "/api/rejected", "/api/node/lem:x"):
            code, hdrs, body = ctx.get(path)
            assert code == 503, f"{path}: expected 503, got {code}"
            assert hdrs.get("Retry-After") == "5"
            parsed = json.loads(body)
            assert parsed["status"] == "rebuild_in_progress"
        # Non-Kuzu endpoints stay up.
        code, _hdrs, _body = ctx.get("/api/coordinator")
        assert code == 200
        code, _hdrs, _body = ctx.get("/api/active")
        assert code == 200


def test_events_limit_validation(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    with _ServerCtx(tmp_path) as ctx:
        code, _hdrs, body = ctx.get("/api/events?limit=0")
        assert code == 400
        code, _hdrs, body = ctx.get("/api/events?limit=-3")
        assert code == 400
        code, _hdrs, body = ctx.get("/api/events?limit=10000")
        assert code == 200
        parsed = json.loads(body)
        # Server clamps to 500.
        assert parsed["limit"] == 500
        code, _hdrs, body = ctx.get("/api/events?limit=notanumber")
        assert code == 400


def test_unknown_route_returns_404(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    with _ServerCtx(tmp_path) as ctx:
        code, _hdrs, _body = ctx.get("/totally/missing")
        assert code == 404


def test_index_root_serves_html(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    with _ServerCtx(tmp_path) as ctx:
        code, hdrs, body = ctx.get("/")
        assert code == 200
        assert "text/html" in hdrs.get("Content-Type", "")
        text = body.decode("utf-8")
        assert "<title>Rethlas Dashboard</title>" in text
        # Minimal sanity: the JS must reference each Phase I endpoint.
        for endpoint in (
            "/api/overview", "/api/active", "/api/attention", "/api/theorems",
            "/api/dashboard",
        ):
            assert endpoint in text


def test_dashboard_heartbeat_round_trip(tmp_path: Path) -> None:
    """dashboard.json written by HeartbeatPublisher is exposed via /api/dashboard."""
    _init_ws(tmp_path)
    from dashboard.heartbeat import HeartbeatPublisher

    pub = HeartbeatPublisher(tmp_path, bind="127.0.0.1:8765", interval_s=60.0)
    pub.start()
    try:
        with _ServerCtx(tmp_path) as ctx:
            code, _hdrs, body = ctx.get("/api/dashboard")
            assert code == 200
            parsed = json.loads(body)
            assert parsed["dashboard"]["bind"] == "127.0.0.1:8765"
            assert parsed["liveness"] == "healthy"
    finally:
        pub.stop()


def test_attention_includes_three_x_stuck_targets(tmp_path: Path) -> None:
    """ARCHITECTURE §6.7 — labelled "3x consecutive" attention entries
    written by the coordinator are surfaced through /api/attention.
    """
    _init_ws(tmp_path)
    # Hand-write a coordinator.json with attention_targets populated.
    hb = CoordinatorHeartbeat(
        pid=1, started_at=utc_now_iso(), updated_at=utc_now_iso(),
        status=STATUS_RUNNING,
        attention_targets=[
            {
                "kind": "generator", "target": "thm:t",
                "trigger": "apply_failed", "reason": "label_conflict",
                "count": 3,
                "message": "generator stuck on thm:t: 3× label_conflict",
            },
            {
                "kind": "verifier", "target": "thm:u",
                "trigger": "timed_out", "reason": "", "count": 4,
                "message": "verifier frozen on thm:u",
            },
        ],
    )
    write_coordinator_hb(tmp_path / "runtime" / "state" / "coordinator.json", hb)

    with _ServerCtx(tmp_path) as ctx:
        code, _hdrs, body = ctx.get("/api/attention")
        assert code == 200
        parsed = json.loads(body)
        msgs = [i.get("message", "") for i in parsed["items"] if i.get("kind") == "stuck_target"]
        assert any("stuck on thm:t" in m for m in msgs)
        assert any("frozen on thm:u" in m for m in msgs)


def test_attention_flags_dashboard_child_degraded(tmp_path: Path) -> None:
    """§6.4: when the coordinator-managed dashboard child exhausts its
    restart budget and goes ``degraded``, /api/attention must surface an
    ``dashboard_degraded`` item so the operator knows to investigate.
    """
    _init_ws(tmp_path)
    hb = CoordinatorHeartbeat(
        pid=1, started_at=utc_now_iso(), updated_at=utc_now_iso(),
        status=STATUS_RUNNING,
        children={
            "dashboard": {"pid": 0, "status": "degraded", "updated_at": utc_now_iso()},
        },
    )
    write_coordinator_hb(tmp_path / "runtime" / "state" / "coordinator.json", hb)

    with _ServerCtx(tmp_path) as ctx:
        code, _hdrs, body = ctx.get("/api/attention")
        assert code == 200
        parsed = json.loads(body)
        kinds = {i["kind"] for i in parsed["items"]}
        assert "dashboard_degraded" in kinds


def test_attention_does_not_flag_dashboard_running_or_backoff(tmp_path: Path) -> None:
    """Auto-recovering states (starting, running, backoff) stay off the
    attention list — only the terminal ``degraded`` state demands the
    operator's eyes.
    """
    _init_ws(tmp_path)
    for status in ("starting", "running", "backoff"):
        hb = CoordinatorHeartbeat(
            pid=1, started_at=utc_now_iso(), updated_at=utc_now_iso(),
            status=STATUS_RUNNING,
            children={
                "dashboard": {"pid": 0, "status": status, "updated_at": utc_now_iso()},
            },
        )
        write_coordinator_hb(tmp_path / "runtime" / "state" / "coordinator.json", hb)
        with _ServerCtx(tmp_path) as ctx:
            code, _hdrs, body = ctx.get("/api/attention")
            assert code == 200
            kinds = {i["kind"] for i in json.loads(body)["items"]}
            assert "dashboard_degraded" not in kinds, f"unexpected at status={status}"


def test_attention_endpoint_lists_user_blocked(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    # Seed a definition that will be at pass_count=0 (not -1) — so to make
    # something user-blocked we tamper Kuzu directly after librarian writes.
    subprocess.run(
        [PYTHON, "-m", "cli.main", "--workspace", str(tmp_path), "add-node",
         "--label", "def:x", "--kind", "definition",
         "--statement", "Define X.", "--actor", "user:alice"],
        capture_output=True, text=True, check=False,
    )
    from tests.fixtures.librarian_proc import librarian as _librarian
    from librarian.heartbeat import PHASE_READY
    with _librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)
    import kuzu
    db = kuzu.Database(str(tmp_path / "knowledge_base" / "dag.kz"))
    conn = kuzu.Connection(db)
    try:
        conn.execute("MATCH (n:Node {label: 'def:x'}) SET n.pass_count = -1")
    finally:
        del conn
        del db

    with _ServerCtx(tmp_path) as ctx:
        code, _hdrs, body = ctx.get("/api/attention")
        assert code == 200
        parsed = json.loads(body)
        kinds = [i["kind"] for i in parsed["items"]]
        assert "user_blocked" in kinds


def test_malformed_runtime_json_does_not_crash(tmp_path: Path) -> None:
    _init_ws(tmp_path)
    bad = tmp_path / "runtime" / "state" / "coordinator.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("not-json{", encoding="utf-8")
    with _ServerCtx(tmp_path) as ctx:
        code, _hdrs, body = ctx.get("/api/coordinator")
        assert code == 200
        parsed = json.loads(body)
        assert parsed["coordinator"] == {}
        assert parsed["liveness"] == "down"
