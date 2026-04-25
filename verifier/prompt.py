"""Verifier prompt assembler (PHASE1 M7).

The verifier sees a single dispatched target. The prompt is intentionally
narrow:

1. Task — verify the target's proof
2. Target node — full statement + proof from the job file
3. Dependency hashes — to keep the verdict's verification_hash aligned

Verifier is single-mode in Phase I (``mode = "single"``); there is no
repair vs. fresh distinction, and there is never a user-hint section
to surface (hints flow only into generator).
"""

from __future__ import annotations

from common.runtime.jobs import JobRecord


def compose_prompt(rec: JobRecord) -> str:
    parts = [_task_section(rec), _target_section(rec)]
    if rec.dep_statement_hashes:
        deps = "\n".join(
            f"- {lbl}: statement_hash={h[:12]}..."
            for lbl, h in sorted(rec.dep_statement_hashes.items())
        )
        parts.append(f"## Dependency hashes\n\n{deps}")
    return "\n\n".join(parts).rstrip() + "\n"


def _task_section(rec: JobRecord) -> str:
    target_kind = rec.target_kind or "node"
    body = (
        f"Verify the proof of {rec.target} (kind={target_kind})."
        " Return a JSON verdict (accepted | gap | critical) with verification_hash"
        f"={rec.dispatch_hash}."
    )
    return f"## Task\n\n{body}"


def _target_section(rec: JobRecord) -> str:
    parts = []
    if rec.statement.strip():
        parts.append(f"### Statement\n{rec.statement.strip()}")
    if rec.proof.strip():
        parts.append(f"### Proof\n{rec.proof.strip()}")
    return "## Target\n\n" + "\n\n".join(parts)


__all__ = ["compose_prompt"]
