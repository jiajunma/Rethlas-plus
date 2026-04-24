"""`rethlas add-node` — publish a ``user.node_added`` event."""

from __future__ import annotations

from cli.publish import publish
from cli.workspace import ensure_initialised, workspace_paths


def run_add_node(
    *,
    workspace: str | None,
    label: str,
    kind: str,
    statement: str,
    proof: str,
    remark: str,
    source_note: str,
    actor: str,
) -> int:
    ws = workspace_paths(workspace)
    ensure_initialised(ws)
    outcome = publish(
        ws,
        etype="user.node_added",
        actor=actor,
        target=label,
        payload={
            "kind": kind,
            "statement": statement,
            "proof": proof,
            "remark": remark,
            "source_note": source_note,
        },
    )
    return outcome.exit_code
