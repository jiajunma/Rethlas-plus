"""`rethlas attach-hint` — publish a ``user.hint_attached`` event."""

from __future__ import annotations

from cli.publish import publish
from cli.workspace import ensure_initialised, workspace_paths


def run_attach_hint(
    *,
    workspace: str | None,
    target: str,
    hint: str,
    actor: str,
) -> int:
    ws = workspace_paths(workspace)
    ensure_initialised(ws)
    outcome = publish(
        ws,
        etype="user.hint_attached",
        actor=actor,
        target=target,
        payload={"hint": hint, "remark": ""},
    )
    return outcome.exit_code
