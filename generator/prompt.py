"""Generator prompt assembler (ARCHITECTURE §6.2 step "Prompt composition").

Inputs come from ``runtime/jobs/{job_id}.json`` (coordinator-populated)
and from the local ``nodes/*.md`` view. The wrapper does not consult
Kuzu — every fact below is already on disk by the time ``role.py``
calls into this module.

Sections (always in this fixed order):

1. Generation prompt — task description for the target label
2. Initial guidance — fresh mode + non-empty user-section in
   ``repair_hint`` (without this the user-supplied hint would be lost
   on the first attempt; §6.2 step 2)
3. Repair context — repair mode only: ``verification_report`` +
   full ``repair_hint``
4. Latest batch rejection report — runtime decoder/admission summary
5. Repair history summary — current ``repair_count`` (advisory)
6. Target's current state — statement + previous proof attempt

The composer returns a single string suitable for ``codex exec``. Test
helpers can inspect the return value to assert that, e.g., a fresh-mode
job with a user hint surfaces the hint under "Initial guidance".
"""

from __future__ import annotations

from common.runtime.jobs import JobRecord


def compose_prompt(rec: JobRecord, *, latest_rejection: str = "") -> str:
    parts: list[str] = []
    parts.append(_generation_prompt(rec))
    parts.append(_memory_scope(rec))
    initial = _initial_guidance(rec)
    if initial:
        parts.append(initial)
    repair = _repair_context(rec)
    if repair:
        parts.append(repair)
    if latest_rejection:
        parts.append(_section("Latest batch rejection report", latest_rejection.strip()))
    history = _repair_history(rec)
    if history:
        parts.append(history)
    parts.append(_target_state(rec))
    return "\n\n".join(parts).rstrip() + "\n"


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body.rstrip()}"


def _generation_prompt(rec: JobRecord) -> str:
    target_kind = rec.target_kind or "node"
    if rec.mode == "repair":
        intro = (
            f"Repair the proof of {rec.target} (kind={target_kind})."
            f" The previous attempt was rejected; produce a corrected proof."
        )
    else:
        intro = (
            f"Generate a complete proof of {rec.target} (kind={target_kind})."
            f" Introduce auxiliary lemmas under brand-new labels as needed."
        )
    return _section("Task", intro)


def _problem_id_for(target: str) -> str:
    """Deterministic ``problem_id`` derived from the dispatched target label.

    Mirrors ``agents/generation/mcp/server.py:sanitize_problem_id``: any
    character outside ``[A-Za-z0-9._-]`` becomes ``_``, runs collapse,
    leading/trailing ``._`` are stripped. So ``lem:foo`` → ``lem_foo``.
    Two dispatches against the same target share a ``problem_id`` and
    therefore share scratch memory; different targets stay isolated.
    """
    import re
    cleaned = re.sub(r"\s+", "_", target.strip())
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned or "problem"


def _memory_scope(rec: JobRecord) -> str:
    """Tell the agent which ``problem_id`` to pass to MCP memory tools.

    Without this section the agent has nothing to pass and must invent
    a value, which sharded memory across skill calls in the past.
    """
    pid = _problem_id_for(rec.target)
    body = (
        f"Use `problem_id=\"{pid}\"` for every "
        "`memory_search`, `memory_append`, `memory_init`, and "
        "`branch_update` MCP call in this run. The same value is reused "
        "across repair rounds for the same target."
    )
    return _section("Memory scope", body)


def _initial_guidance(rec: JobRecord) -> str:
    """Emit the user's hint section verbatim on fresh dispatch.

    Without this step, the user-contributed sections of ``repair_hint``
    would be lost when the first batch bumps ``verification_hash`` and
    §5.4 clears ``repair_hint`` (regression PHASE1 M6 explicitly tests).
    """
    if rec.mode != "fresh":
        return ""
    user_text = _user_sections_only(rec.repair_hint)
    if not user_text.strip():
        return ""
    return _section("Initial guidance", user_text)


def _repair_context(rec: JobRecord) -> str:
    if rec.mode != "repair":
        return ""
    body_parts: list[str] = []
    if rec.verification_report.strip():
        body_parts.append(f"### verification_report\n{rec.verification_report.strip()}")
    if rec.repair_hint.strip():
        body_parts.append(f"### repair_hint\n{rec.repair_hint.strip()}")
    if not body_parts:
        return ""
    return _section("Repair context", "\n\n".join(body_parts))


def _repair_history(rec: JobRecord) -> str:
    if rec.repair_count <= 0:
        return ""
    advisory = (
        "Small repair_count suggests trying a local proof tweak; large counts "
        "suggest reconsidering the statement itself."
    )
    return _section(
        "Repair history",
        f"current repair_count = {rec.repair_count}. {advisory}",
    )


def _target_state(rec: JobRecord) -> str:
    parts: list[str] = []
    if rec.statement.strip():
        parts.append(f"### Statement\n{rec.statement.strip()}")
    else:
        parts.append("### Statement\n(no statement supplied yet)")
    if rec.proof.strip():
        parts.append(f"### Previous proof attempt\n{rec.proof.strip()}")
    if rec.dep_statement_hashes:
        deps = "\n".join(
            f"- {lbl}: statement_hash={h[:12]}..."
            for lbl, h in sorted(rec.dep_statement_hashes.items())
        )
        parts.append(f"### Dependency hashes\n{deps}")
    return _section("Target current state", "\n\n".join(parts))


def _user_sections_only(repair_hint: str) -> str:
    """Extract the user-authored sections of ``repair_hint``.

    The hint follows the §5.2 structure (§5.4 L1246): a verifier section
    followed by zero-or-more ``[user @ ts]`` user sections separated by
    ``---`` lines. We keep only the user sections and emit them verbatim.
    """
    if not repair_hint:
        return ""
    sections = [s.strip() for s in repair_hint.split("\n---\n")]
    user_sections = [s for s in sections if s.lstrip().startswith("[user @ ")]
    return "\n\n---\n".join(user_sections)


__all__ = ["compose_prompt"]
