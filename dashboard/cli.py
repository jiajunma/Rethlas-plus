"""``rethlas dashboard`` standalone CLI entry (ARCHITECTURE §6.7.1).

Behaviour summary:

- If ``runtime/locks/supervise.lock`` is held, prints the documented
  message and exits ``0`` (informational, not an error). The supervised
  dashboard already serves on the configured bind.
- Otherwise loads ``rethlas.toml`` for ``[dashboard] bind``, allows
  ``--bind HOST:PORT`` to override, then starts the HTTP server +
  state watcher.

The CLI is a thin wrapper; the testable logic lives in
:class:`dashboard.server.DashboardCore`.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

from cli.workspace import ensure_initialised, workspace_paths
from common.config.loader import load_config


_BIND_HELD_MSG_TEMPLATE = (
    "supervise is running on this workspace; it has already started "
    "a dashboard at {bind}. Open that URL instead.\n"
)


def _supervise_lock_held(lock_path: Path) -> bool:
    """Return True if another process holds an exclusive flock on ``lock_path``."""
    if not lock_path.is_file():
        return False
    try:
        fd = os.open(str(lock_path), os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return True
            raise
        # We got the lock — release it and return False.
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _parse_bind(s: str) -> tuple[str, int]:
    if ":" not in s:
        raise SystemExit(f"--bind must be HOST:PORT, got {s!r}")
    host, _, port_s = s.rpartition(":")
    try:
        port = int(port_s)
    except ValueError:
        raise SystemExit(f"--bind port not numeric: {s!r}")
    if not (1 <= port <= 65535):
        raise SystemExit(f"--bind port {port} out of 1..65535")
    if not host:
        raise SystemExit(f"--bind host empty: {s!r}")
    return host, port


def _setup_logging(ws_root: Path) -> None:
    logs = ws_root / "runtime" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_file = logs / "dashboard.log"
    handler = logging.FileHandler(str(log_file), encoding="utf-8")
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    handler.formatter.converter = _utc_converter  # type: ignore[attr-defined]
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Avoid duplicating handlers on repeated invocations in same process.
    if not any(getattr(h, "baseFilename", None) == handler.baseFilename for h in root.handlers):
        root.addHandler(handler)


def _utc_converter(*args: Any) -> Any:
    import time

    return time.gmtime(*args)


def run_dashboard(workspace: str | None, args: argparse.Namespace) -> int:
    ws = workspace_paths(workspace)
    ensure_initialised(ws)
    _setup_logging(ws.root)

    cfg = load_config(ws.rethlas_toml)
    bind_str: str = args.bind or cfg.dashboard.bind

    # When coordinator is the parent process (PHASE1 M9 child management),
    # it holds supervise.lock itself — skipping the "lock held -> already
    # running" early-exit so the supervisor doesn't enter a restart loop
    # spawning dashboards that immediately exit.
    if not os.environ.get("RETHLAS_COORDINATOR_DASHBOARD_CHILD") and \
            _supervise_lock_held(ws.supervise_lock):
        sys.stdout.write(_BIND_HELD_MSG_TEMPLATE.format(bind=cfg.dashboard.bind))
        return 0

    host, port = _parse_bind(bind_str)
    if host not in ("127.0.0.1", "localhost", "::1"):
        logging.getLogger("rethlas.dashboard").warning(
            "dashboard binding to non-loopback %s — Phase I has no auth", host
        )

    from dashboard.heartbeat import HeartbeatPublisher
    from dashboard.server import DashboardCore, SseBroker, serve_forever
    from dashboard.state_watcher import StateWatcher

    core = DashboardCore(
        ws.root, desired_pass_count=cfg.scheduling.desired_pass_count
    )
    broker = SseBroker()
    watcher = StateWatcher(ws.root, broker)
    heartbeat = HeartbeatPublisher(ws.root, bind=bind_str)
    watcher.start()
    heartbeat.start()

    def _on_sigterm(signum: int, frame: Any) -> None:
        heartbeat.stop()
        watcher.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)

    try:
        serve_forever(core, host=host, port=port, broker=broker)
    finally:
        heartbeat.stop()
        watcher.stop()
    return 0


__all__ = ["run_dashboard"]
