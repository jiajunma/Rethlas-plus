"""Workspace-local query server for the single-process Kuzu model.

Only librarian opens Kuzu. Other processes query derived state through a
Unix-domain socket carrying one-line JSON request/response messages.
"""

from __future__ import annotations

import json
import os
import socketserver
import threading
from pathlib import Path
from typing import Any, Callable


class QueryServerError(RuntimeError):
    """Query request or transport failure."""


class _ThreadingUnixStreamServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline()
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._write({"ok": False, "reply": "QUERY_ERROR", "error": "malformed_json"})
            return
        try:
            result = self.server.dispatch(payload)  # type: ignore[attr-defined]
        except Exception as exc:
            self._write(
                {
                    "ok": False,
                    "reply": "QUERY_ERROR",
                    "error": type(exc).__name__,
                    "detail": str(exc),
                }
            )
            return
        self._write({"ok": True, "reply": "QUERY_RESULT", "result": result})

    def _write(self, payload: dict[str, Any]) -> None:
        self.wfile.write(
            (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        )
        self.wfile.flush()


class LibrarianQueryServer:
    def __init__(self, socket_path: Path, dispatch: Callable[[dict[str, Any]], Any]) -> None:
        self.socket_path = Path(socket_path)
        self._dispatch = dispatch
        self._server: _ThreadingUnixStreamServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        server = _ThreadingUnixStreamServer(str(self.socket_path), _Handler)
        server.dispatch = self._dispatch  # type: ignore[attr-defined]
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="librarian-query-server",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass


__all__ = ["LibrarianQueryServer", "QueryServerError"]
