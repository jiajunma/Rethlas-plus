"""M6 — prompt composer tests, especially the §6.2 step 2 user-hint
on-fresh-dispatch regression guard.
"""

from __future__ import annotations

from common.runtime.jobs import JobRecord, utc_now_iso
from generator.prompt import compose_prompt


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
