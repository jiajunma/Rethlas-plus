"""`rethlas init` — create the §2.2 workspace skeleton + default config."""

from __future__ import annotations

import sys

from cli.workspace import (
    ANNOTATED_RETHLAS_TOML,
    WorkspacePaths,
    create_workspace_layout,
    workspace_paths,
)
from common.runtime.agents_install import materialize_agents


def run_init(workspace: str | None, *, force: bool = False) -> int:
    ws = workspace_paths(workspace)

    if ws.rethlas_toml.exists() and not force:
        sys.stderr.write(
            f"rethlas.toml already exists at {ws.rethlas_toml}. "
            "Re-run with --force to overwrite the config "
            "(events/ is never touched).\n"
        )
        return 1

    if ws.events.exists() and any(ws.events.iterdir()) and ws.rethlas_toml.exists() and not force:
        # events/ is authoritative truth — never overwrite.
        sys.stderr.write(
            f"events/ already exists and is non-empty at {ws.events}; "
            "cowardly refusing to re-initialize.\n"
        )
        return 1

    create_workspace_layout(ws, annotated_template=False)
    # Under --force we always rewrite the config; otherwise only write
    # when absent.
    if force or not ws.rethlas_toml.exists():
        ws.rethlas_toml.write_text(ANNOTATED_RETHLAS_TOML, encoding="utf-8")

    # H22: materialize the Phase I agent tree (AGENTS.md, .codex/, .agents/
    # skills, mcp/ server) into the workspace so codex worker invocations
    # find their config from the workspace and never have to read above it.
    try:
        materialize_agents(workspace_root=ws.root, overwrite=force)
    except FileNotFoundError as exc:
        sys.stderr.write(
            f"warning: could not materialize agents into workspace: {exc}\n"
            "the workspace is initialized but generator/verifier worker dispatches will fail\n"
        )

    sys.stdout.write(f"initialized workspace at {ws.root}\n")
    return 0
