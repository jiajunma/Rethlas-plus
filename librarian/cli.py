"""``rethlas librarian`` — internal subcommand to spawn the librarian daemon.

Coordinator launches one librarian per workspace via this entry point as
a subprocess. The protocol is the JSON-line stdio channel from
:mod:`librarian.ipc`. Tests can also drive the daemon by spawning the
same subcommand and writing/reading from its stdin/stdout.
"""

from __future__ import annotations

import sys

from cli.workspace import ensure_initialised, workspace_paths
from librarian.daemon import LibrarianDaemon


def run_librarian(workspace: str | None) -> int:
    ws = workspace_paths(workspace)
    ensure_initialised(ws)
    daemon = LibrarianDaemon(
        ws=ws,
        rx=sys.stdin.buffer,
        tx=sys.stdout.buffer,
    )
    return daemon.run()


__all__ = ["run_librarian"]
