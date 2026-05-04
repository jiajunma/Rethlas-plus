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
4. Search branch guidance — repair mode with ``repair_count >= 2``
5. Latest batch rejection report — runtime decoder/admission summary
6. Repair history summary — current ``repair_count`` (advisory)
7. Target's current state — statement + previous proof attempt

The composer returns a single string suitable for ``codex exec``. Test
helpers can inspect the return value to assert that, e.g., a fresh-mode
job with a user hint surfaces the hint under "Initial guidance".
"""

from __future__ import annotations

import re

from common.runtime.jobs import JobRecord

_PHASE2_REROUTE_HINT_MARKER = "phase2:reroute_around_stuck_background"
_LABEL_RE = re.compile(r"\b(?:axiom|def|ext|lem|prop|thm):[A-Za-z0-9_.-]+")


def compose_prompt(rec: JobRecord, *, latest_rejection: str = "") -> str:
    parts: list[str] = []
    parts.append(_generation_prompt(rec))
    parts.append(_memory_scope(rec))
    parts.append(_knowledge_base_access())
    initial = _initial_guidance(rec)
    if initial:
        parts.append(initial)
    repair = _repair_context(rec)
    if repair:
        parts.append(repair)
    branch = _search_branch_guidance(rec)
    if branch:
        parts.append(branch)
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
    if rec.mode == "repair" and _is_phase2_reroute(rec):
        intro = (
            f"Rewrite the proof route for {rec.target} (kind={target_kind})."
            " A dependency chain is blocked by the Phase II background-expansion "
            "guard; produce a corrected proof that removes that chain."
        )
    elif rec.mode == "repair":
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


def _knowledge_base_access() -> str:
    body = (
        "Verified dependency files live under the workspace path "
        "`knowledge_base/nodes/{prefix}_{slug}.md`. The generator normally "
        "runs with cwd `agents/generation`, so use "
        "`../../knowledge_base/nodes/{prefix}_{slug}.md` from that cwd, or "
        "find the absolute workspace path before reading. For example "
        "`prop:maximal_rank_orbits_and_centralizers` is rendered as "
        "`knowledge_base/nodes/prop_maximal_rank_orbits_and_centralizers.md`. "
        "Only nodes with `pass_count >= 1` are rendered there, so the current "
        "target may be absent from `knowledge_base/nodes`; use the target "
        "statement in this prompt as authoritative for the dispatched target."
    )
    return _section("Knowledge base access", body)


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
    if _is_phase2_reroute(rec):
        advisory = (
            "This is not a local patch request. Rewrite the route around the "
            "blocked background dependency chain, and keep any new helper "
            "narrowly tied to this target."
        )
        return _section(
            "Repair history",
            f"current repair_count = {rec.repair_count}. {advisory}",
        )
    advisory = (
        "Small repair_count suggests trying a local proof tweak; large counts "
        "suggest reconsidering the statement itself."
    )
    return _section(
        "Repair history",
        f"current repair_count = {rec.repair_count}. {advisory}",
    )


def _search_branch_guidance(rec: JobRecord) -> str:
    """Phase II-B guard against single-route repair spirals.

    Once a proof has been rejected more than once, ordinary repair mode
    tends to keep polishing the same route. We still use the existing
    ``generator.batch_committed`` event shape, but the prompt switches
    intent: spawn a materially different branch and keep any new helper
    lemmas narrowly tied to the target.
    """
    if rec.mode != "repair" or rec.repair_count < 2 or _is_phase2_reroute(rec):
        return ""
    slug = _problem_id_for(rec.target)
    body = (
        "This is Phase II search-branch mode. Treat the current route as "
        "likely stuck; do not keep locally patching the previous proof.\n\n"
        "Produce a materially different proof strategy. The batch must still "
        f"include `{rec.target}`. If useful, introduce a sibling candidate "
        f"such as `thm:{slug}_b_next_candidate` and make `{rec.target}` reduce "
        "to that candidate via a short proof.\n\n"
        "Avoid expanding generic background. Do not introduce broad algebraic "
        "geometry or topology helper nodes about varieties, closures, open dense "
        "sets, Noetherianity, or irreducibility unless the helper is a narrow "
        "bridge used immediately by the target. Prefer problem-specific lemmas, "
        "existing cited results, or an explicit obstruction/counterexample over "
        "basic-definition expansion.\n\n"
        "Record in the emitted proof text which previous strategy is being "
        "avoided and why the new branch is different."
    )
    return _section("Search branch guidance", body)


def _is_phase2_reroute(rec: JobRecord) -> bool:
    return _PHASE2_REROUTE_HINT_MARKER in rec.repair_hint.lower()


def _target_state(rec: JobRecord) -> str:
    parts: list[str] = []
    if rec.statement.strip():
        parts.append(f"### Statement\n{rec.statement.strip()}")
    else:
        parts.append("### Statement\n(no statement supplied yet)")
    is_reroute = _is_phase2_reroute(rec)
    blocked_labels = _phase2_blocked_labels(rec) if is_reroute else set()
    if rec.proof.strip():
        if is_reroute:
            parts.append(
                "### Previous proof attempt intentionally omitted\n"
                "The previous route is intentionally not included because this "
                "Phase II reroute must not continue the blocked background chain. "
                "Rebuild the target proof from the statement, allowed existing "
                "nodes, and narrow problem-specific arguments."
            )
        else:
            parts.append(f"### Previous proof attempt\n{rec.proof.strip()}")
    if rec.dep_statement_hashes:
        dep_items = sorted(rec.dep_statement_hashes.items())
        dep_title = "Dependency hashes"
        if blocked_labels:
            dep_items = [
                (lbl, h)
                for lbl, h in dep_items
                if lbl not in blocked_labels
            ]
            dep_title = "Allowed dependency hashes"
        if not dep_items:
            return _section("Target current state", "\n\n".join(parts))
        deps = "\n".join(
            f"- {lbl}: statement_hash={h[:12]}..."
            for lbl, h in dep_items
        )
        parts.append(f"### {dep_title}\n{deps}")
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


def _phase2_blocked_labels(rec: JobRecord) -> set[str]:
    if not _is_phase2_reroute(rec):
        return set()
    return {match.group(0).rstrip(".,;:)]}") for match in _LABEL_RE.finditer(rec.repair_hint)}


__all__ = ["compose_prompt"]
