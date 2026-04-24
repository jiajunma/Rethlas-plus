"""`rethlas revise-node` — publish a ``user.node_revised`` event."""

from __future__ import annotations

from cli.publish import publish
from cli.workspace import ensure_initialised, workspace_paths


def run_revise_node(
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
        etype="user.node_revised",
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
