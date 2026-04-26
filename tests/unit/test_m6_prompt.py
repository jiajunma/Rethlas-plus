"""M6 — prompt composer tests, especially the §6.2 step 3 user-hint
on-fresh-dispatch regression guard and the §6.2 step 2 Memory scope
problem_id assertion.
"""

from __future__ import annotations

from agents.generation.mcp.server import sanitize_problem_id
from common.runtime.jobs import JobRecord, utc_now_iso
from generator.prompt import _problem_id_for, compose_prompt


def _job(**overrides) -> JobRecord:
    base = dict(
        job_id="gen-x",
        kind="generator",
        target="thm:foo",
        mode="fresh",
        dispatch_hash="sha256:abc",
        pid=1,
        pgid=1,
        started_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        status="starting",
        log_path="logs/gen-x.codex.log",
        target_kind="theorem",
        statement="Statement of the theorem.",
        proof="",
        dep_statement_hashes={"def:x": "ab" * 32},
        repair_hint="",
        verification_report="",
        repair_count=0,
        h_rejected="",
    )
    base.update(overrides)
    return JobRecord(**base)


def test_fresh_mode_with_user_hint_emits_initial_guidance() -> None:
    rec = _job(repair_hint="[user @ alice]\nTry induction on n.\n")
    prompt = compose_prompt(rec)
    assert "Initial guidance" in prompt
    assert "induction on n" in prompt
    assert "Repair context" not in prompt


def test_fresh_mode_without_user_hint_omits_initial_guidance() -> None:
    rec = _job(repair_hint="")
    prompt = compose_prompt(rec)
    assert "Initial guidance" not in prompt


def test_repair_mode_emits_repair_context() -> None:
    rec = _job(
        mode="repair",
        repair_hint="[user @ alice]\nfocus on lemma 2\n---\n[verifier]\ngap in step 4\n",
        verification_report="summary: gap in step 4",
        repair_count=1,
        h_rejected="d" * 64,
    )
    prompt = compose_prompt(rec)
    assert "Repair context" in prompt
    assert "verification_report" in prompt
    assert "repair_count = 1" in prompt
    # Initial guidance is fresh-only.
    assert "Initial guidance" not in prompt


def test_target_state_includes_dep_hashes() -> None:
    rec = _job(dep_statement_hashes={"def:x": "ab" * 32, "lem:y": "cd" * 32})
    prompt = compose_prompt(rec)
    assert "Dependency hashes" in prompt
    assert "def:x" in prompt
    assert "lem:y" in prompt


def test_latest_rejection_section_inserted() -> None:
    rec = _job()
    prompt = compose_prompt(rec, latest_rejection="cycle: thm:foo -> lem:bar -> thm:foo")
    assert "Latest batch rejection report" in prompt
    assert "cycle:" in prompt


def test_user_section_extraction_keeps_only_user_blocks() -> None:
    """If repair_hint has both verifier + user sections, fresh-mode prompt
    must show ONLY the user sections under Initial guidance."""
    rec = _job(
        repair_hint=(
            "[verifier]\nthings broken\n---\n[user @ alice]\nuser advice\n"
        )
    )
    prompt = compose_prompt(rec)
    assert "user advice" in prompt
    assert "things broken" not in prompt


def test_memory_scope_section_surfaces_problem_id() -> None:
    """Generator agents must be told which ``problem_id`` to pass to MCP
    memory tools; the colon in the target label is normalised to ``_`` to
    match ``sanitize_problem_id`` in ``agents/generation/mcp/server.py``."""
    rec = _job(target="thm:foo")
    prompt = compose_prompt(rec)
    assert "## Memory scope" in prompt
    assert 'problem_id="thm_foo"' in prompt


def test_problem_id_for_matches_mcp_sanitize_problem_id() -> None:
    """``generator/prompt.py:_problem_id_for`` is a hand-mirrored copy of
    ``agents/generation/mcp/server.py:sanitize_problem_id`` (the worker
    layer keeps the MCP server import optional, see ARCH §6.2). Drift
    between the two would silently shard parent and sub-agent scratch
    memory under the same target. Pin them against the same fixture so
    any divergence fails this test."""
    cases = [
        "thm:foo",
        "lem:block_form_for_x0_plus_u",
        "def:primary_object",
        "ext:vogan_green_2025",
        "  whitespace  in  middle  ",
        "label/with/slashes",
        "label.with.dots",
        "label-with-dashes",
        "Mixed:Case_Label",
        "",
        "___",
        "...",
        "label:with::doubles",
    ]
    for case in cases:
        assert _problem_id_for(case) == sanitize_problem_id(case), (
            f"drift detected for input {case!r}"
        )
